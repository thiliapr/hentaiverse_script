# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import json, pathlib, random, re
from typing import Literal, Callable
from collections import defaultdict
from utils.battle import BattleAPI, Monster

# 根据你自己的经验去设置
ATTACK_RANGE = 5  # 一个魔法可以攻击到上下各多少个怪兽，取决于你的实际情况。比如 1 就代表能够攻击到目标上面 1 个怪兽和下面 1 个怪兽，加上目标本身，一共能够攻击到 3 个怪兽；2 就代表能攻击到 5 个怪兽，3 就 7 个，依此类推
BOSS_HELATH_THRESHOLD = 5000  # BOSS 是有多少血量以上的怪兽
DEFAULT_MAGIC_DAMAGE = 2000  # 不知道一个魔法多少伤害时，瞎蒙的缺省值
DEFAULT_PHYSICS_DAMAGE = 1000  # 不知道普通攻击多少伤害时，瞎蒙的缺省值
ICU_HEALTH_THRESHOLD = 1000  # 什么时候是快死了的状态，低于这个血量会想尽一切办法回血
DOCTOR_HEALTH_THRESHOLD = 1900  # 什么时候回复生命，低于这个血量会尝试回血
MANA_RESTORE_THRESHOLD = 400  # 什么时候回复蓝量，低于这个蓝量会尝试用药水回蓝
EXPECT_HEALTH_BEFORE_END = 2000  # 战斗将要结束时，你期望有多少血量
EXPECT_MANA_BEFORE_END = 2000  # 战斗将要结束时，你期望有多少蓝量

# 低级设置
EMA_MULTIPLIER = 0.99  # 1 - EMA 衰减因子
EPSILON = 0.3  # 探索率，越高越冒险，越低越死板守旧

# 脚本预设，没事别动
SKILL_DAMAGE_FILE = pathlib.Path("skill_damage_data.json")
MONSTER_DAMAGE_FILE = pathlib.Path("monster_damage_data.json")
config = json.loads(pathlib.Path("config.json").read_text("utf-8"))
skill_damage_data = defaultdict(lambda: {"damage_sum": 0, "weight_sum": 0}) | json.loads(SKILL_DAMAGE_FILE.read_text("utf-8"))
monster_damage_data = defaultdict(lambda: defaultdict(lambda: {"damage_sum": 0, "weight_sum": 0}))
# 手动更新 monster 信息，否则会覆盖第二级 defaultdict
for skill_id, skill_data in json.loads(MONSTER_DAMAGE_FILE.read_text("utf-8")).items():
    monster_damage_data[skill_id] |= skill_data


def try_to_use(api: BattleAPI, category: Literal["magic", "item"], name: str, *args, **kwargs) -> list[str] | None:
    if thing := next((thing for thing in getattr(api, f"get_player_{category}s")() if thing.name == name and thing.available), None):
        return getattr(api, f"use_{category}")(thing, *args, **kwargs)


def attack_with_logger(api: BattleAPI, skill_id: str, method: Callable[..., list[str]], *args, **kwargs) -> list[str]:
    # 执行攻击
    textlog = method(*args, **kwargs)

    # 分析伤害
    damage_list = []
    monster_ids = []
    for log in textlog:
        if (res := re.search(r"[\w ]+ [a-z]+s ([\w\W]+) for (\d+) (\w+ )?damage", log)) is not None:
            monster_name, damage, _ = res.groups()
            if (monster_id := next((monster.monster_id for monster in api.get_monsters() if monster.name == monster_name), None)) is not None:
                damage_list.append(int(damage))
                monster_ids.append(monster_id)

    # 更新技能数据（没打中一个怪兽就别记了）
    skill_data = skill_damage_data[skill_id]
    if damage_list:
        value = sum(damage_list) / len(damage_list)
        skill_data["damage_sum"] = skill_data["damage_sum"] * EMA_MULTIPLIER + value
        skill_data["weight_sum"] = skill_data["weight_sum"] * EMA_MULTIPLIER + 1

    # 更新怪兽数据，用技能基础伤害的倍数表示
    skill_base_damage = skill_data["damage_sum"] / skill_data["weight_sum"]
    for damage, monster_id in zip(damage_list, monster_ids):
        monster_data = monster_damage_data[skill_id][str(monster_id)]
        value = damage / skill_base_damage
        monster_data["damage_sum"] = monster_data["damage_sum"] * EMA_MULTIPLIER + value
        monster_data["weight_sum"] = monster_data["weight_sum"] * EMA_MULTIPLIER + 1

    return textlog


def predict_damage(skill_id: str, monster: Monster) -> float:
    # 获取技能伤害
    if skill_id not in skill_damage_data:
        return DEFAULT_MAGIC_DAMAGE if skill_id.startswith("Magic/") else DEFAULT_PHYSICS_DAMAGE
    skill_base_damage = skill_damage_data[skill_id]["damage_sum"] / skill_damage_data[skill_id]["weight_sum"]

    # 获取技能对怪兽的伤害
    if str(monster.monster_id) not in (skill_data := monster_damage_data[skill_id]):
        return skill_base_damage
    monster_data = skill_data[str(monster.monster_id)]
    multiplier = monster_data["damage_sum"] / monster_data["weight_sum"]
    return skill_base_damage * multiplier


def control_monster(api: BattleAPI, monster_idx: int, with_sleep: bool):
    if not any(effect.name in (["Asleep"] if with_sleep else []) + ["Silenced", "Blinded", "Weakened"] for effect in api.get_monsters()[monster_idx].effects):
        for magic_name in (["Sleep"] if with_sleep else []) + ["Silence", "Blind", "Weaken"]:
            if try_to_use(api, "magic", magic_name, BattleAPI.MONSTER_START_ID + monster_idx):
                break


def battle():
    api = BattleAPI(config["ipb_member_id"], config["ipb_pass_hash"], config["user_agent"])

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
                    try_to_use(api, "item", item_name)

        # 保命
        if api.get_player_health() < ICU_HEALTH_THRESHOLD:
            for item_name in ["Health Gem", "Health Potion"]:
                if try_to_use(api, "item", item_name):
                    break
            else:
                try_to_use(api, "magic", "Cure", BattleAPI.PLAYER_ID)
        elif api.get_player_health() < DOCTOR_HEALTH_THRESHOLD:
            try_to_use(api, "magic", "Cure", BattleAPI.PLAYER_ID)

        # 回蓝
        if api.get_player_mana() < MANA_RESTORE_THRESHOLD:
            for item_name in ["Mana Gem", "Mana Potion"]:
                if try_to_use(api, "item", item_name):
                    break

        # 扔掉（使用）没用物品
        for item_name in ["Mystic Gem", "Spirit Gem"]:
            if try_to_use(api, "item", item_name):
                break

        # 如果启用了结束前回复的模式，那么迷晕敌人，等待回复
        monster_health = [(idx, monster.health) for idx, monster in enumerate(api.get_monsters()) if monster.health > 0]
        if len(monster_health) == 1:
            monster_idx, health = monster_health[0]
            if heal_before_end_flag and (api.get_player_health() < EXPECT_HEALTH_BEFORE_END or api.get_player_mana() < EXPECT_MANA_BEFORE_END):
                # 给敌人打麻药
                control_monster(api, monster_idx, True)
                # 救人
                if api.get_player_health() < EXPECT_HEALTH_BEFORE_END:
                    if not try_to_use(api, "magic", "Cure", BattleAPI.PLAYER_ID):
                        for item_name in ["Health Gem", "Health Potion"]:
                            try_to_use(api, "item", item_name)
                            break
                # 回蓝
                if api.get_player_mana() < EXPECT_MANA_BEFORE_END:
                    for item_name in ["Mana Gem", "Mana Potion"]:
                        if try_to_use(api, "item", item_name):
                            break
                    else:
                        api.do_defend()
                continue

            # 如果只有一个怪，而且血量很低，普通攻击
            if health < predict_damage("Attack/Attack", api.get_monsters()[monster_idx]):
                attack_with_logger(api, "Attack/Attack", api.do_attack, BattleAPI.MONSTER_START_ID + monster_idx)
                continue

        # 如果场上仅存在 Boss 的话，给 Boss 加 Debuff
        bosses = [monster_idx for monster_idx, monster in enumerate(api.get_monsters()) if monster.health > BOSS_HELATH_THRESHOLD]
        if len(bosses) == sum(monster.health > 0 for monster in api.get_monsters()):
            for monster_idx in bosses:
                # 打 Boss 不能用 Sleep，因为 Asleep Debuff 一碰就会消失，只应该在回血（不会攻击到怪兽）时用
                control_monster(api, monster_idx, False)

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
                window = api.get_monsters()[max(monster_idx - ATTACK_RANGE, 0):monster_idx + ATTACK_RANGE + 1]
                # 从历史数据预测伤害，计算指标
                damage = predict_damage(f"Magic/{attack_magic.name}", api.get_monsters()[monster_idx])
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
        print(f"计划用 {best_magic.name} 打第 {best_target + 1} 个怪兽。")

        # 攻击
        for log in attack_with_logger(api, f"Magic/{best_magic.name}", api.use_magic, best_magic, BattleAPI.MONSTER_START_ID + best_target):
            print(log)
            if log == "Stop beating dead ponies.":
                print("一些不好的事情发生了！服务器说你在鞭尸！")
                api.get_monsters()[best_target].health = 0

        # 打印当前状态
        print(f"Player: HP={api.get_player_health()}; MP={api.get_player_mana()}; SP={api.get_player_spirit()}")
        for i, monster in enumerate(api.get_monsters(), 1):
            print(f"Monster {i}({monster.name}): HP={monster.health}; MP={monster.mana / 120 * 100:.0f}%; SP={monster.spirit / 120 * 100:.0f}%")

    # 战斗结束时保存日志
    SKILL_DAMAGE_FILE.write_text(json.dumps(skill_damage_data, indent="\t"), encoding="utf-8")
    MONSTER_DAMAGE_FILE.write_text(json.dumps(monster_damage_data, indent="\t"), encoding="utf-8")


if __name__ == "__main__":
    while True:
        battle()
