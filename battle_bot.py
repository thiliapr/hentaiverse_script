# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import json, pathlib, random, re, copy
from typing import Literal, Callable
from utils.battle import BattleAPI, Monster

# 根据你自己的经验去设置
BOSS_HELATH_THRESHOLD = 5000  # BOSS 是有多少血量以上的怪兽
DEFAULT_MAGIC_DAMAGE = 2000  # 不知道一个魔法多少伤害时，瞎蒙的缺省值
DEFAULT_PHYSICS_DAMAGE = 1000  # 不知道普通攻击多少伤害时，瞎蒙的缺省值
ICU_HEALTH_THRESHOLD = 1000  # 什么时候是快死了的状态，低于这个血量会想尽一切办法回血
DOCTOR_HEALTH_THRESHOLD = 1900  # 什么时候回复生命，低于这个血量会尝试回血
MANA_RESTORE_THRESHOLD = 400  # 什么时候回复蓝量，低于这个蓝量会尝试用药水回蓝
EXPECT_HEALTH_BEFORE_END = 2000  # 战斗将要结束时，你期望有多少血量
EXPECT_MANA_BEFORE_END = 400  # 战斗将要结束时，你期望有多少蓝量

# 低级设置
EMA_MULTIPLIER = 0.99  # 1 - EMA 衰减因子
EPSILON = 0.3  # 探索率，越高越冒险，越低越死板守旧

# 脚本预设，没事别动
SKILL_DATA_FILE = pathlib.Path("skill_data.json")
MONSTER_DATA_FILE = pathlib.Path("monster_data.json")
config = json.loads(pathlib.Path("config.json").read_text("utf-8"))
MONSTER_TEMPLATE = {"relative_damage": {"sum_value": 0, "sum_weight": 0}}
SKILL_TEMPLATE = {"attack_range": 0, "damage": {"sum_value": 0, "sum_weight": 0}}
all_skill_data = json.loads(SKILL_DATA_FILE.read_text("utf-8"))
all_monster_data = json.loads(MONSTER_DATA_FILE.read_text("utf-8"))


class EMA:
    @staticmethod
    def predict(data: dict[str, float]) -> float:
        return data["sum_value"] / data["sum_weight"]

    @staticmethod
    def update(data: dict[str, float], new_value: float):
        data["sum_value"] = data["sum_value"] * EMA_MULTIPLIER + new_value
        data["sum_weight"] = data["sum_weight"] * EMA_MULTIPLIER + 1


class BattleTool:
    @staticmethod
    def display_log_after_action(_, textlog: list[str]):
        print("\n".join(textlog))
        print("=" * 32)

    @staticmethod
    def attack_with_logger(api: BattleAPI, skill_id: str, method: Callable[..., list[str]], *args, **kwargs) -> list[str]:
        # 执行攻击
        textlog = method(*args, **kwargs)

        # 分析伤害
        damage_info = []
        for log in textlog:
            if (res := re.search(r"[\w ]+ [a-z]+s ([\w\W]+) for (\d+) (\w+ )?damage", log)) is not None:
                monster_name, damage, _ = res.groups()
                if (monster_info := next(((monster.monster_id, idx) for idx, monster in enumerate(api.get_monsters()) if monster.name == monster_name), None)) is not None:
                    monster_id, monster_index = monster_info
                    damage_info.append((monster_index, monster_id, int(damage)))

        # 没有造成任何伤害时跳过数据库更新
        if not damage_info:
            return textlog
        monster_indices, _, damage_list = zip(*damage_info)

        # 更新技能数据（伤害和范围）
        skill_data = all_skill_data.setdefault(skill_id, copy.deepcopy(SKILL_TEMPLATE))
        EMA.update(skill_data["damage"], sum(damage_list) / len(damage_list))
        target_monster_idx = args[-1] - BattleAPI.MONSTER_START_ID
        skill_data["attack_range"] = max(max(monster_indices) - target_monster_idx, target_monster_idx - min(monster_indices), skill_data["attack_range"])

        # 更新怪兽数据，用技能基础伤害的倍数表示
        skill_base_damage = EMA.predict(skill_data["damage"])
        for _, monster_id, damage in damage_info:
            EMA.update(all_monster_data.setdefault(skill_id, {}).setdefault(str(monster_id), copy.deepcopy(MONSTER_TEMPLATE))["relative_damage"], damage / skill_base_damage)

        return textlog

    @staticmethod
    def predict_damage(skill_id: str, monster: Monster) -> float:
        # 获取技能伤害
        if skill_id not in all_skill_data:
            return DEFAULT_MAGIC_DAMAGE if skill_id.startswith("Magic/") else DEFAULT_PHYSICS_DAMAGE
        skill_base_damage = EMA.predict(all_skill_data[skill_id]["damage"])

        # 获取技能对怪兽的伤害
        if skill_id not in all_monster_data:
            return skill_base_damage
        if str(monster.monster_id) not in (skill_data := all_monster_data[skill_id]):
            return skill_base_damage
        multiplier = EMA.predict(skill_data[str(monster.monster_id)]["relative_damage"])
        return skill_base_damage * multiplier

    @staticmethod
    def predict_attack_range(skill_id: str) -> int:
        if skill_id not in all_skill_data:
            return 0
        return all_skill_data[skill_id]["attack_range"]

    @staticmethod
    def try_to_use(api: BattleAPI, category: Literal["magic", "item"], name: str, *args, **kwargs) -> list[str] | None:
        if thing := next((thing for thing in getattr(api, f"get_player_{category}s")() if thing.name == name and thing.available), None):
            return getattr(api, f"use_{category}")(thing, *args, **kwargs)

    @staticmethod
    def control_monster(api: BattleAPI, monster_idx: int, with_sleep: bool):
        if not any(effect.name in (["Asleep"] if with_sleep else []) + ["Silenced", "Blinded", "Weakened"] for effect in api.get_monsters()[monster_idx].effects):
            for magic_name in (["Sleep"] if with_sleep else []) + ["Silence", "Blind", "Weaken"]:
                if BattleTool.try_to_use(api, "magic", magic_name, BattleAPI.MONSTER_START_ID + monster_idx):
                    break


def battle():
    api = BattleAPI(config["ipb_member_id"], config["ipb_pass_hash"], config["user_agent"])

    # 使每次 do_action 都实时显示 log，而不是循环最后才显示
    api.add_post_action_hook(BattleTool.display_log_after_action)

    # 检测是否需要结束前回血、是否需要叠 Buff
    # 如果分析当前回合数和总回合数没有结果，说明战斗只持续一个回合
    heal_before_end_flag = keep_buff = False
    if (result := re.search(r"Round (\d+) / (\d+)", api.logs[0][-1])) is not None:
        current_rounds, total_rounds = [int(x) for x in result.groups()]
        if current_rounds < total_rounds:
            heal_before_end_flag = True
        if total_rounds > 5:
            keep_buff = True

    while any(monster.health > 0 for monster in api.get_monsters()):
        # 保持 Buff
        if keep_buff:
            for item_name, effect_name in [("Health Draught", "Regeneration"), ("Mana Draught", "Replenishment")]:
                if not any(effect.name == effect_name for effect in api.get_player_effects()):
                    BattleTool.try_to_use(api, "item", item_name)

        # 保命
        if api.get_player_health() < ICU_HEALTH_THRESHOLD:
            for item_name in ["Health Gem", "Health Potion"]:
                if BattleTool.try_to_use(api, "item", item_name):
                    break
            else:
                BattleTool.try_to_use(api, "magic", "Cure", BattleAPI.PLAYER_ID)
        elif api.get_player_health() < DOCTOR_HEALTH_THRESHOLD:
            BattleTool.try_to_use(api, "magic", "Cure", BattleAPI.PLAYER_ID)

        # 回蓝
        if api.get_player_mana() < MANA_RESTORE_THRESHOLD:
            for item_name in ["Mana Gem", "Mana Potion"]:
                if BattleTool.try_to_use(api, "item", item_name):
                    break

        # 扔掉（使用）没用物品
        for item_name in ["Mystic Gem", "Spirit Gem"]:
            if BattleTool.try_to_use(api, "item", item_name):
                break

        # 如果启用了结束前回复的模式，那么迷晕敌人，等待回复
        monster_health = [(idx, monster.health) for idx, monster in enumerate(api.get_monsters()) if monster.health > 0]
        if len(monster_health) == 1:
            monster_idx, health = monster_health[0]
            if heal_before_end_flag and (api.get_player_health() < EXPECT_HEALTH_BEFORE_END or api.get_player_mana() < EXPECT_MANA_BEFORE_END):
                # 给敌人打麻药
                BattleTool.control_monster(api, monster_idx, True)
                # 救人
                if api.get_player_health() < EXPECT_HEALTH_BEFORE_END:
                    if not BattleTool.try_to_use(api, "magic", "Cure", BattleAPI.PLAYER_ID):
                        for item_name in ["Health Gem", "Health Potion"]:
                            BattleTool.try_to_use(api, "item", item_name)
                            break
                # 回蓝
                if api.get_player_mana() < EXPECT_MANA_BEFORE_END:
                    for item_name in ["Mana Gem", "Mana Potion"]:
                        if BattleTool.try_to_use(api, "item", item_name):
                            break
                    else:
                        api.do_defend()
                continue

            # 如果只有一个怪，而且血量很低，普通攻击
            if health < BattleTool.predict_damage("Attack/Attack", api.get_monsters()[monster_idx]):
                BattleTool.attack_with_logger(api, "Attack/Attack", api.do_attack, BattleAPI.MONSTER_START_ID + monster_idx)
                continue

        # 如果场上仅存在 Boss 的话，给 Boss 加 Debuff
        bosses = [monster_idx for monster_idx, monster in enumerate(api.get_monsters()) if monster.health > BOSS_HELATH_THRESHOLD]
        if len(bosses) == sum(monster.health > 0 for monster in api.get_monsters()):
            for monster_idx in bosses:
                # 打 Boss 不能用 Sleep，因为 Asleep Debuff 一碰就会消失，只应该在回血（不会攻击到怪兽）时用
                BattleTool.control_monster(api, monster_idx, False)

        # 攻击死最多的、伤害最多的、打中最多的
        target_score = []
        for attack_magic in api.get_player_magics():
            if attack_magic.category != "magic_damage" or not attack_magic.available:
                continue
            for monster_idx in range(len(api.get_monsters())):
                # Stop beating dead ponies
                if api.get_monsters()[monster_idx].health == 0:
                    continue
                # Python 切片允许上界超过列表长度
                skill_id = f"Magic/{attack_magic.name}"
                attack_range = BattleTool.predict_attack_range(skill_id)
                window = api.get_monsters()[max(monster_idx - attack_range, 0):monster_idx + attack_range + 1]
                # 从历史数据预测伤害，计算指标
                damage = BattleTool.predict_damage(skill_id, api.get_monsters()[monster_idx])
                hit_number = len(window)
                will_die = sum(monster.health < damage for monster in window)
                damage_sum = sum(min(damage, monster.health) for monster in window)
                damage_per_mana = damage_sum / attack_magic.mana_cost
                # 添加进候选人名单
                target_score.append(((attack_magic, monster_idx), (will_die, damage_sum, hit_number, damage_per_mana)))

        # 选择魔法和目标
        (best_magic, best_target), _ = max(target_score, key=lambda x: x[1])
        if random.random() < EPSILON:
            (best_magic, _), _ = random.choice(target_score)

        # 执行攻击
        for log in BattleTool.attack_with_logger(api, f"Magic/{best_magic.name}", api.use_magic, best_magic, BattleAPI.MONSTER_START_ID + best_target):
            if log == "Stop beating dead ponies.":
                print("一些不好的事情发生了！服务器说你在鞭尸！")
                api.get_monsters()[best_target].health = 0

    # 战斗结束时保存战斗数据
    SKILL_DATA_FILE.write_text(json.dumps(all_skill_data, indent="\t"), encoding="utf-8")
    MONSTER_DATA_FILE.write_text(json.dumps(all_monster_data, indent="\t"), encoding="utf-8")


if __name__ == "__main__":
    while True:
        battle()
