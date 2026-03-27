# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import json, pathlib, random, re, argparse
from typing import Any, Literal
from pydantic import BaseModel, Field
from utils.battle import BattleAPI, BattleResult, Effect, Item, Magic, Monster, TokenNotFoundError


class EWMAData(BaseModel):
    weighted_sum: float = Field(0, description="所有历史数据的加权和")
    total_weight: float = Field(0, ge=0, description="所有历史数据的权重的和")
    multiplier: float = Field(1, gt=0, exclude=True, description="数据权重衰减因子，默认为 1（表示无衰减，即算术平均）")

    def add_observation(self, new_value: float):
        # weighted_sum = data[-1] + multiplier * data[-2] + multiplier ** 2 * data[-3] + ... = data[-1] + multiplier * (data[-2] + multiplier * (data[-3] + ...))
        # total_weight = 1 + multiplier + multiplier ** 2 + multiplier ** 3 = 1 + multiplier * (1 + multiplier * (1 + multiplier * (1 + ...)))
        self.weighted_sum = self.weighted_sum * self.multiplier + new_value
        self.total_weight = self.total_weight * self.multiplier + 1

    def get_current_average(self) -> float:
        return self.weighted_sum / self.total_weight


class SkillData(BaseModel):
    attack_range: int = Field(0, ge=0, description="技能的攻击范围（能攻击除目标以外，能攻击目标上下各多少只怪兽）")
    damage: EWMAData = Field(default_factory=EWMAData, description="技能的基础伤害")


class MonsterData(BaseModel):
    damage_multiplier: dict[str, EWMAData] = Field(default_factory=dict, description="特定技能攻击该怪兽，造成的相对于技能基础伤害的倍数。比如技能基础伤害 1000，相对倍数 1.1，那么计算该技能对怪兽造成的伤害就是 1100")


class BattleBotConfig(BaseModel):
    elite_health_threshold: int = Field(gt=0, description="精英生物判定阈值，高于此值的怪兽被视为精英，并以特殊战术应对")
    critical_health_line: int = Field(gt=0, description="濒死判定线，低于此值将不计代价回血")
    normal_healing_line: int = Field(gt=0, description="治疗触发阈值，低于此值尝试恢复生命")
    mana_supply_line: int = Field(gt=0, description="魔力补给触发阈值，低于此值尝试恢复魔力")
    pre_battle_health_reserve: int = Field(gt=0, description="下一场战斗开始时的理想血量储备，用于应对连续无休息的战斗")
    pre_battle_mana_reserve: int = Field(gt=0, description="下一场战斗开始时的理想蓝量储备，用于应对连续无休息的战斗")
    draught_buff_round_threshold: int = Field(gt=0, description="持续回复Buff触发回合阈值。当战斗总回合数超过此值时，使用 Health Draught 和 Mana Draught 获取持续的血量、蓝量回复效果")
    spark_buff: bool = Field(description="是否尝试保持 Spark of Life 的 Buff")
    ewma_multiplier: float = Field(0.99, gt=0, description="EMWA 更新数据的衰减因子，用于更新技能基础伤害，以及怪兽受到技能的伤害")


class AuthenticationConfig(BaseModel):
    ipb_member_id: str
    ipb_pass_hash: str
    user_agent: str | None = None


class BaseAction(BaseModel):
    action_type: Literal["item", "magic", "attack", "defend"]
    logging_skill_id: str | None = Field(None, description="如果存在，则根据指定的技能 ID 记录该动作造成的伤害")


class ActionItem(BaseAction):
    action_type: Literal["item"] = Field("item")
    item: Item


class ActionMagic(BaseAction):
    action_type: Literal["magic"] = Field("magic")
    magic: Magic
    target: int


class ActionAttack(BaseAction):
    action_type: Literal["attack"] = Field("attack")
    target: int


class ActionDefend(BaseAction):
    action_type: Literal["defend"] = Field("defend")


class BattleBot:
    def __init__(self, api: BattleAPI, config: BattleBotConfig, skill_data: dict[str, SkillData], monster_data: dict[str, MonsterData]):
        self.api = api
        self.config = config
        self.skill_data = skill_data
        self.monster_data = monster_data
        self.__init_ewma_multiplier()
        self.__init_flags()

    def __init_ewma_multiplier(self):
        for data in [
            *[x.damage for x in self.skill_data.values()],
            *[y for x in self.monster_data.values() for y in x.damage_multiplier.values()]
        ]:
            data.multiplier = self.config.ewma_multiplier

    def __init_flags(self):
        # 检测是否需要结束前回血、是否需要叠回血和回蓝 Buff
        self.heal_before_end_flag = self.draught_buff = False
        if (result := re.search(r"Round (\d+) / (\d+)", self.api.logs[0][0])) is not None:
            current_rounds, total_rounds = [int(x) for x in result.groups()]
            if current_rounds < total_rounds:
                self.heal_before_end_flag = True
            if total_rounds > self.config.draught_buff_round_threshold:
                self.draught_buff = True
        if any(monster.health > self.config.elite_health_threshold * 2 for monster in self.api.monsters):
            self.draught_buff = True

    @staticmethod
    def __has_effect(effect_name: str, effects: list[Effect]) -> bool:
        return any(effect.name == effect_name for effect in effects)

    def __try_to_use(self, category: Literal["magic", "item"], name: str, **kwargs) -> ActionItem | ActionMagic | None:
        if thing := next((thing for thing in getattr(self.api, f"get_player_{category}s")() if thing.name == name and thing.available), None):
            return globals()[f"Action{category.capitalize()}"](**({category: thing} | kwargs))

    def __heal(self, magic_only: bool) -> BaseAction | None:
        # 优先使用魔法治疗
        if action_magic := self.__try_to_use("magic", "Cure", target=BattleAPI.PLAYER_ID):
            return action_magic

        # 尝试使用消耗品
        if magic_only:
            return
        for item_name in ["Health Gem", "Health Potion"]:
            if action_consumable := self.__try_to_use("item", item_name):
                return action_consumable

    def __control_monster(self, monster_idx: int, with_sleep: bool) -> ActionMagic | None:
        control_magic_and_effect = [("Silence", "Silenced"), ("Weaken", "Weakened"), ("Blind", "Blinded")]
        if with_sleep:
            control_magic_and_effect.insert(0, ("Sleep", "Asleep"))

        # 检测是否需要使用控制效果。如果已经拥有最佳效果，那就不需要使用了（一个效果控制整个怪兽，不需要叠其他控制 Debuff 了）；否则，给怪兽叠一个 Debuff（强度不够，得和其他控制 Debuff 配合着用）
        best_effect = control_magic_and_effect[0][1]
        monster = self.api.monsters[monster_idx]
        if BattleBot.__has_effect(best_effect, monster.effects):
            return

        # 按顺序施展控制效果
        for magic_name, effect_name in control_magic_and_effect:
            # 怪兽已经有这个 Debuff 就不用叠了，叠下一个
            if BattleBot.__has_effect(effect_name, monster.effects):
                continue
            # 给怪兽叠 Debuff
            if action := self.__try_to_use("magic", magic_name, target=BattleAPI.MONSTER_START_ID + monster_idx):
                return action

    def __predict_damage(self, skill_id: str, monster: Monster) -> int:
        if skill_id not in self.skill_data:
            return 19890604
        skill_base_damage = self.skill_data[skill_id].damage.get_current_average()

        multiplier = 1
        if (monster_data := self.monster_data.get(str(monster.monster_id))) is not None:
            if skill_id in monster_data.damage_multiplier:
                multiplier = monster_data.damage_multiplier[skill_id].get_current_average()
        return skill_base_damage * multiplier

    def __update_after_action(self, action: ActionMagic | ActionAttack, textlog: list[str]):
        # 分析伤害
        damage_info = []
        for log in textlog:
            if (res := BattleAPI.parse_damage(log)) is not None:
                if (monster_info := next(((monster.monster_id, idx) for idx, monster in enumerate(self.api.monsters) if monster.name == res.monster_name), None)) is not None:
                    monster_id, monster_index = monster_info
                    damage_info.append((monster_index, monster_id, int(res.damage)))

        # 没有造成任何伤害时跳过数据库更新
        if not damage_info:
            return textlog
        monster_indices, _, damage_list = zip(*damage_info)

        # 更新技能数据（伤害和范围）
        skill_data = self.skill_data.setdefault(action.logging_skill_id, SkillData(damage=EWMAData(multiplier=self.config.ewma_multiplier)))
        skill_data.damage.add_observation(sum(damage_list) / len(damage_list))
        target_monster_idx = action.target - BattleAPI.MONSTER_START_ID
        skill_data.attack_range = max(max(monster_indices) - target_monster_idx, target_monster_idx - min(monster_indices), skill_data.attack_range)

        # 更新怪兽数据，用技能基础伤害的倍数表示
        skill_base_damage = skill_data.damage.get_current_average()
        for _, monster_id, damage in damage_info:
            self.monster_data.setdefault(monster_id, MonsterData()).damage_multiplier.setdefault(action.logging_skill_id, EWMAData(multiplier=self.config.ewma_multiplier)).add_observation(damage / skill_base_damage)

        return textlog

    @staticmethod
    def display_situation_after_action(api: BattleAPI, textlog: list[str]):
        def format_effects_str(effects: list[Effect]) -> str:
            effect_strings = []
            for effect in effects:
                effect_str = f"{effect.name}({effect.remaining_turns} Turn"
                if effect.remaining_turns > 1:
                    effect_str += "s"
                effect_str += ")"
                effect_strings.append(effect_str)
            return ", ".join(effect_strings)

        # 只在有日志的时候打印战斗记录
        if not textlog:
            return
        print("\n".join(textlog))

        # 如果游戏尚未结束，打印玩家和场上怪兽信息
        if all(monster.health == 0 for monster in api.monsters):
            print("- - " * 20)
            return

        # 获取玩家血量（当持有 Spark of Life Buff 时，游戏不会显示血量）
        player_health = api.get_player_health()
        if BattleBot.__has_effect("Spark of Life", api.get_player_effects()):
            player_health = "Unknown"

        # 打印现场情况
        print("+ - " * 10)
        print(f"Player: Health={player_health}; Mana={api.get_player_mana()}; Spirit={api.get_player_spirit()}; Effects={format_effects_str(api.get_player_effects())}")
        print("\n".join(f"Monster {chr(ord('A') + monster_idx)}({monster.name}): Health={monster.health}; Mana={monster.mana / 1.2:.0f}%; Spirit={monster.spirit / 1.2:.0f}%; Effects={format_effects_str(monster.effects)}" for monster_idx, monster in enumerate(api.monsters) if monster.health))
        print("# = " * 16)

    def execute_action(self, action: BaseAction) -> list[str]:
        # 执行动作，获得战斗记录
        if action.action_type == "attack":
            textlog = self.api.do_attack(action.target)
        elif action.action_type == "defend":
            textlog = self.api.do_defend()
        elif action.action_type == "item":
            textlog = self.api.use_item(action.item)
        elif action.action_type == "magic":
            textlog = self.api.use_magic(action.magic, action.target)

        # 记录伤害
        if action.logging_skill_id:
            self.__update_after_action(action, textlog)

        return textlog

    def decide(self) -> list[tuple[BaseAction, tuple | int]]:
        # 敌人血厚时，要有持续回血、回蓝的 Buff
        if self.draught_buff:
            for item_name, effect_name in [("Health Draught", "Regeneration"), ("Mana Draught", "Replenishment")]:
                if not BattleBot.__has_effect(effect_name, self.api.get_player_effects()) and (action := self.__try_to_use("item", item_name)):
                    return [(action, 0)]

        # 药水回蓝
        if self.api.get_player_mana() < self.config.mana_supply_line:
            for item_name in ["Mana Gem", "Mana Potion"]:
                if action := self.__try_to_use("item", item_name):
                    return [(action, 0)]

        # 如果没有保命 Buff，就分情况进行急救回血和普通回血
        if not BattleBot.__has_effect("Spark of Life", self.api.get_player_effects()):
            if self.api.get_player_health() < self.config.critical_health_line:
                if action := self.__heal(magic_only=False):
                    return [(action, 0)]
            if self.api.get_player_health() < self.config.normal_healing_line:
                if action := self.__heal(magic_only=True):
                    return [(action, 0)]

        # 如果 Spirit 足够的话（Spark of Life 需要 Spirit 发挥作用），上保命 Buff。注意，Spark of Life 会隐藏实际生命值，导致 API 无法获取实际生命，显示只有 1 点生命值，这不是实际情况
        if self.config.spark_buff and self.api.get_player_spirit() > 1 and not BattleBot.__has_effect("Spark of Life", self.api.get_player_effects()):
            if action := self.__try_to_use("magic", "Spark of Life", target=BattleAPI.PLAYER_ID):
                return [(action, 0)]

        # 丢弃无用物品
        for item_name in ["Mystic Gem", "Spirit Gem"]:
            if action := self.__try_to_use("item", item_name):
                return [(action, 0)]

        # 战斗结束前策略
        monster_health = [(idx, monster.health) for idx, monster in enumerate(self.api.monsters) if monster.health > 0]
        if len(monster_health) == 1:
            monster_idx, health = monster_health[0]

            # 如果启用了结束前回复的模式，那么迷晕敌人，等待回复
            if self.heal_before_end_flag and ((self.api.get_player_health() < self.config.pre_battle_health_reserve and not self.__has_effect("Spark of Life", self.api.get_player_effects())) or self.api.get_player_mana() < self.config.pre_battle_mana_reserve):
                # 给敌人打麻药
                if action := self.__control_monster(monster_idx, with_sleep=True):
                    return [(action, 0)]

                # 尝试回血到期望值
                if (self.api.get_player_health() < self.config.pre_battle_health_reserve) and (action := self.__heal(magic_only=False)):
                    return [(action, 0)]

                # 尝试回蓝到期望值
                if self.api.get_player_mana() < self.config.pre_battle_mana_reserve:
                    for item_name in ["Mana Gem", "Mana Potion"]:
                        if action := self.__try_to_use("item", item_name):
                            return [(action, 0)]
                    return [(ActionDefend(), 0)]

            # 如果只有一个怪，而且血量很低，普通攻击
            if health < self.__predict_damage("Attack/Attack", self.api.monsters[monster_idx]):
                return [(ActionAttack(target=BattleAPI.MONSTER_START_ID + monster_idx, logging_skill_id="Attack/Attack"), 0)]

        # 如果场上仅存在 Boss 的话，给 Boss 加 Debuff
        bosses = [(monster_idx, monster) for monster_idx, monster in enumerate(self.api.monsters) if monster.health > self.config.elite_health_threshold]
        if len(bosses) == sum(monster.health > 0 for monster in self.api.monsters):
            for monster_idx, _ in bosses:
                # 打 Boss 不能用 Sleep，因为 Asleep Debuff 一碰就会消失，只应该在回血（不会攻击到怪兽）时用
                if action := self.__control_monster(monster_idx, with_sleep=False):
                    return [(action, 0)]

            # 如果所有怪兽都被叠了控制 Debuff，那么就给他们叠破防 Debuff
            if all(BattleBot.__has_effect("Silenced", monster.effects) for _, monster in bosses):
                for monster_idx, monster in bosses:
                    if BattleBot.__has_effect("Imperiled", monster.effects):
                        continue
                    if action := self.__try_to_use("magic", "Imperil", target=BattleAPI.MONSTER_START_ID + monster_idx):
                        return [(action, 0)]

        # 选择攻击魔法和目标
        action_scores = []
        for magic in self.api.get_player_magics():
            if magic.category != "magic_damage" or not magic.available:
                continue
            for monster_idx in range(len(self.api.monsters)):
                # Stop beating dead ponies
                if self.api.monsters[monster_idx].health == 0:
                    continue

                # 获取攻击范围窗口
                skill_id = f"Magic/{magic.name}"
                attack_range = self.skill_data[skill_id].attack_range if skill_id in self.skill_data else 0
                window = self.api.monsters[max(monster_idx - attack_range, 0):monster_idx + attack_range + 1]
                window = [monster for monster in window if monster.health > 0]

                # 从历史数据预测伤害，计算指标
                damages = [self.__predict_damage(skill_id, monster) for monster in window]
                will_die = sum(monster.health < damage for monster, damage in zip(window, damages))
                kill_deficit = sum(max(monster.health - damage, 0) for monster, damage in zip(window, damages))
                hit_number = len(window)
                damage_sum = sum(min(damage, monster.health) for monster, damage in zip(window, damages))
                damage_per_mana = damage_sum / magic.mana_cost

                # 添加进候选人名单
                action_scores.append((ActionMagic(magic=magic, target=BattleAPI.MONSTER_START_ID + monster_idx, logging_skill_id=skill_id), (will_die, -kill_deficit, damage_sum, hit_number, damage_per_mana)))

        # 返回可用动作
        if action_scores:
            return action_scores
        return [(ActionDefend(), 0)]


def battle(epsilon: float, config_override: dict[str, Any] | None = None) -> BattleResult:
    # 加载战斗数据和配置文件
    all_skill_data, all_monster_data, config = [json.loads(pathlib.Path(f"{name}.json").read_text("utf-8")) for name in ["skill_data", "monster_data", "config"]]
    all_skill_data, all_monster_data = [{k: data_class.model_validate(v) for k, v in data.items()} for data, data_class in [(all_skill_data, SkillData), (all_monster_data, MonsterData)]]

    # 创建 API
    authentication_config = AuthenticationConfig.model_validate(config["authentication"])
    api = BattleAPI(authentication_config.ipb_member_id, authentication_config.ipb_pass_hash, authentication_config.user_agent)

    # 打印初始日志
    print("= - " * 20)
    BattleBot.display_situation_after_action(api, api.logs[0])

    # 使每次 do_action 都实时显示 log
    api.add_post_action_hook(BattleBot.display_situation_after_action)

    # 创建 Battle Bot
    battle_bot_config = BattleBotConfig.model_validate(config["battle_bot"])
    for k, v in config_override.items() | {}:
        setattr(battle_bot_config, k, v)
    battle_bot = BattleBot(api, battle_bot_config, all_skill_data, all_monster_data)

    # 使用 Battle Bot 预测并执行动作
    while api.battle_result == BattleResult.IN_PROGRESS:
        # 决定动作
        actions = battle_bot.decide()
        best_action, best_score = max(actions, key=lambda x: x[1])
        action, score = best_action, best_score

        # 仅在多个可用动作时打印信息
        if len(actions) > 1 and random.random() < epsilon:
            action, score = random.choice(actions)
            print(f"[battle_bot.battle] [随机探索]\n\t随机选择动作: {action}（分数: {score}）\n\t最佳动作: {best_action}（分数: {best_score}）")

        # 执行动作
        battle_bot.execute_action(action)

    # 保存战斗数据
    for data, prefix, indent, separators in [(all_skill_data, "skill", "\t", None), (all_monster_data, "monster", None, (",", ":"))]:
        pathlib.Path(f"{prefix}_data.json").write_text(json.dumps({k: v.model_dump() for k, v in data.items()}, indent=indent, separators=separators), encoding="utf-8")

    return api.battle_result


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-e", "--epsilon", type=float, default=0., help="随机探索率，越大越激进，越小越保守")
    parser.add_argument("-l", "--loop", action="store_true", help="一直尝试进行战斗，直到找不到战斗")
    return parser.parse_args(args)


def main(args: argparse.Namespace):
    if args.loop:
        try:
            while True:
                battle(args.epsilon)
        except TokenNotFoundError:
            print("检测不到 battle_token，大概是没有战斗了")
    else:
        battle(args.epsilon)


if __name__ == "__main__":
    main(parse_args())
