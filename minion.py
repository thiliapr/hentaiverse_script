# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import json, pathlib, random, re
from typing import Literal, Callable
from collections import defaultdict
from utils.battle import BattleAPI

damage_data_file = pathlib.Path("damage_data.json")
config = json.loads(pathlib.Path("config.json").read_text("utf-8"))
damage_data = defaultdict(lambda: {"damage_sum": 0, "weight_sum": 0}) | json.loads(damage_data_file.read_text("utf-8"))
heal_before_end_flag = True
keep_buff = True


def try_to_use(api: BattleAPI, category: Literal["magic", "item"], name: str, *args, **kwargs) -> list[str] | None:
    if thing := next((thing for thing in getattr(api, f"get_player_{category}s")() if thing.name == name and thing.available), None):
        return getattr(api, f"use_{category}")(thing, *args, **kwargs)


def attack_with_logger(api: BattleAPI, skill_id: str, method: Callable[..., list[str]], *args, **kwargs) -> list[str]:
    # 执行攻击
    textlog = method(*args, **kwargs)
    # 分析伤害
    current_damage_list = []
    for log in textlog:
        if (res := re.search(r"[\w ]+ [a-z]+s ([\w\W]+) for (\d+) (\w+ )?damage", log)) is not None:
            monster_name, damage, _ = res.groups()
            if any(monster.name == monster_name for monster in api.get_monsters()):
                current_damage_list.append(int(damage))

    # EMA 更新数据（没打中一个怪兽就别记了）
    if current_damage_list:
        multiplier = 0.99
        damage_data[skill_id]["damage_sum"] = damage_data[skill_id]["damage_sum"] * multiplier + sum(current_damage_list) / len(current_damage_list)
        damage_data[skill_id]["weight_sum"] = damage_data[skill_id]["weight_sum"] * multiplier + 1
    return textlog


def predict_damage(skill_id: str) -> float:
    if skill_id in damage_data:
        return damage_data[skill_id]["damage_sum"] / damage_data[skill_id]["weight_sum"]
    return 2000 if skill_id.startswith("Magic/") else 800


def battle():
    api = BattleAPI(config["ipb_member_id"], config["ipb_pass_hash"], config["user_agent"])

    # 检测是否适合当前脚本
    if num_boss := sum(monster.health > 5000 for monster in api.get_monsters()):
        raise RuntimeError(f"检测到 {num_boss} 个 Boss，建议手动操作——使用 Scan 技能查抗性，给怪兽加 Silenced Debuff，打它。")

    while any(monster.health > 0 for monster in api.get_monsters()):
        # 保持 Buff
        if keep_buff:
            for item_name, effect_name in [("Health Draught", "Regeneration"), ("Mana Draught", "Replenishment")]:
                if not any(effect.name == effect_name for effect in api.get_player_effects()):
                    try_to_use(api, "item", item_name)

        # 保命
        if api.get_player_health() < 900:
            for item_name in ["Health Gem", "Health Potion"]:
                if try_to_use(api, "item", item_name):
                    break
            else:
                try_to_use(api, "magic", "Cure", BattleAPI.PLAYER_ID)
        elif api.get_player_health() < 1500:
            try_to_use(api, "magic", "Cure", BattleAPI.PLAYER_ID)

        # 回蓝
        if api.get_player_mana() < 400:
            for item_name in ["Mana Gem", "Mana Potion"]:
                if try_to_use(api, "item", item_name):
                    break

        # 扔掉（使用）没用物品
        for item_name in ["Mystic Gem", "Spirit Gem"]:
            if try_to_use(api, "item", item_name):
                break

        # 如果启用了结束前回复的模式，那么迷晕敌人，等待回复
        monster_health = [(idx, monster.health) for idx, monster in enumerate(api.get_monsters()) if monster.health > 0]
        if len(monster_health) == 1 and heal_before_end_flag and (api.get_player_health() < 2000 or api.get_player_mana() < 400):
            # 给敌人打麻药
            if not any(effect.name in ["Asleep", "Silenced", "Blinded", "Weakened"] for effect in api.get_monsters()[monster_idx].effects):
                for magic_name in ["Sleep", "Silence", "Blind", "Weaken"]:
                    if try_to_use(api, "magic", magic_name, BattleAPI.MONSTER_START_ID + monster_idx):
                        break
            # 救人
            if api.get_player_health() < 2000:
                if not try_to_use(api, "magic", "Cure", BattleAPI.PLAYER_ID):
                    for item_name in ["Health Gem", "Health Potion"]:
                        try_to_use(api, "item", item_name)
                        break
            # 回蓝
            if api.get_player_mana() < 400:
                for item_name in ["Mana Gem", "Mana Potion"]:
                    if try_to_use(api, "item", item_name):
                        break
                else:
                    api.do_defend()
            continue

        # 如果只有一个怪，而且血量很低，普通攻击
        if len(monster_health) == 1 and monster_health[0][1] < predict_damage("Attack/Attack"):
            monster_idx = monster_health[0][0]
            attack_with_logger(api, "Attack/Attack", api.do_attack, BattleAPI.MONSTER_START_ID + monster_idx)
            continue

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
                window = api.get_monsters()[max(monster_idx - 5, 0):monster_idx + 6]
                # 从历史数据预测伤害，计算指标
                damage = predict_damage(f"Magic/{attack_magic.name}")
                hit_number = len(window)
                will_die = sum(monster.health < damage for monster in window)
                damage_sum = sum(min(damage, monster.health) for monster in window)
                damage_per_mana = damage_sum / attack_magic.mana_cost
                # 添加进候选人名单
                target_score.append(((attack_magic, monster_idx), (will_die, damage_sum, hit_number, damage_per_mana)))

        # 选择魔法和目标
        (best_magic, best_target), _ = max(target_score, key=lambda x: x[1])
        if random.random() < 0.9:
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
    damage_data_file.write_text(json.dumps(damage_data), encoding="utf-8")


while True:
    battle()
