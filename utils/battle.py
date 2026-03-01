# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import re
import requests
from bs4 import BeautifulSoup
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

        # 获取战斗界面
        page = request_with_retry(requests.get, MAIN_URL, **self.__request_kwargs).text

        # 获取 battle_token
        result = re.search('var battle_token = "([^"]+)"', page)
        if result is None:
            raise TokenNotFoundError(page)
        self.__battle_token, = result.groups()

        # 解析获取各个容器的 soup
        soup = BeautifulSoup(page, "lxml")
        self.__soups = {container_id: soup.find(id=container_id) for container_id in ["pane_vitals", "pane_effects", "pane_monster", "pane_item", "table_magic"]}

        # 初始化日志，格式: 每次更新的日志列表
        # 比如 logs[0][0] 表示初始日志的第一条
        self.logs = [[x.text for x in soup.find(id="textlog").find_all("tr")]]

        # 解析获取怪兽信息
        monsters_info = sorted(
            re.findall(r"Spawned Monster ([A-Z]): MID=([0-9]+) \(([^)]+)\) LV=(\d+) HP=(\d+)", page),
            key=lambda x: x[0]  # 按照怪物出场的顺序排序，A 最先出场，B 第二个出场，以此类推
        )
        self.__monsters = [Monster(name=name, monster_id=monster_id, level=level, health=health) for _, monster_id, name, level, health in monsters_info]

    def __do_action(self, action: dict[str, int | str]) -> list[str]:
        # 补充信息、执行动作
        action |= {"type": "battle", "method": "action", "token": self.__battle_token}
        resp_json = request_with_retry(requests.post, f"{MAIN_URL}/json", json=action, **self.__request_kwargs).json()

        # 更新各个 soup
        for container_id in self.__soups:
            if container_id in resp_json:
                self.__soups[container_id] = BeautifulSoup(resp_json[container_id], "lxml")

        # 添加日志到本地记录
        textlog = [log["t"] for log in resp_json["textlog"]]
        self.logs.append(textlog)

        # 解析怪兽受到的伤害，并相应地更新怪兽的生命值。你问我为什么不直接从 pane_monsters 拿？只能拿得到比例啊！
        monster_name_to_idx = {monster.name: i for i, monster in enumerate(self.get_monsters())}
        for log in textlog:
            # Persistent 格式: $skill_name $effect(全小写字母且动词第三人称单数形式) $monster_name(怪兽名字复杂多变) for $damage ($damage_type[SPACE])?damage
            if (res := re.search(r"[\w ]+ [a-z]+s ([\w\W]+) for (\d+) (\w+ )?damage", log)) is not None:
                monster_name, damage, _ = res.groups()
                # 更新怪兽的生命值
                if monster_name in monster_name_to_idx:
                    monster = self.__monsters[monster_name_to_idx[monster_name]]
                    monster.health = max(monster.health - int(damage), 0)

        # 返回原始战斗记录
        return textlog

    @staticmethod
    def parse_effect(effect_str: str) -> Effect:
        name, description, remaining_turns = re.search(r"battle\.set_infopane_effect\('([^']+)',\s*'([^']+)',\s*(\d+)\)", effect_str).groups()
        return Effect(name=name, description=description, remaining_turns=remaining_turns)

    def use_magic(self, magic: Magic, target: int) -> list[str]:
        return self.__do_action({"mode": "magic", "target": target, "skill": magic.skill_id})

    def use_item(self, item: Item) -> list[str]:
        return self.__do_action({"mode": "items", "target": 0, "skill": item.skill_id})

    def do_defend(self) -> list[str]:
        return self.__do_action({"mode": "defend", "target": 0, "skill": 0})

    def do_attack(self, target: int) -> list[str]:
        return self.__do_action({"mode": "attack", "target": target, "skill": 0})

    def get_monsters(self) -> list[Monster]:
        # 每次获取的时候，先更新一下状态
        for monster, monster_element in zip(self.__monsters, self.__soups["pane_monster"].find_all(class_="btm1")):
            # 注意: 在血量、蓝量、Spirit 量条显示的都是比例缩放的值
            # 比如满了就是 120px，一半就是 60px，非常不靠谱。既然 pane_vitals 已经包含血量了，我们就不从 pane 获取了
            for attr, alt in [("mana", "magic"), ("spirit", "spirit")]:
                result = monster_element.find(alt=alt)
                if result is None:
                    continue
                value = int(re.search(r"width:(\d+)px", result.attrs["style"]).group(1))
                setattr(monster, attr, value)

            # 怪兽也有 Buff
            monster.effects = [BattleAPI.parse_effect(effect_element.attrs["onmouseover"]) for effect_element in monster_element.find(class_="btm6").find_all("img")]
        return self.__monsters

    def get_player_health(self) -> int:
        if (health_bar := self.__soups["pane_vitals"].find(id="vrhb")):
            return int(health_bar.text)
        # 血量显示在血量条中间，当血量过少时，血量条过短，就不会显示血量，这时候我们当作 1 血处理
        return 1

    def get_player_mana(self) -> int:
        # 蓝量和 Spirit 都是显示在条外的，所以即使为零也会显示，不需要特殊处理
        return int(self.__soups["pane_vitals"].find(id="vrm").text)

    def get_player_spirit(self) -> int:
        return int(self.__soups["pane_vitals"].find(id="vrs").text)

    def get_player_effects(self) -> list[Effect]:
        return [BattleAPI.parse_effect(effect_element.attrs["onmouseover"]) for effect_element in self.__soups["pane_effects"].find_all("img")]

    def get_player_magics(self) -> list[Magic]:
        current_category: str
        magic_skills = []

        for row in self.__soups["table_magic"].find_all("tr"):
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
        for child in self.__soups["pane_item"].find_all(class_="bti1"):
            if (item_element := child.find(class_="bti3").find("div")) is None:
                continue
            name = item_element.text
            available = "onclick" in item_element.attrs
            skill_id = None
            if available:
                skill_id, = re.search(r"battle\.set_friendly_skill\('([^']+)'\)", item_element.attrs["onclick"]).groups()
            items.append(Item(name=name, available=available, skill_id=skill_id))
        return items
