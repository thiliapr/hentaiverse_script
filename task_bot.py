# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: thiliapr/hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

# 本文件是 thiliapr/hentaiverse_script 的一部分
# thiliapr/hentaiverse_script 是自由软件，你可以依照由自由软件基金会发布的 GNU Affero 通用公共许可证分发或修改它，无论是版本 3 许可证，还是（按你的决定）任何以后版都可以。
# 发布 thiliapr/hentaiverse_script 是希望它能有用，但是并无保障，甚至连可销售和符合某个特定的目的都不保证。请参看 GNU Affero 通用公共许可证以了解详情。
# 你应该随程序获得一份 GNU Affero 通用公共许可证的复本。如果没有，请看 <https://www.gnu.org/licenses/agpl.html>。

import random, json, pathlib, time, re, requests
from functools import partial
from abc import ABC, abstractmethod
from typing import Any
from collections.abc import Callable
from tqdm import tqdm
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from utils.constants import MAIN_URL
from utils.network import request_with_retry
from utils.battle import BattleResult, TokenNotFoundError
from battle_bot import BattleWithRiddleAI, RiddleAIConfig, AuthenticationConfig


# Bot 配置
class MarketBotConfig(BaseModel):
    wanted_items: list[str] = Field(..., description="想要保留的物品名称")
    skipped_filters: list[str] = Field(..., description="想要跳过的过滤器（分类）")


class EquipmentStoreBotConfig(BaseModel):
    skipped_filters: list[str] = Field(..., description="想要跳过的过滤器（分类）")
    skipped_qualities: list[str] = Field(..., description="想要跳过的装备品质")


class BattleConfig(BaseModel):
    difficult_level: str = Field(..., description="战斗难度等级，如果为 0 则不进行战斗")
    epsilon: float = Field(..., description="攻击动作的探索率")
    battle_bot_override: dict[str, Any] = Field(..., description="该类事件的特定的 BattleBot 配置，覆盖默认配置")


class BaseTaskBotConfig(BaseModel):
    enabled: bool = Field(..., description="是否启用该 Bot")
    market_bot: MarketBotConfig = Field(..., description="市场变卖物品的配置")
    equipment_store_bot: EquipmentStoreBotConfig = Field(..., description="装备商店的配置")
    battle: dict[str, BattleConfig] = Field(..., description="各类战斗事件的配置")


class PersistentBotConfig(BaseTaskBotConfig):
    training_henjutsu: list[str] = Field(..., description="要训练的 Henjutsu 名称")


class IsekaiBotConfig(BaseTaskBotConfig):
    pass


# 各个世界的 Bot
class BaseBot(ABC):
    def init(self, isekai: bool, config: dict[str, Any], force: bool = False):
        self.enabled = config["task_bot"]["enabled"]
        if not self.enabled and not force:
            return

        self.main_url = f"{MAIN_URL}/{'isekai' if isekai else ''}"
        self.config = (IsekaiBotConfig if isekai else PersistentBotConfig).model_validate(config["task_bot"])
        self.battle_with_riddle_ai = BattleWithRiddleAI(
            isekai,
            RiddleAIConfig.model_validate(config["riddle_ai"]),
            AuthenticationConfig.model_validate(config["authentication"]),
        )
        self.request_kwargs = {"cookies": {"ipb_member_id": config["authentication"]["ipb_member_id"], "ipb_pass_hash": config["authentication"]["ipb_pass_hash"]}, "headers": {"User-Agent": config["authentication"]["user_agent"]}}

    # 工具方法
    def api_request(self, *args, **kwargs):
        return request_with_retry(*args, **kwargs, **self.request_kwargs)

    def get_arena_list(self, url: str) -> tuple[int, list[tuple[dict[str, str | int | float], Callable[[], Any]]]]:
        url = f"{self.main_url}/{url}"

        # 获取网页并解析体力值
        page = self.api_request(requests.get, url).text
        stamina = int(re.search(r"Stamina: (\d+)", page).group(1))

        # 解析 Arena 列表，并获取每个战斗的 API 信息
        soup = BeautifulSoup(page, "lxml")
        table_header = [label.text for label in soup.find(id="arena_list").find("tr").find_all("th")]
        arena_list = []
        for arena in soup.find(id="arena_list").find_all("tr")[1:]:
            info = arena.find_all("td")
            if not (start_button := info[-1].find("img")) or "onclick" not in start_button.attrs:
                continue

            # 获取开启战斗的函数
            initid = re.search(r"init_battle\((\d+),\d+\)", start_button.attrs["onclick"]).group(1)
            postoken = soup.find("input", attrs={"name": "postoken"}).attrs["value"]
            battle_func = partial(self.api_request, requests.post, url, data={"initid": initid, "postoken": postoken})

            # 获取其他战斗信息
            battle_info = {label: info[idx].text for idx, label in enumerate(table_header) if label}
            battle_info["Min Level"] = int(battle_info["Min Level"].removeprefix("Lv. "))
            battle_info["Rounds"] = int(battle_info["Rounds"])
            battle_info["EXP Mod"] = float(battle_info["EXP Mod"].removeprefix("X"))
            battle_info["Entry Cost"] = 0 if battle_info["Entry Cost"] == "-" else int(battle_info["Entry Cost"].split(" ")[0])
            battle_info["Clear Bonus"] = int(battle_info["Clear Bonus"].replace(",", "").removesuffix(" C"))
            arena_list.append((battle_info, battle_func))

        return stamina, arena_list

    # 自动化任务
    def repair_equipment(self) -> bool:
        url = f"{self.main_url}/?s=Bazaar&ss=am&screen=repair&filter=equipped"
        soup = BeautifulSoup(self.api_request(requests.get, url).text, "lxml")

        # 如果存在至少一个装备损坏，就修复装备
        equipments = []
        for equipment in soup.find(id="equiplist").find_all("tr", onclick=True):
            equipment_id = re.search(r"hover_equip\((\d+)\)", equipment.attrs["onmouseover"]).group(1)
            equipments.append(equipment_id)
        if not equipments:
            return False
        
        # 网络请求
        postoken = soup.find("input", attrs={"name": "postoken"}).attrs["value"]
        self.api_request(requests.post, url, data={"postoken": postoken, "eqids[]": equipments, "replace_charms": "on"})
        return True

    def equipment_store_bot(self) -> int:
        # 获取装备商店主页
        soup = BeautifulSoup(self.api_request(requests.get, f"{self.main_url}/?s=Bazaar&ss=am&screen=sell").text, "lxml")
        
        # 卖掉各个过滤器下的物品
        filters = [
            filter_element.attrs["href"]
            for filter_element in soup.find(id="filterbar").find_all("a", href=True)
            if filter_element.text not in self.config.equipment_store_bot.skipped_filters
        ]
        equipments_sold = 0
        for href in tqdm(filters, desc="Sell Equipments"):
            soup = BeautifulSoup(self.api_request(requests.get, href).text, "lxml")

            # 遍历每一个物品
            equipments = []
            for equipment in soup.find(id="equiplist").find_all("tr", onclick=True):
                if any(quality in equipment.text for quality in self.config.equipment_store_bot.skipped_qualities):
                    continue
                if equipment.attrs.get("data-eqprotect") == "1":
                    continue
                equipments.append(re.search(r"hover_equip\((\d+)\)", equipment.attrs["onmouseover"]).group(1))

            # 卖出物品
            if not equipments:
                continue
            storetoken = soup.find("input", attrs={"name": "postoken"}).attrs["value"]
            self.api_request(requests.post, href, data={"postoken": storetoken, "eqids[]": equipments})
            equipments_sold += len(equipments)

        return equipments_sold

    def attribute_point_allocation(self) -> int:
        # https://ehwiki.org/wiki/Character_Stats#Primary_Attributes
        url = f"{self.main_url}/?s=Character&ss=ch"
        attributes = ["str", "dex", "agi", "end", "int", "wis"]
        total_attributes_allocated = 0

        while True:
            soup = BeautifulSoup(self.api_request(requests.get, url).text, "lxml")
            remaining_exp = int(soup.find(id="remaining_exp").text.replace(",", ""))
            required_exp = {attr: int(soup.find(id=f"{attr}_left").text.replace(",", "")) for attr in attributes}

            # 均衡加点
            attr_delta = {attr: 0 for attr in attributes}
            for attr in sorted(attributes, key=lambda attr: required_exp[attr]):
                if required_exp[attr] <= remaining_exp:
                    attr_delta[attr] = 1
                    remaining_exp -= required_exp[attr]

            # 发送请求
            if (attributes_allocated := sum(v > 0 for v in attr_delta.values())) == 0:
                break
            total_attributes_allocated += attributes_allocated
            self.api_request(requests.post, url, data={"attr_apply": "1"} | {f"{attr}_delta": str(delta) for attr, delta in attr_delta.items()})
            if all(remaining_exp < exp for exp in required_exp.values()):
                break

        return total_attributes_allocated

    def market_bot(self) -> tuple[int, list[str]]:
        # 获取市场主页
        soup = BeautifulSoup(self.api_request(requests.get, f"{self.main_url}/?s=Bazaar&ss=mk").text, "lxml")

        # 查看各个过滤器下的物品
        filters = [
            (filter_element.text, filter_element.attrs["href"])
            for filter_element in soup.find(id="filterbar").find_all("a", href=True)
            if filter_element.text not in self.config.market_bot.skipped_filters
        ]
        items_to_sell = []
        for filter_name, href in tqdm(filters, desc="Fetch Market's Itemlist"):
            soup = BeautifulSoup(self.api_request(requests.get, href).text, "lxml")

            # 遍历每一个物品
            for item_element in soup.find(id="market_itemlist").find_all("tr", onclick=True):
                item_name, your_stock = [ele.text for ele in item_element.find_all("td")[:2]]
                if item_name in self.config.market_bot.wanted_items:
                    continue
                if your_stock == "":
                    continue
                items_to_sell.append((f"{filter_name}/{item_name}", re.search(r"document\.location='([^']+)'", item_element.attrs["onclick"]).group(1)))

        # 卖出物品
        items_sold = []
        for item_id, href in tqdm(items_to_sell, desc="Sell Items"):
            soup = BeautifulSoup(self.api_request(requests.get, href).text, "lxml")
            
            # Buy Order List 是按照价格高到低（利润大到小）排序的，对于 Seller 来说，最上面的 Order 是最值的
            if (best_order := soup.find(id="market_itembuy").find(class_="market_itemorders").find("tr", onclick=True)) is None:
                continue

            # 提取数据
            sell_price, count = re.search(r"autofill_from_buy_order\(\d+,(\d+),(\d+)\)", best_order.attrs["onclick"]).groups()
            if count == "0":
                continue
            marketoken, update_value = [soup.find("input", attrs={"name": name}).attrs["value"] for name in ["marketoken", "sellorder_update"]]

            # 卖出物品，并加入成功列表，计算赚的 Credits
            self.api_request(requests.post, href, data={"marketoken": marketoken, "sellorder_batchcount": count, "sellorder_batchprice": sell_price, "sellorder_update": update_value})
            items_sold.append(item_id)

        # 转移市场 Credit 到账户里
        soup = BeautifulSoup(self.api_request(requests.get, f"{self.main_url}/?s=Bazaar&ss=mk").text, "lxml")
        market_balance = re.search(r"\.value=(\d+)", soup.find_all(class_="credit_balance")[1].attrs["onclick"]).group(1)
        marketoken, action_value = [soup.find("input", attrs={"name": name}).attrs["value"] for name in ["marketoken", "account_withdraw"]]
        if market_balance != "0":
            self.api_request(requests.post, f"{self.main_url}/?s=Bazaar&ss=mk", data={"marketoken": marketoken, "account_amount": market_balance, "account_withdraw": action_value})

        return int(market_balance), items_sold

    def get_settings(self) -> tuple[BeautifulSoup, dict[str, str]]:
        # 获取并解析设置页面
        url = f"{self.main_url}/?s=Character&ss=se"
        soup = BeautifulSoup(self.api_request(requests.get, url).text, "lxml")

        # 获取原有设置
        params = {}
        for input_element in soup.find_all("input"):
            # 无名氏是没准备加入表单的，跳过
            if (name := input_element.attrs.get("name")) is None:
                continue

            # 单选或复选框没选的跳过
            if input_element.attrs.get("type") in ["checkbox", "radio"]:
                if input_element.attrs.get("checked") is None:
                    continue
                # 单选框可能没有 value 属性，默认值为 "on"
                params[name] = "on"

            # 如果有 value 属性就加入表单
            if (value := input_element.attrs.get("value")) is not None:
                params[name] = value
        return soup, params

    def update_settings(self, params):
        # 更新设置
        url = f"{self.main_url}/?s=Character&ss=se"
        self.api_request(requests.post, url, data=params)

    def arena(self) -> Callable[[], Any] | None:
        stamina, arena_list = self.get_arena_list("?s=Battle&ss=ar")
        if stamina < 80:
            return
        if arena_list:
            return arena_list[-1][1]

    def ring_of_blood(self) -> Callable[[], Any] | None:
        _, arena_list = self.get_arena_list("?s=Battle&ss=rb")
        for battle_info, battle_func in arena_list:
            if battle_info["Entry Cost"] > 1:
                continue
            return battle_func

    @abstractmethod
    def task(self) -> tuple[str, BattleResult] | None:
        pass


class PersistentBot(BaseBot):
    def __init__(self, *args, **kwargs):
        config = json.loads(pathlib.Path("world/persistent/config.json").read_text("utf-8"))
        self.init(False, config, *args, **kwargs)
        self.encounter_cookies = {}

    # 自动化任务
    def train_henjutsu(self) -> str | None:
        url = f"{self.main_url}/?s=Character&ss=tr"
        soup = BeautifulSoup(self.api_request(requests.get, url).text, "lxml")
        for subject in soup.find(id="train_table").find_all("tr"):
            # 跳过表头
            info_elements = subject.find_all("td")
            if not info_elements:
                continue

            # 根据名字筛选
            if (henjutsu_name := info_elements[0].text) not in self.config.training_henjutsu:
                continue

            # 如果无法训练（比如还在训练，或者 Credits 不够），看看下一个的情况
            if "onclick" not in (train_button := info_elements[-1].find("img")).attrs:
                continue
            
            # 开始训练
            subject_id, = re.search(r"training.start_training\((\d+)\)", train_button.attrs["onclick"]).groups()
            self.api_request(requests.post, url, data={"start_train": subject_id, "cancel_train": "0"})
            return henjutsu_name

    def settings_for_task(self, difficult_level: str):
        # 获取设置页面和原有设置
        soup, params = self.get_settings()

        # 选择最佳称号（最后一个效果最好）和最佳 UI (Utilitarian)
        title_override = soup.find(id="settings_title").find_all("tr")[-1].find("input", {"name": "title_override"}).attrs["value"]
        vitalstyle = "d"

        # 更改设置
        self.update_settings(params | {"difflevel": difficult_level, "title_override": title_override, "vitalstyle": vitalstyle})

    def monster_lab_bot(self) -> tuple[bool, int]:
        url = f"{self.main_url}/?s=Bazaar&ss=ml"
        soup = BeautifulSoup(self.api_request(requests.get, url).text, "lxml")

        # 喂饱肚子
        if feed := soup.find(onclick="do_feed_all('food')") is not None:
            self.api_request(requests.post, url, data={"feed_all": "food"})

        # 给属性加点
        attrs_upgraded = 0
        for slot in soup.find(id="slot_pane").find_all(class_="msl"):
            href = re.search(r"document\.location='([^']+)'", slot.attrs["onclick"]).group(1)
            soup = BeautifulSoup(self.api_request(requests.get, href).text, "lxml")

            # 寻找可加点属性
            for attr_group in soup.find(id="monsterstats_top").find_all(class_="mcr"):
                for attr in attr_group.find_all("tr"):
                    if "onclick" not in (upgrade_button := attr.find("img")).attrs:
                        continue
                    
                    # 加点、记录
                    attr_name = re.search(r"do_crystal_upgrade\('(\w+)', event\)", upgrade_button.attrs["onclick"]).group(1)
                    self.api_request(requests.post, href, data={"crystal_upgrade": attr_name, "crystal_count": "1"})
                    attrs_upgraded += 1

        return feed, attrs_upgraded

    def encounter(self) -> Callable[[], Any] | None:
        # Random Encounter is a single-round battle that places players against common foes in order to get a lot credits and EXP.
        # https://ehwiki.org/wiki/Random_Encounter
        cookies = self.request_kwargs["cookies"]
        headers = self.request_kwargs["headers"]

        # 发送网页请求
        if "event" not in self.encounter_cookies:
            self.encounter_cookies["event"] = "1"
        resp = request_with_retry(requests.get, "https://e-hentai.org/news.php", headers=headers, cookies=cookies | self.encounter_cookies)
        self.encounter_cookies.update(dict(resp.cookies))

        # 解析检测随机遇敌事件，并点击遇敌链接
        soup = BeautifulSoup(resp.text, "lxml")
        if (eventpane := soup.find(id="eventpane")) is None:
            return
        if (link_element := eventpane.find("a", href=True)) is None:
            return

        battle_func = partial(self.api_request, requests.get, link_element.attrs["href"])
        return battle_func

    def task(self) -> tuple[str, tuple[str, BattleResult]] | None:
        if self.config.training_henjutsu:
            print(f"[task_bot.PersistentBot.task] [TrainHenjutsu] 尝试训练 Henjutsu ...")
            if henjutsu_trained := self.train_henjutsu():
                print(f"[task_bot.PersistentBot.task] [TrainHenjutsu] 成功开始训练 {henjutsu_trained}")

        print("[task_bot.PersistentBot.task] [LookForBattle] 检测战斗事件 ...")
        for event_type, func in [("Random Encounter", self.encounter), ("Arena", self.arena), ("Ring of Blood", self.ring_of_blood)]:
            if (battle_config := self.config.battle[event_type]).difficult_level == "0":
                continue
            if battle_func := func():
                break
        else:
            return

        print(f"[task_bot.PersistentBot.task] [{event_type}] [RepairEquipment] 检测装备损坏 ...")
        if self.repair_equipment():
            print(f"[task_bot.PersistentBot.task] [{event_type}] [RepairEquipment] 已修复所有装备")

        print(f"[task_bot.PersistentBot.task] [{event_type}] [AllocateAttribute] 尝试加点 ...")
        if attr_allocated := self.attribute_point_allocation():
            print(f"[task_bot.PersistentBot.task] [{event_type}] [AllocateAttribute] 已加 {attr_allocated} 个属性点")

        # 打印当前战斗事件，并设置难度
        print(f"[task_bot.PersistentBot.task] [{event_type}] [SettingDifficultLevel] 设置难度等级为 {battle_config.difficult_level} ...")
        self.settings_for_task(battle_config.difficult_level)
        print(f"[task_bot.PersistentBot.task] [{event_type}] [Battle] 开始战斗 ...")
        battle_func()

        try:
            while True:
                battle_result = self.battle_with_riddle_ai.battle(False, battle_config.epsilon, battle_config.difficult_level, battle_config.battle_bot_override)
        except TokenNotFoundError:
            # 找不到 BattleToken，可能意味着遇到小马谜题，或者战斗结束。由于小马谜题在 battle 内已经解决，所以现在只可能是战斗结束
            pass

        print(f"[task_bot.PersistentBot.task] [MarketBot] 在市场变卖物品 ...")
        market_balance, items_sold = self.market_bot()
        if market_balance:
            print(f"[task_bot.PersistentBot.task] [MarketBot] 入账 {market_balance} Credits; 变卖了的物品: {items_sold}")

        print(f"[task_bot.PersistentBot.task] [EquipmentStoreBot] 变卖装备 ...")
        if equipments_sold := self.equipment_store_bot():
            print(f"[task_bot.PersistentBot.task] [EquipmentStoreBot] 变卖了 {equipments_sold} 件装备")

        print(f"[task_bot.PersistentBot.task] [MonsterLabBot] 检测 MonsterLab 情况 ...")
        if any(result := self.monster_lab_bot()):
            feed, attrs_upgraded = result
            print(f"[task_bot.PersistentBot.task] [MonsterLabBot] {feed=}, {attrs_upgraded=}")

        return event_type, battle_result


class IsekaiBot(BaseBot):
    def __init__(self, *args, **kwargs):
        config = json.loads(pathlib.Path("world/isekai/config.json").read_text("utf-8"))
        self.init(True, config, *args, **kwargs)

    # 自动化任务
    def settings_for_task(self, difficult_level: str):
        # 获取设置页面和原有设置
        _, params = self.get_settings()

        # 选择最佳 UI (Utilitarian)
        vitalstyle = "d"

        # 更改设置
        self.update_settings(params | {"difflevel": difficult_level, "vitalstyle": vitalstyle})

    def task(self) -> tuple[str, BattleResult] | None:
        print("[task_bot.IsekaiBot.task] [LookForBattle] 检测战斗事件 ...")
        for event_type, func in [("Arena", self.arena), ("Ring of Blood", self.ring_of_blood)]:
            if self.config.battle[event_type].difficult_level == "0":
                continue
            if battle_func := func():
                battle_config = self.config.battle[event_type]
                break
        else:
            return

        print(f"[task_bot.IsekaiBot.task] [RepairEquipment] 检测装备损坏 ...")
        if self.repair_equipment():
            print(f"[task_bot.IsekaiBot.task] [RepairEquipment] 已修复所有装备")

        print(f"[task_bot.IsekaiBot.task] [AllocateAttribute] 尝试加点 ...")
        if attr_allocated := self.attribute_point_allocation():
            print(f"[task_bot.IsekaiBot.task] [AllocateAttribute] 已加 {attr_allocated} 个属性点")

        battle_func()
        try:
            while True:
                battle_result = self.battle_with_riddle_ai.battle(True, battle_config.epsilon, "default", battle_config.battle_bot_override)
        except TokenNotFoundError:
            # 找不到 BattleToken，可能意味着遇到小马谜题，或者战斗结束。由于小马谜题在 battle 内已经解决，所以现在只可能是战斗结束
            pass

        print(f"[task_bot.IsekaiBot.task] [MarketBot] 在市场变卖物品 ...")
        market_balance, items_sold = self.market_bot()
        if market_balance:
            print(f"[task_bot.IsekaiBot.task] [MarketBot] 入账 {market_balance} Credits; 变卖了的物品: {items_sold}")

        print(f"[task_bot.IsekaiBot.task] [EquipmentStoreBot] 变卖装备 ...")
        if equipments_sold := self.equipment_store_bot():
            print(f"[task_bot.IsekaiBot.task] [EquipmentStoreBot] 变卖了 {equipments_sold} 件装备")

        return event_type, battle_result


def log_battle_result(world: str, result: tuple[str, BattleResult]):
    # 获取战斗事件类型和结果，定义事件 ID
    event_type, battle_result = result
    event_id = f"{world}:{event_type}"

    # 加载日志，如果没有就初始化为空
    task_log = {}
    if (task_log_path := pathlib.Path(f"world/task_log.json")).exists():
        task_log = json.loads(task_log_path.read_text("utf-8"))

    # 事件对应的计数加一
    log = task_log.setdefault("battle_result", {}).setdefault(event_id, {})
    log[battle_result.name] = log.get(battle_result.name, 0) + 1

    # 将更新后的日志写入本地
    task_log_path.write_text(json.dumps(task_log, indent="\t"), "utf-8")


def main():
    # 显示版权声明、无担保说明、许可证信息和查看方式
    print("[task_bot.main] [Info] task_bot - HentaiVerse 战斗、市场、训练的自动化脚本")
    print("[task_bot.main] [Info] Copyright (C) 2026 thiliapr <thiliapr@tutanota.com>")
    print("[task_bot.main] [Info] 本脚本是 thiliapr/hentaiverse 的一部分，是一个自由软件，遵循 GNU AGPL v3 or later 进行分发")
    print("[task_bot.main] [Info] thiliapr/hentaiverse_script 不提供任何保障，甚至连可销售和符合某个特定的目的都不保证")
    print("[task_bot.main] [Info] 您应该已收到一份 AGPL 副本。如果没有，请访问 https://www.gnu.org/licenses/agpl.html")
    print()

    # 创建 Bot 实例
    persistent_bot = PersistentBot()
    isekai_bot = IsekaiBot()

    # 调整字体
    print("[task_bot.main] [SetFont] 调整字体 ...")
    for world, bot in [("Persistent", persistent_bot), ("Isekai", isekai_bot)]:
        if not bot.enabled:
            continue
        _, params = bot.get_settings()
        bot.update_settings(params | {"fontlocal": "on"})

    while True:
        for world, bot in [("Persistent", persistent_bot), ("Isekai", isekai_bot)]:
            # 跳过未启用的 Bot
            if not bot.enabled:
                continue
            # 成功进行战斗后，记录战斗结果
            if result := bot.task():
                log_battle_result(world, result)
        else:
            # Random Encounter event can occur once every 30 minutes upon visitation of the E-Hentai news page or a gallery
            for _ in tqdm(range(random.randint(1800, 1830)), desc="Wait"):
                time.sleep(1)
            continue


if __name__ == "__main__":
    main()
