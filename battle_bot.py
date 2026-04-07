# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import json, pathlib, random, re, argparse, math, time
from functools import partial
from abc import ABC, abstractmethod
from typing import Any, Literal
from pydantic import BaseModel, Field
from utils.battle import BattleAPI, BattleResult, Effect, Item, Magic, Monster, TokenNotFoundError, AuthenticationConfig


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


class GameData(BaseModel):
    skill_damage_base: dict[str, EWMAData] = Field(default_factory=dict, description="每个攻击技能的基础伤害值")
    skill_max_enemies_hit: dict[str, int] = Field(default_factory=dict, description="每个攻击技能最多能同时攻击到的怪兽数量")
    skill_recovery_amount: dict[str, int] = Field(default_factory=dict, description="生命回复技能的最大回复量")
    skill_reaction_monsters_ratio: dict[str, EWMAData] = Field(default_factory=dict, description="每个动作执行后，会遭到反击的怪兽数量比例。例如值为 0.5 表示一半怪兽会反击，1 表示全部怪兽会反击，2 表示全部怪兽会反击两次")
    monster_damage_to_player: EWMAData = Field(default_factory=EWMAData, description="平均一个怪兽攻击时，对玩家造成伤害")
    skill_monster_damage_multiplier: dict[str, EWMAData] = Field(default_factory=dict, description="每个攻击技能对特定怪物的伤害倍率。Key 的格式: '{skill_id}@{monster_id}'")


class BaseAction(BaseModel, ABC):
    action_type: Literal["item", "magic", "attack", "defend"]
    attack_skill: bool = Field(False, description="该技能是否是攻击技能。如果是，则记录该动作造成的伤害")
    recovery_skill: bool = Field(False, description="该技能是否为生命回复技能。如果是，则记录该动作回复的血量")

    @property
    @abstractmethod
    def skill_id(self) -> str:
        pass


class ActionItem(BaseAction):
    action_type: Literal["item"] = Field("item")
    item: Item

    @property
    def skill_id(self) -> str:
        return f"Item:{self.item.name}"


class ActionMagic(BaseAction):
    action_type: Literal["magic"] = Field("magic")
    magic: Magic
    target: int

    @property
    def skill_id(self) -> str:
        return f"Magic:{self.magic.name}"


class ActionAttack(BaseAction):
    action_type: Literal["attack"] = Field("attack")
    target: int

    @property
    def skill_id(self) -> str:
        return "Attack"


class ActionDefend(BaseAction):
    action_type: Literal["defend"] = Field("defend")

    @property
    def skill_id(self) -> str:
        return "Defend"


class BattleBotConfig(BaseModel):
    elite_health_threshold: int = Field(gt=0, description="精英生物判定阈值，高于此值的怪兽被视为精英，并以特殊战术应对")
    critical_health_line: int = Field(gt=0, description="濒死判定线，低于此值将不计代价回血")
    normal_healing_line: int = Field(gt=0, description="治疗触发阈值，当血量高于濒死线但低于此值时，会尝试使用普通治疗技能（如非紧急的小恢复术）")
    mana_supply_line: int = Field(description="魔力补给触发阈值，低于此值尝试恢复魔力")
    spirit_supply_line: int = Field(description="Spirit 补给触发阈值，低于此值尝试恢复 Spirit")
    pre_battle_health_reserve: int = Field(gt=0, description="下一场战斗开始时的理想血量储备，用于应对连续无休息的战斗")
    pre_battle_mana_reserve: int = Field(gt=0, description="下一场战斗开始时的理想蓝量储备，用于应对连续无休息的战斗")
    spark_trigger_spirit: int = Field(description="在 Spirit 达到该值时，使用 Spark of Life 技能")
    prof_mana_threshold: int = Field(ge=0, description="刷技能熟练度的蓝量门槛。高于此值则用非伤害技能刷熟练度，低于则直接攻击结束战斗")
    ewma_multiplier: float = Field(0.99, gt=0, description="EWMA 更新数据的衰减因子，用于更新技能基础伤害，以及怪兽受到技能的伤害")


class BattleBot:
    def __init__(self, api: BattleAPI, config: BattleBotConfig, game_data: GameData):
        self.api = api
        self.config = config
        self.game_data = game_data
        self.__init_ewma_multiplier()
        self.__init_flags()

    def __init_ewma_multiplier(self):
        for data in [
            *self.game_data.skill_damage_base.values(),
            *self.game_data.skill_monster_damage_multiplier.values(),
            *self.game_data.skill_reaction_monsters_ratio.values(),
            self.game_data.monster_damage_to_player
        ]:
            data.multiplier = self.config.ewma_multiplier

    def __new_ewma_data(self) -> EWMAData:
        return EWMAData(multiplier=self.config.ewma_multiplier)

    def __init_flags(self):
        # 检测是否需要结束前回血、是否需要叠回血和回蓝 Buff
        self.heal_before_end_flag = self.draught_buff = False
        if (result := re.search(r"Round (\d+) / (\d+)", self.api.logs[0][0])) is not None:
            current_rounds, total_rounds = [int(x) for x in result.groups()]
            if current_rounds < total_rounds:
                self.heal_before_end_flag = self.draught_buff = True
        if any(monster.health > self.config.elite_health_threshold * 2 for monster in self.api.monsters):
            self.draught_buff = True

    @staticmethod
    def __has_effect(effect_name: str, effects: list[Effect]) -> bool:
        return any(effect.name == effect_name for effect in effects)

    def __get_alive_monsters(self) -> list[tuple[int, Monster]]:
        return [(idx, monster) for idx, monster in enumerate(self.api.monsters) if monster.health]

    def __predict_damage_to_monster(self, skill_id: str, monster: Monster) -> float:
        if skill_id not in (database := self.game_data.skill_damage_base):
            return 19890604
        skill_base_damage = database[skill_id].get_current_average()

        multiplier = 1
        if (key := f"{skill_id}@{monster.monster_id}") in (database := self.game_data.skill_monster_damage_multiplier):
            multiplier = database[key].get_current_average()
        return skill_base_damage * multiplier

    def __predict_recovery_amount(self, skill_id: str) -> int:
        return self.game_data.skill_recovery_amount.get(skill_id, 19890604)

    def __predict_damage_to_player(self, skill_id: str) -> float:
        reaction_monsters_ratio = each_damage = 0.
        alive_monsters = len(self.__get_alive_monsters())
        if skill_id in (database := self.game_data.skill_reaction_monsters_ratio):
            reaction_monsters_ratio = database[skill_id].get_current_average()
        if (database := self.game_data.monster_damage_to_player).total_weight:
            each_damage = database.get_current_average()
        return alive_monsters * reaction_monsters_ratio * each_damage

    def __update_attack_data(self, action: ActionMagic | ActionAttack, textlog: list[str]):
        # 分析伤害
        damage_info = []
        for monster_name, damage, source in BattleAPI.parse_damage(textlog):
            if source != "action":
                continue
            if (monster_info := next(((monster.monster_id, idx) for idx, monster in enumerate(self.api.monsters) if monster.name == monster_name), None)) is not None:
                monster_id, monster_index = monster_info
                damage_info.append((monster_index, monster_id, int(damage)))
        if not damage_info:
            return

        # 更新技能数据（伤害和最大攻击范围）
        monster_indices, _, damage_list = zip(*damage_info)
        self.game_data.skill_damage_base.setdefault(action.skill_id, self.__new_ewma_data()).add_observation(sum(damage_list) / len(damage_list))
        self.game_data.skill_max_enemies_hit[action.skill_id] = max(max(monster_indices) - min(monster_indices) + 1, self.game_data.skill_max_enemies_hit.get(action.skill_id, 1))

        # 更新怪兽数据，用技能基础伤害的倍数表示
        skill_base_damage = self.game_data.skill_damage_base[action.skill_id].get_current_average()
        for _, monster_id, damage in damage_info:
            self.game_data.skill_monster_damage_multiplier.setdefault(f"{action.skill_id}@{monster_id}", self.__new_ewma_data()).add_observation(damage / skill_base_damage)

    def __update_recovery_data(self, action: ActionMagic | ActionItem, textlog: list[str]):
        # 捕获有回复生命的记录
        health_restored = None
        for log in textlog:
            if res := re.search(r"Recovered (\d+) points of health", log):
                health_restored = res.group(1)
            elif res := re.search(r"You are healed for (\d+) Health Points", log):
                health_restored = res.group(1)

        # 更新数据
        if not health_restored:
            print("[battle_bot.BattleBot.__update_recovery_data] [TextLogReplay] ===== Begin of Replay ===")
            print("\n".join(textlog))
            print("[battle_bot.BattleBot.__update_recovery_data] [TextLogReplay] ===== End of Replay ===")
            raise RuntimeError("使用了回复魔法/物品，却找不到回复记录。这意味着存在回复记录规则的遗漏，请联系作者并把上面的记录发给作者修复（或者你自己加上去）")

        health_restored = int(health_restored)
        self.game_data.skill_recovery_amount[action.skill_id] = max(health_restored, self.game_data.skill_recovery_amount.get(action.recovery_skill, 0))

    def __update_monster_damage(self, action: BaseAction, textlog: list[str]):
        total_damage = 0
        damage_count = 0
        for log in textlog:
            # 获取伤害
            damage = None
            if res := re.search(r".+ [a-z]+s you for (\d+) \w+ damage", log):
                damage = res.group(1)
            elif res := re.search(r".+ uses .+, and [a-z]+s you for (\d+) \w+ damage", log):
                damage = res.group(1)
            elif res := re.search(r".+ [a-z]+s you, causing (\d+) points of \w+ damage", log):
                damage = res.group(1)
            elif res := re.search(r".+ [a-z]+s .+, which [a-z]+s! You( resist the attack, and)? take (\d+) \w+ damage", log):
                damage = res.group(2)
            # 累积计算平均伤害
            if damage:
                total_damage += int(damage)
                damage_count += 1

        # 更新数据
        if alive_monsters := len(self.__get_alive_monsters()):
            self.game_data.skill_reaction_monsters_ratio.setdefault(action.skill_id, self.__new_ewma_data()).add_observation(damage_count / alive_monsters)
        if damage_count:
            self.game_data.monster_damage_to_player.add_observation(total_damage / damage_count)

    def __analyze_score(self, skill_id: str, monster_idx: int, mana_cost: int) -> tuple:
        # 获取攻击窗口。以目标为中心，尽量保持对称（多出一个就给左侧），左右两侧最多各取 ceil((max_targets-1)/2) 个目标，总数量不超过 max_targets
        # 示例（max_targets=6，T 代表 Target）: [T B C D] E F G H; A [B C D T F G] H; A B C D [E F G T]
        # 你问我为什么这个窗口是这个逻辑？我咋知道，我就是个写外挂的，这个问题得问游戏开发者去
        max_targets = self.game_data.skill_max_enemies_hit.get(skill_id, 1)
        targets_up = min(monster_idx, math.ceil((max_targets - 1) / 2))
        targets_down = min(max_targets - targets_up - 1, math.ceil((max_targets - 1) / 2))
        window = list(enumerate(self.api.monsters))[monster_idx - targets_up:monster_idx + targets_down + 1]
        window = [(idx, monster) for idx, monster in window if monster.health > 0]

        # 从历史数据预测伤害，计算指标
        raw_damage_dealt = {idx: self.__predict_damage_to_monster(skill_id, monster) for idx, monster in window}
        actual_damage_taken = {idx: min(damage, self.api.monsters[idx].health) for idx, damage in raw_damage_dealt.items()}
        will_die = sum(actual_damage_taken.get(idx, 0) >= monster.health for idx, monster in window)
        kill_deficit = 0
        if survivor_healths := [x for x in [monster.health - actual_damage_taken.get(idx, 0) for idx, monster in enumerate(self.api.monsters)] if x]:
            kill_deficit = min(survivor_healths)
        damage_sum = sum(actual_damage_taken.values())
        damage_per_mana = damage_sum / max(mana_cost, 1)

        return will_die, -kill_deficit, damage_sum, len(window), damage_per_mana, sum(raw_damage_dealt.values())

    def __try_to_use(self, category: Literal["magic", "item"], name: str, **kwargs) -> ActionItem | ActionMagic | None:
        if thing := next((thing for thing in getattr(self.api, f"get_player_{category}s")() if thing.name == name and thing.available), None):
            return globals()[f"Action{category.capitalize()}"](**({category: thing} | kwargs))

    def __heal(self, critical: bool) -> BaseAction | None:
        # 便宜回血
        action_cure = None
        if self.__predict_recovery_amount("Magic:Cure") > self.__predict_damage_to_player("Magic:Cure"):
            action_cure = self.__try_to_use("magic", "Cure", target=BattleAPI.PLAYER_ID, recovery_skill=True)
        if not critical:
            return action_cure

        # 紧急、昂贵回血
        action_full_cure = None
        if self.__predict_recovery_amount("Magic:Full-Cure") > self.__predict_damage_to_player("Magic:Full-Cure"):
            action_full_cure = self.__try_to_use("magic", "Full-Cure", target=BattleAPI.PLAYER_ID, recovery_skill=True)

        action_consumable = None
        for item_name in ["Health Gem", "Health Potion"]:
            if action_consumable := self.__try_to_use("item", item_name, recovery_skill=True):
                break

        return action_full_cure or action_consumable or action_cure

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

    def __boss_debuff(self, bosses: list[tuple[int, Monster]]) -> BaseAction | None:
        for monster_idx, _ in bosses:
            # 打 Boss 不能用 Sleep，因为 Asleep Debuff 一碰就会消失，只应该在回血（不会攻击到怪兽）时用
            if action := self.__control_monster(monster_idx, with_sleep=False):
                return action

        # 如果所有怪兽都被叠了控制 Debuff，那么就给他们叠破防 Debuff
        if not all(BattleBot.__has_effect("Silenced", self.api.monsters[idx].effects) for idx in bosses):
            return

        for monster_idx, monster in bosses:
            if BattleBot.__has_effect("Imperiled", monster.effects):
                continue
            if action := self.__try_to_use("magic", "Imperil", target=BattleAPI.MONSTER_START_ID + monster_idx):
                return action

    def __heal_before_end(self) -> BaseAction | None:
        # 仅当战场只存在一个怪兽时使用
        if self.api.get_player_health() < self.config.pre_battle_health_reserve or self.api.get_player_mana() < self.config.pre_battle_mana_reserve:
            # 给敌人打麻药
            if action := self.__control_monster(self.__get_alive_monsters()[0][0], with_sleep=True):
                return action

            # 尝试回血到期望值
            if (self.api.get_player_health() < self.config.pre_battle_health_reserve) and (action := self.__heal(critical=False)):
                return action

            # 尝试回蓝到期望值
            if self.api.get_player_mana() < self.config.pre_battle_mana_reserve:
                for item_name in ["Mana Gem", "Mana Potion"]:
                    if action := self.__try_to_use("item", item_name):
                        return action
                return ActionDefend()

    def __grind_proficiency(self) -> BaseAction:
        # 回蓝 Buff
        if not BattleBot.__has_effect("Replenishment", self.api.get_player_effects()) and (action := self.__try_to_use("item", "Mana Draught")):
            return action

        # 低血量时，睡眠 + 回血，练 Supportive 和 Staff 熟练度; 高血量时，挨打，练 Armor 熟练度
        if self.api.get_player_health() < self.config.pre_battle_health_reserve:
            if action := self.__control_monster(self.__get_alive_monsters()[0][0], with_sleep=True):
                return action
            if action := self.__heal(critical=False):
                return action
        else:
            if not self.__has_effect("Asleep", self.__get_alive_monsters()[0][1].effects) and (action := self.__control_monster(self.__get_alive_monsters()[0][0], with_sleep=False)):
                return action

        return ActionDefend()

    def __auto_attack(self) -> list[tuple[BaseAction, tuple]]:
        # 获取各个目标的普通攻击、魔法分数
        action_scores = []
        for monster_idx in range(len(self.api.monsters)):
            # Stop beating dead ponies
            if self.api.monsters[monster_idx].health == 0:
                continue
            attack_target = BattleAPI.MONSTER_START_ID + monster_idx

            # 遍历每个魔法
            for magic in self.api.get_player_magics():
                if magic.category != "magic_damage" or not magic.available:
                    continue

                # 获取分数，并添加进候选人名单
                score = self.__analyze_score(f"Magic:{magic.name}", monster_idx, magic.mana_cost)
                action_scores.append((ActionMagic(magic=magic, target=attack_target, attack_skill=True), score))

            # 获取普通攻击情况
            score = self.__analyze_score("Attack", monster_idx, 0)
            action_scores.append((ActionAttack(target=attack_target, attack_skill=True), score))

        # 返回可用动作
        return action_scores

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

        # 打印现场情况
        print("+ - " * 10)
        print(f"Player: Health={api.get_player_health()}; Mana={api.get_player_mana()}; Spirit={api.get_player_spirit()}; Effects={format_effects_str(api.get_player_effects())}")
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

        # 更新数据
        self.__update_monster_damage(action, textlog)
        if action.attack_skill:
            self.__update_attack_data(action, textlog)
        if action.recovery_skill:
            self.__update_recovery_data(action, textlog)

        return textlog

    def decide(self) -> list[tuple[BaseAction, tuple | int]]:
        # 敌人血厚时，要有持续回血、回蓝的 Buff
        if self.draught_buff:
            for item_name, effect_name in [("Health Draught", "Regeneration"), ("Mana Draught", "Replenishment"), ("Spirit Draught", "Refreshment")]:
                if not BattleBot.__has_effect(effect_name, self.api.get_player_effects()) and (action := self.__try_to_use("item", item_name)):
                    return [(action, 0)]

        # 药水回蓝、Spirit 
        if self.api.get_player_mana() < self.config.mana_supply_line:
            for item_name in ["Mana Gem", "Mana Potion"]:
                if action := self.__try_to_use("item", item_name):
                    return [(action, 0)]

        if self.api.get_player_spirit() < self.config.spirit_supply_line:
            if action := self.__try_to_use("item", "Spirit Potion"):
                return [(action, 0)]

        # 分情况进行急救回血和普通回血
        if self.api.get_player_health() < self.config.critical_health_line:
            if action := self.__heal(critical=True):
                return [(action, 0)]
        if self.api.get_player_health() < self.config.normal_healing_line:
            if action := self.__heal(critical=False):
                return [(action, 0)]

        # 丢弃无用物品
        for item_name in ["Mystic Gem", "Spirit Gem"]:
            if action := self.__try_to_use("item", item_name):
                return [(action, 0)]

        if sum(monster.health > 0 for monster in self.api.monsters) == 1:
            # 如果启用了结束前回复的模式，那么迷晕敌人，等待回复
            if self.heal_before_end_flag:
                if (action := self.__heal_before_end()):
                    return [(action, 0)]
            # 耍戏，提升属性熟练度
            elif self.api.get_player_mana() > self.config.prof_mana_threshold:
                return [(self.__grind_proficiency(), 0)]

        # 如果可以撑过这回合，并且 Spirit 足够的话（Spark of Life 需要 Spirit 发挥作用），上保命 Buff
        if self.api.get_player_health() > self.__predict_damage_to_player("Magic:Spark of Life") and self.api.get_player_spirit() >= self.config.spark_trigger_spirit and not BattleBot.__has_effect("Spark of Life", self.api.get_player_effects()):
            if action := self.__try_to_use("magic", "Spark of Life", target=BattleAPI.PLAYER_ID):
                return [(action, 0)]

        # 如果场上仅存在 Boss 的话，给 Boss 加 Debuff
        bosses = [(monster_idx, monster) for monster_idx, monster in enumerate(self.api.monsters) if monster.health > self.config.elite_health_threshold]
        if len(bosses) == len(self.__get_alive_monsters()) and (action := self.__boss_debuff(bosses)):
            return [(action, 0)]

        # 攻击阶段
        return self.__auto_attack()


def battle(isekai: bool, epsilon: float, difficult_level: str, config_override: dict[str, Any] | None = None) -> BattleResult:
    # 初始化档案
    root_dir = pathlib.Path("world") / ("isekai" if isekai else "persistent")
    config_path, game_data_path, monster_damage_to_player_path = [root_dir / f"{name}.json" for name in ["config", "game_data", f"monster_damage_to_player [level={difficult_level}]"]]
    if not all(path.exists() for path in [config_path, game_data_path]):
        game_data = GameData().model_dump()
        game_data.pop("monster_damage_to_player")

        root_dir.mkdir(parents=True, exist_ok=True)
        game_data_path.write_text(json.dumps(game_data, indent=2))
        print(f"[BattleBot.battle] 检测到不存在 {root_dir} 档案，已初始化档案")

    # 加载战斗数据和配置文件
    config, game_data, monster_damage_to_player = [json.loads(path.read_text("utf-8")) if path.exists() else None for path in [config_path, game_data_path, monster_damage_to_player_path]]
    game_data["monster_damage_to_player"] = EWMAData().model_dump()
    if monster_damage_to_player:
        game_data["monster_damage_to_player"] = monster_damage_to_player

    # 创建 API
    auth_config = AuthenticationConfig.model_validate(config["authentication"])
    api = BattleAPI(isekai, auth_config)

    # 打印初始日志
    print("= - " * 20)
    BattleBot.display_situation_after_action(api, api.logs[0])

    # 使每次 do_action 都实时显示 log
    api.add_post_action_hook(BattleBot.display_situation_after_action)

    # 创建 Battle Bot
    battle_bot_config = BattleBotConfig.model_validate(config["battle_bot"])
    game_data = GameData.model_validate(game_data)
    for k, v in (config_override or {}).items():
        setattr(battle_bot_config, k, v)
    battle_bot = BattleBot(api, battle_bot_config, game_data)

    # 使用 Battle Bot 预测并执行动作
    last_execution_time = 0
    while api.battle_result == BattleResult.IN_PROGRESS:
        # 决定动作
        actions = battle_bot.decide()
        action, score = best_action, best_score = max(actions, key=lambda x: x[1])

        # 仅在多个可用动作时打印信息
        if len(actions) > 1:
            print(f"[battle_bot.battle] 最佳动作: skill={best_action.skill_id}; target={best_action.target}; score={best_score}")
            if random.random() < epsilon:
                action, score = random.choice(actions)
                print(f"[battle_bot.battle] [随机探索] 随机选择动作: skill={action.skill_id}; target={action.target}; score={score}")

        # 控制频率并执行动作
        # To prevent botting and overloading the server there is a server side restriction which prevents more than 4 turns per second.
        # https://ehwiki.org/wiki/Action_Speed
        if (interval := time.time() - last_execution_time) < 1 / 4:
            time.sleep(1 / 4 - interval)
        battle_bot.execute_action(action)
        last_execution_time = time.time()

    # 保存战斗数据
    game_data = game_data.model_dump()
    monster_damage_to_player = game_data.pop("monster_damage_to_player")
    for data, prefix in [(game_data, "game_data"), (monster_damage_to_player, f"monster_damage_to_player [level={difficult_level}]")]:
        (root_dir / pathlib.Path(f"{prefix}.json")).write_text(json.dumps(data, indent=2), encoding="utf-8")

    return api.battle_result


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--isekai", action="store_true", help="游戏分两个模式: Persistent 和 Isekai。指定该 flag 以进行异世界的战斗")
    parser.add_argument("-e", "--epsilon", type=float, default=0., help="随机探索率，越大越激进，越小越保守")
    parser.add_argument("-l", "--loop", action="store_true", help="一直尝试进行战斗，直到找不到战斗")
    parser.add_argument("-d", "--difficult-level", default="default", help="这场战斗的难度等级。这会影响怪兽对玩家伤害的预测")
    return parser.parse_args(args)


def main(args: argparse.Namespace):
    battle_func = partial(battle, args.isekai, args.epsilon, args.difficult_level)
    if args.loop:
        try:
            while True:
                battle_func()
        except TokenNotFoundError:
            print("检测不到 battle_token，大概是没有战斗了")
    else:
        battle_func()


if __name__ == "__main__":
    main(parse_args())
