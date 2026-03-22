# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import re
import enum
from typing import Any
from collections.abc import Callable
import requests
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field
from utils.network import request_with_retry
from utils.constants import MAIN_URL


class Magic(BaseModel):
    name: str = Field(description="显示名称")
    available: bool = Field(description="目前是否可用")
    skill_id: int = Field(ge=0, description="技能 ID")
    description: str = Field(description="详细描述")
    mana_cost: int = Field(ge=0, description="需要消耗的蓝量")
    cooldown: int = Field(ge=0, description="冷却的回合数")
    category: str = Field(description="所属分类")


class Item(BaseModel):
    name: str = Field(description="显示名称")
    available: bool = Field(description="目前是否可用")
    skill_id: str | None = Field(description="技能 ID，不可用时没有")


class Effect(BaseModel):
    name: str = Field(description="显示名称")
    description: str = Field(description="详细描述")
    remaining_turns: int = Field(ge=0, description="将在多少个回合后消失")


class Monster(BaseModel):
    name: str = Field(description="显示名称")
    monster_id: int = Field(ge=0, description="怪兽在数据库里的 ID")
    level: int = Field(ge=0, description="等级")
    health: int = Field(ge=0, description="当前的血量")
    mana: int = Field(0, ge=0, description="当前的蓝量")
    spirit: int = Field(0, ge=0, description="当前的 Spirit 量")
    effects: list[Effect] = Field([], description="怪兽身上的各种 Buff")


class DamageLog(BaseModel):
    skill_name: str = Field(description="什么技能造成了伤害")
    action: str = Field(description="造成了怎样的伤害，比如 hit、crit、blast")
    monster_name: str = Field(description="被伤害了的怪兽的名字")
    damage: int = Field(ge=1, description="造成了多少生命值的伤害")
    attribute: str | None = Field(description="造成的是什么属性的伤害")


class BattleResult(enum.Enum):
    IN_PROGRESS = enum.auto()
    VICTORY = enum.auto()
    DEFEAT = enum.auto()
    UNCONFIRMED = enum.auto()


class TokenNotFoundError(Exception):
    def __init__(self, page: str):
        self.page = page


class BattleAPI:
    PLAYER_ID = 0
    MONSTER_START_ID = 1

    def __init__(self, ipb_member_id: str, ipb_pass_hash: str, user_agent: str | None = None):
        # 定义 request 参数
        self.__request_kwargs = {"cookies": {"ipb_member_id": ipb_member_id, "ipb_pass_hash": ipb_pass_hash}}
        if user_agent:
            self.__request_kwargs["headers"] = {"User-Agent": user_agent}

        # 根据页面更新 soup 和战斗记录
        self.__containers = {container_id: None for container_id in ["pane_vitals", "pane_effects", "pane_monster", "pane_item", "table_magic"]}
        self.__battle_token, self.__containers, initial_logs = self.__refresh_page_and_parse()
        self.logs = [initial_logs]

        # 解析获取怪兽信息，按怪物出场的顺序排序，A 最先出场，B 第二个出场，以此类推
        monsters_info = sorted([x.groups() for x in [re.search(r"Spawned Monster ([A-Z]): MID=([0-9]+) \(([^)]+)\) LV=(\d+) HP=(\d+)", log) for log in initial_logs] if x is not None], key=lambda x: x[0])
        self.monsters = [Monster(name=name, monster_id=monster_id, level=level, health=health) for _, monster_id, name, level, health in monsters_info]
        self.__update_monster_info()

        # 初始化战斗结果
        self.battle_result = BattleResult.IN_PROGRESS

        # 初始化钩子列表
        self.__post_action_hooks = []

    def __do_action(self, action: dict[str, int | str]) -> list[str]:
        # 补充信息
        action |= {"type": "battle", "method": "action", "token": self.__battle_token}

        # 告诉服务器执行动作
        textlog = []
        while True:
            # 发送动作包给服务器
            resp_json = None
            try:
                resp_json = requests.post(f"{MAIN_URL}/json", json=action, timeout=30, **self.__request_kwargs).json()
            except (requests.exceptions.ChunkedEncodingError, requests.ConnectionError, requests.ReadTimeout) as e:
                print(f"[BattleAPI.__do_action] 发生了网络错误: {e}")

            # 如果响应（没有发生网络错误），就用响应更新容器和战斗记录
            if resp_json:
                for container_id in self.__containers:
                    if container_id in resp_json:
                        self.__containers[container_id] = BeautifulSoup(resp_json[container_id], "lxml")
                textlog = [log["t"] for log in resp_json["textlog"]]

            # 否则刷新页面，检测是否发送请求成功
            else:
                try:
                    self.__battle_token, self.__containers, textlog = self.__refresh_page_and_parse()

                    # 如果战斗记录没有更新，说明请求没发出去，再发一次
                    if self.logs[-1] == textlog:
                        print(f"[BattleAPI.__do_action] 请求没有发送到服务器，再发一次 ...")
                        continue

                    # 如果战斗记录包含 "Initializing" 而且之前存在过其他战斗记录，说明这是一个新的、不同于之前的战斗。原因是服务器的确已经收到了请求，并i企鹅这个请求终结了所有怪兽、结束了战斗，但是客户端接收这个消息时发生了网络错误
                    if len(self.logs) > 1 and any("Initializing" in log for log in textlog):
                        print("[BattleAPI.__do_action] 服务器已经开启新的战斗")
                        self.battle_result = BattleResult.VICTORY
                except TokenNotFoundError:
                    # 如果没有找到 Token，说明本场战斗已经结束，但是由于未接收到服务器的响应，无法得知战斗结果
                    self.battle_result = BattleResult.UNCONFIRMED
            break

        # 仅在还处于本场战斗时更新
        if textlog:
            # 添加新的战斗记录
            self.logs.append(textlog)

            # 更新怪兽信息
            self.__update_monster_helath(textlog)
            self.__update_monster_info()

            # 更新战斗结果
            if any(log == "You are Victorious!" for log in textlog):
                self.battle_result = BattleResult.VICTORY
            if any(log == "You have been defeated." for log in textlog):
                self.battle_result = BattleResult.DEFEAT

        # 执行钩子
        for callback in self.__post_action_hooks:
            callback(self, textlog)

        # 返回原始战斗记录
        return textlog

    def __get_player_vital(self, label_id: str) -> int | None:
        # 游戏提供两种 UI: Standard 和 Utilitarian。它们的标签有不同的 ID 前缀
        for prefix in ["", "d"]:
            if (label := self.__containers["pane_vitals"].find(id=f"{prefix}{label_id}")):
                return int(label.text)

    def __update_monster_helath(self, textlog: list[str]):
        # 解析怪兽受到的伤害，并相应地更新怪兽的生命值
        # 你问我为什么不直接从 pane_monsters 拿？只能拿得到比例啊！
        monster_name_to_idx = {monster.name: i for i, monster in enumerate(self.monsters)}
        for log in textlog:
            monster_name = None
            # Persistent 伤害日志格式: $skill_name $effect(全小写字母且动词第三人称单数形式) $monster_name(怪兽名字复杂多变) for $damage ($damage_type[SPACE])?damage
            if (res := BattleAPI.parse_damage(log)) is not None:
                monster_name = res.monster_name
                damage = res.damage

            # 更新怪兽生命值
            if monster_name in monster_name_to_idx:
                monster = self.monsters[monster_name_to_idx[monster_name]]
                monster.health = int(max(monster.health - damage, 0))

    def __update_monster_info(self):
        # 每次获取的时候，先更新一下状态
        for monster, monster_element in zip(self.monsters, self.__containers["pane_monster"].find_all(class_="btm1")):
            # 注意: 血量、蓝量、Spirit 量条显示的都是比例缩放的值，比如满了就是 120px，一半就是 60px，非常不靠谱
            for attr, alt in [("mana", "magic"), ("spirit", "spirit")]:
                result = monster_element.find(alt=alt)
                if result is None:
                    continue
                value = int(re.search(r"width:(\d+)px", result.attrs["style"]).group(1))
                setattr(monster, attr, value)

            # 有时候服务器返回日志说效果造成了什么伤害，但就是不说怪兽名，比如 "Freezing Limbs explodes for 253 cold damage"
            # 这样就会造成怪兽生命值偏大（我有什么办法）。等到怪兽死亡时，我们就手动置零吧
            if "onclick" not in monster_element.attrs:
                monster.health = 0

            # 怪兽也有 Buff
            monster.effects = [BattleAPI.parse_effect(effect_element.attrs["onmouseover"]) for effect_element in monster_element.find(class_="btm6").find_all("img")]

    def __refresh_page_and_parse(self) -> tuple[str, dict[str, Tag], list[str]]:
        # 获取战斗界面
        page = request_with_retry(requests.get, MAIN_URL, **self.__request_kwargs).text

        # 获取 battle_token
        if (result := re.search('var battle_token = "([^"]+)"', page)) is None:
            raise TokenNotFoundError(page)
        battle_token, = result.groups()

        # 解析页面
        soup = BeautifulSoup(page, "lxml")
        containers = {container_id: soup.find(id=container_id) for container_id in self.__containers}

        # 获取最新战斗记录。网页里为了方便查看最新信息，记录是从新到旧排序的，也就是旧的在上边、新的在下边。我们得反转回来
        logs = [x.text for x in reversed(soup.find(id="textlog").find_all("tr"))]
        return battle_token, containers, logs

    @staticmethod
    def parse_effect(effect_str: str) -> Effect:
        name, description, remaining_turns = re.search(r"battle\.set_infopane_effect\('([^']+)',\s*'([^']+)',\s*(\d+)\)", effect_str).groups()
        return Effect(name=name, description=description, remaining_turns=remaining_turns)

    @staticmethod
    def parse_damage(log: str) -> DamageLog | None:
        if (res := re.search(r"([\w ]+) ([a-z]+)s ([\w\W]+) for (\d+) (\w+)? ?damage", log)) is not None:
            skill_name, action, monster_name, damage, attribute = res.groups()
            return DamageLog(skill_name=skill_name, action=action, monster_name=monster_name, damage=damage, attribute=attribute)

    def use_magic(self, magic: Magic, target: int) -> list[str]:
        return self.__do_action({"mode": "magic", "target": target, "skill": magic.skill_id})

    def use_item(self, item: Item) -> list[str]:
        return self.__do_action({"mode": "items", "target": 0, "skill": item.skill_id})

    def do_defend(self) -> list[str]:
        return self.__do_action({"mode": "defend", "target": 0, "skill": 0})

    def do_attack(self, target: int) -> list[str]:
        return self.__do_action({"mode": "attack", "target": target, "skill": 0})

    def get_player_health(self) -> int:
        # 在 Standard UI 下，血量显示在血量条中间，当血量过少时，血量条过短，就不会显示血量，这时候我们当作 1 血处理
        if (health := self.__get_player_vital("vrhb")) is not None:
            return health
        return 1

    def get_player_mana(self) -> int:
        # 无论是在任何模式，蓝量和 Spirit 都是显示在条外的，所以即使为零也会显示，不需要特殊处理
        return self.__get_player_vital("vrm")

    def get_player_spirit(self) -> int:
        return self.__get_player_vital("vrs")

    def get_player_effects(self) -> list[Effect]:
        return [BattleAPI.parse_effect(effect_element.attrs["onmouseover"]) for effect_element in self.__containers["pane_effects"].find_all("img")]

    def get_player_magics(self) -> list[Magic]:
        current_category: str
        magic_skills = []

        for row in self.__containers["table_magic"].find_all("tr"):
            if (category_img := row.find("img")) is not None:
                current_category = category_img.attrs["alt"]
                continue
            # 你知道吗，每一行都有 [1, 2] 个魔法，这混乱程度……
            for magic_element in row.find_all(class_="btsd"):
                name, description, mana_cost, cooldown = re.search(r"battle\.set_infopane_spell\('([^']+)', '([^']+)', '\w+', (\d+), \d+, (\d+)\)", magic_element.attrs["onmouseover"]).groups()
                skill_id = magic_element.attrs["id"]
                available = "onclick" in magic_element.attrs
                magic_skills.append(Magic(name=name, available=available, skill_id=skill_id, description=description, mana_cost=mana_cost, cooldown=cooldown, category=current_category))
        return magic_skills

    def get_player_items(self) -> list[Item]:
        items = []
        for child in self.__containers["pane_item"].find_all(class_="bti1"):
            if (item_element := child.find(class_="bti3").find("div")) is None:
                continue
            name = item_element.text
            available = "onclick" in item_element.attrs
            skill_id = None
            if available:
                skill_id, = re.search(r"battle\.set_friendly_skill\('([^']+)'\)", item_element.attrs["onclick"]).groups()
            items.append(Item(name=name, available=available, skill_id=skill_id))
        return items

    def add_post_action_hook(self, callback: Callable[["BattleAPI", list[str]], Any]):
        self.__post_action_hooks.append(callback)
