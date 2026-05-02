# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import random, json, pathlib, time, re, requests
from functools import partial
from abc import ABC, abstractmethod
from typing import Any
from collections.abc import Callable
from tqdm import tqdm
from bs4 import BeautifulSoup
from utils.constants import MAIN_URL
from utils.network import request_with_retry
from utils.battle import BattleResult, TokenNotFoundError
from battle_bot import BattleWithRiddleAI, RiddleAIConfig


class BaseBot(ABC):
    def init(self, config: dict[str, Any], main_url: str):
        self.main_url = main_url
        self.config = config
        self.battle_with_riddle_ai = BattleWithRiddleAI(RiddleAIConfig.model_validate(config["riddle_ai"]))
        self.request_kwargs = {"cookies": {"ipb_member_id": config["authentication"]["ipb_member_id"], "ipb_pass_hash": config["authentication"]["ipb_pass_hash"]}, "headers": {"User-Agent": config["authentication"]["user_agent"]}}

    def api_request(self, *args, **kwargs):
        return request_with_retry(*args, **kwargs, **self.request_kwargs)

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
        # 获取跳过的过滤器和物品
        skipped_filters = wanted_items = []
        if "task_bot" in self.config:
            skipped_filters = self.config["task_bot"]["market_bot"]["skipped_filters"]
            wanted_items = self.config["task_bot"]["market_bot"]["wanted_items"]

        # 获取市场主页
        soup = BeautifulSoup(self.api_request(requests.get, f"{self.main_url}/?s=Bazaar&ss=mk").text, "lxml")

        # 查看各个过滤器下的物品
        filters = [(filter_element.text, filter_element.attrs["href"]) for filter_element in soup.find(id="filterbar").find_all("a", href=True) if filter_element.text not in skipped_filters]
        items_to_sell = []
        for filter_name, href in tqdm(filters, desc="Fetch Market's Itemlist"):
            soup = BeautifulSoup(self.api_request(requests.get, href).text, "lxml")

            # 遍历每一个物品
            for item_element in soup.find(id="market_itemlist").find_all("tr", onclick=True):
                item_name, your_stock = [ele.text for ele in item_element.find_all("td")[:2]]
                if item_name in wanted_items:
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

    @abstractmethod
    def task(self) -> tuple[str, BattleResult] | None:
        pass


class PersistentBot(BaseBot):
    def __init__(self):
        config = json.loads(pathlib.Path("world/persistent/config.json").read_text("utf-8"))
        self.init(config, MAIN_URL)
        self.encounter_cookies = {}

    def train_henjutsu(self) -> str | None:
        url = f"{self.main_url}/?s=Character&ss=tr"
        soup = BeautifulSoup(self.api_request(requests.get, url).text, "lxml")
        for subject in soup.find(id="train_table").find_all("tr"):
            # 跳过表头
            info_elements = subject.find_all("td")
            if not info_elements:
                continue

            # 根据名字筛选
            if (henjutsu_name := info_elements[0].text) not in self.config["task_bot"]["training_henjutsu"]:
                continue

            # 如果无法训练（比如还在训练，或者 Credits 不够），看看下一个的情况
            if "onclick" not in (train_button := info_elements[-1].find("img")).attrs:
                continue
            
            # 开始训练
            subject_id, = re.search(r"training.start_training\((\d+)\)", train_button.attrs["onclick"]).groups()
            self.api_request(requests.post, url, data={"start_train": subject_id, "cancel_train": "0"})
            return henjutsu_name

    def repair_equipment(self) -> bool:
        url = f"{self.main_url}/?s=Forge&ss=re"
        soup = BeautifulSoup(self.api_request(requests.get, url).text, "lxml")

        # 如果存在至少一个装备损坏，就修复装备
        if soup.find(class_="equiplist").find(class_="eqp"):
            self.api_request(requests.post, url, data={"repair_all": "1"})
            return True
        return False

    def settings(self, difficult_level: str):
        url = f"{self.main_url}/?s=Character&ss=se"
        soup = BeautifulSoup(self.api_request(requests.get, url).text, "lxml")

        # 获取字体信息
        use_local_font = "checked" in soup.find("input", attrs={"name": "fontlocal"}).attrs
        font_family = soup.find("input", {"name": "fontface"}).attrs["value"]

        # 选择最佳称号（最后一个效果最好）和最佳 UI（Utilitarian）
        title_override = soup.find(id="settings_title").find_all("tr")[-1].find("input", {"name": "title_override"}).attrs["value"]
        vitalstyle = "d"

        # 更改设置
        self.api_request(requests.post, url, data={"difflevel": difficult_level, "title_override": title_override, "fontlocal": "on" if use_local_font else "off", "fontface": font_family, "vitalstyle": vitalstyle, "submit": "Apply Changes"})

    def equipment_store_bot(self) -> int:
        # 获取装备商店主页
        soup = BeautifulSoup(self.api_request(requests.get, f"{self.main_url}/?s=Bazaar&ss=es").text, "lxml")
        
        # 卖掉各个过滤器下的物品
        filters = [filter_element.attrs["href"] for filter_element in soup.find(id="filterbar").find_all("a", href=True) if filter_element.text not in self.config["task_bot"]["equipment_store_bot"]["skipped_filters"]]
        equipments_sold = 0
        for href in tqdm(filters, desc="Sell Equipments"):
            soup = BeautifulSoup(self.api_request(requests.get, href).text, "lxml")

            # 遍历每一个物品
            equipments = []
            for equipment in soup.find(id="item_pane").find(class_="equiplist").find_all(class_="eqp"):
                if not (sell_button := equipment.find(attrs={"data-locked": "0"})):
                    continue
                equipments.append(re.search(r"equips.set\((\d+),'item_pane',\d+,\d+\)", sell_button.attrs["onmouseover"]).group(1))

            # 卖出物品
            if not equipments:
                continue
            storetoken = soup.find("input", attrs={"name": "storetoken"}).attrs["value"]
            self.api_request(requests.post, href, data={"storetoken": storetoken, "select_group": "item_pane", "select_eids": ",".join(equipments)})
            equipments_sold += len(equipments)

        return equipments_sold

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

    def arena(self) -> Callable[[], Any] | None:
        # 每隔一个小时就会回复 1 点体力值，所以有体力的时候快去打 Arena 拿 Credit 吧
        # https://ehwiki.org/wiki/Stamina
        url = f"{self.main_url}/?s=Battle&ss=ar"
        page = self.api_request(requests.get, url).text

        # 获取体力值并检测是否符合条件
        # | Stamina | Status | Effect |
        # | 60-99 | Great | +100% EXP but stamina drains 50% faster |
        if int(re.search(r"Stamina: (\d+)", page).group(1)) < 85:
            return

        # 检测可用的 Arena，并筛选
        soup = BeautifulSoup(page, "lxml")
        battles = []
        for arena in soup.find(id="arena_list").find_all("tr"):
            # 跳过 Table Header 行和不可用战斗
            if not (info := arena.find_all("td")) or "onclick" not in (start_button := info[-1].find("img")).attrs:
                continue
            # 获取战斗信息
            rounds = int(info[3].text)
            clear_bonus = int(info[-2].text.replace(",", "").removesuffix(" C"))
            # 获取战斗 API 信息
            initid, inittoken = re.search(r"init_battle\((\d+),\d+,'(\w+)'\)", start_button.attrs["onclick"]).groups()
            api_data = {"initid": initid, "inittoken": inittoken}
            # 记录战斗
            battles.append((rounds, clear_bonus, api_data))

        # 筛出奖励小于 1000 的战斗，并选择最优性价比的战斗
        battles = [battle for battle in battles if battle[1] >= 1000]
        if not battles:
            return
        best_battle_data = max(battles, key=lambda x: x[1] / x[0])[-1]
        battle_func = partial(self.api_request, requests.post, url, data=best_battle_data)
        return battle_func

    def ring_of_blood(self) -> Callable[[], Any] | None:
        url = f"{self.main_url}/?s=Battle&ss=rb"
        page = self.api_request(requests.get, url).text

        soup = BeautifulSoup(page, "lxml")
        for tr in soup.find(id="arena_list").find_all("tr"):
            if not tr.find("td") or "onclick" not in (start_button := tr.find_all("td")[-1].find("img")).attrs:
                continue

            initid, entrycost, inittoken = re.search(r"init_battle\((\d+),(\d+),'(\w+)'\)", start_button.attrs["onclick"]).groups()
            if int(entrycost) > 1:
                continue
            return partial(self.api_request, requests.post, url, data={"initid": initid, "inittoken": inittoken})

    def task(self) -> tuple[str, tuple[str, BattleResult]] | None:
        if self.config["task_bot"]["training_henjutsu"]:
            print(f"[task_bot.PersistentBot.task] [TrainHenjutsu] 尝试训练 Henjutsu ...")
            if henjutsu_trained := self.train_henjutsu():
                print(f"[task_bot.PersistentBot.task] [TrainHenjutsu] 成功开始训练 {henjutsu_trained}")

        print("[task_bot.PersistentBot.task] [LookForBattle] 检测战斗事件 ...")
        for event_type, func in [("Random Encounter", self.encounter), ("Arena", self.arena), ("Ring of Blood", self.ring_of_blood)]:
            if battle_func := func():
                battle_config = self.config["task_bot"]["battle"][event_type]
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
        print(f"[task_bot.PersistentBot.task] [{event_type}] [SettingDifficultLevel] 设置难度等级为 {battle_config['difficult_level']} ...")
        self.settings(battle_config['difficult_level'])
        print(f"[task_bot.PersistentBot.task] [{event_type}] [Battle] 开始战斗 ...")
        battle_func()

        try:
            while True:
                battle_result = self.battle_with_riddle_ai.battle(False, battle_config["epsilon"], battle_config["difficult_level"], battle_config["battle_bot_override"])
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
    def __init__(self):
        config = json.loads(pathlib.Path("world/isekai/config.json").read_text("utf-8"))
        self.init(config, f"{MAIN_URL}/isekai")

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
        filters = [filter_element.attrs["href"] for filter_element in soup.find(id="filterbar").find_all("a", href=True)]
        equipments_sold = 0
        for href in tqdm(filters, desc="Sell Equipments"):
            soup = BeautifulSoup(self.api_request(requests.get, href).text, "lxml")

            # 遍历每一个物品
            equipments = []
            for equipment in soup.find(id="equiplist").find_all("tr", onclick=True):
                equipments.append(re.search(r"hover_equip\((\d+)\)", equipment.attrs["onmouseover"]).group(1))

            # 卖出物品
            if not equipments:
                continue
            storetoken = soup.find("input", attrs={"name": "postoken"}).attrs["value"]
            self.api_request(requests.post, href, data={"postoken": storetoken, "eqids[]": equipments})
            equipments_sold += len(equipments)

        return equipments_sold

    def __get_arena_list(self, url: str) -> tuple[int, list[tuple[int, Callable[[], Any]]]]:
        url = f"{self.main_url}/{url}"
        page = self.api_request(requests.get, url).text
        stamina = int(re.search(r"Stamina: (\d+)", page).group(1))

        soup = BeautifulSoup(page, "lxml")
        postoken = soup.find("input", attrs={"name": "postoken"}).attrs["value"]
        arena_list = []
        for arena in soup.find(id="arena_list").find_all("tr"):
            if not (info := arena.find_all("td")) or not (start_button := info[-1].find("img")) or "onclick" not in start_button.attrs:
                continue
            initid, entrycost = re.search(r"init_battle\((\d+),(\d+)\)", start_button.attrs["onclick"]).groups()
            battle_func = partial(self.api_request, requests.post, url, data={"initid": initid, "postoken": postoken})
            arena_list.append(((int(entrycost), battle_func)))

        return stamina, arena_list

    def arena(self) -> Callable[[], Any] | None:
        stamina, arena_list = self.__get_arena_list("?s=Battle&ss=ar")
        if stamina < 80:
            return
        if arena_list:
            return arena_list[0][1]

    def ring_of_blood(self) -> Callable[[], Any] | None:
        _, arena_list = self.__get_arena_list("?s=Battle&ss=rb")
        for entrycost, battle_func in arena_list:
            if entrycost > 1:
                continue
            return battle_func

    def task(self) -> tuple[str, BattleResult] | None:
        print("[task_bot.IsekaiBot.task] [LookForBattle] 检测战斗事件 ...")
        for event_type, func in [("Arena", self.arena), ("Ring of Blood", self.ring_of_blood)]:
            if battle_func := func():
                battle_config = self.config["task_bot"]["battle"][event_type]
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
                battle_result = self.battle_with_riddle_ai.battle(True, battle_config["epsilon"], "default", battle_config["battle_bot_override"])
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


def main():
    persistent_bot = PersistentBot()
    isekai_bot = IsekaiBot()

    while True:
        for world, bot in [("Persistent", persistent_bot), ("Isekai", isekai_bot)]:
            if result := bot.task():
                break
        else:
            # Random Encounter event can occur once every 30 minutes upon visitation of the E-Hentai news page or a gallery
            for _ in tqdm(range(random.randint(1800, 1830)), desc="Wait"):
                time.sleep(1)
            continue

        # 成功进行战斗后，记录战斗结果
        event_type, battle_result = result
        event_id = f"{world}:{event_type}"

        task_log = {}
        if (task_log_path := pathlib.Path(f"world/task_log.json")).exists():
            task_log = json.loads(task_log_path.read_text("utf-8"))

        log = task_log.setdefault("battle_result", {}).setdefault(event_id, {})
        log[battle_result.name] = log.get(battle_result.name, 0) + 1

        task_log_path.write_text(json.dumps(task_log, indent="\t"), "utf-8")


if __name__ == "__main__":
    main()
