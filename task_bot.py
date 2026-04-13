# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import random, json, pathlib, time, re, requests
from typing import Any
from collections.abc import Callable
from tqdm import tqdm
from bs4 import BeautifulSoup
from utils.constants import MAIN_URL
from utils.network import request_with_retry
from utils.battle import TokenNotFoundError
from battle_bot import battle_with_skip_riddle

config = json.loads(pathlib.Path("world/persistent/config.json").read_text("utf-8"))
request_kwargs = {"cookies": {"ipb_member_id": config["authentication"]["ipb_member_id"], "ipb_pass_hash": config["authentication"]["ipb_pass_hash"]}, "headers": {"User-Agent": config["authentication"]["user_agent"]}}


def monster_lab_bot() -> tuple[bool, int]:
    url = f"{MAIN_URL}/?s=Bazaar&ss=ml"
    soup = BeautifulSoup(request_with_retry(requests.get, url, **request_kwargs).text, "lxml")

    # 喂饱肚子
    if feed := soup.find(onclick="do_feed_all('food')") is not None:
        request_with_retry(requests.post, url, data={"feed_all": "food"}, **request_kwargs)

    # 给属性加点
    attrs_upgraded = 0
    for slot in soup.find(id="slot_pane").find_all(class_="msl"):
        href = re.search(r"document\.location='([^']+)'", slot.attrs["onclick"]).group(1)
        soup = BeautifulSoup(request_with_retry(requests.get, href, **request_kwargs).text, "lxml")

        # 寻找可加点属性
        for attr_group in soup.find(id="monsterstats_top").find_all(class_="mcr"):
            for attr in attr_group.find_all("tr"):
                if "onclick" not in (upgrade_button := attr.find("img")).attrs:
                    continue
                
                # 加点、记录
                attr_name = re.search(r"do_crystal_upgrade\('(\w+)', event\)", upgrade_button.attrs["onclick"]).group(1)
                request_with_retry(requests.post, href, data={"crystal_upgrade": attr_name, "crystal_count": "1"}, **request_kwargs)
                attrs_upgraded += 1

    return feed, attrs_upgraded


def market_bot() -> tuple[int, list[str]]:
    # 获取市场主页
    soup = BeautifulSoup(request_with_retry(requests.get, f"{MAIN_URL}/?s=Bazaar&ss=mk", **request_kwargs).text, "lxml")

    # 查看各个过滤器下的物品
    filters = [(filter_element.text, filter_element.attrs["href"]) for filter_element in soup.find(id="filterbar").find_all("a", href=True) if filter_element.text not in config["task_bot"]["market_bot"]["skipped_filters"]]
    items_to_sell = []
    for filter_name, href in tqdm(filters, desc="Fetch Market's Itemlist"):
        soup = BeautifulSoup(request_with_retry(requests.get, href, **request_kwargs).text, "lxml")

        # 遍历每一个物品
        for item_element in soup.find(id="market_itemlist").find_all("tr", onclick=True):
            item_name, your_stock = [ele.text for ele in item_element.find_all("td")[:2]]
            if item_name in config["task_bot"]["market_bot"]["wanted_items"]:
                continue
            if your_stock == "":
                continue
            items_to_sell.append((f"{filter_name}/{item_name}", re.search(r"document\.location='([^']+)'", item_element.attrs["onclick"]).group(1)))

    # 卖出物品
    items_sold = []
    for item_id, href in tqdm(items_to_sell, desc="Sell Items"):
        soup = BeautifulSoup(request_with_retry(requests.get, href, **request_kwargs).text, "lxml")
        
        # Buy Order List 是按照价格高到低（利润大到小）排序的，对于 Seller 来说，最上面的 Order 是最值的
        if (best_order := soup.find(id="market_itembuy").find(class_="market_itemorders").find("tr", onclick=True)) is None:
            continue

        # 提取数据
        sell_price, count = re.search(r"autofill_from_buy_order\(\d+,(\d+),(\d+)\)", best_order.attrs["onclick"]).groups()
        if count == "0":
            continue
        marketoken, update_value = [soup.find("input", attrs={"name": name}).attrs["value"] for name in ["marketoken", "sellorder_update"]]

        # 卖出物品，并加入成功列表，计算赚的 Credits
        request_with_retry(requests.post, href, data={"marketoken": marketoken, "sellorder_batchcount": count, "sellorder_batchprice": sell_price, "sellorder_update": update_value}, **request_kwargs)
        items_sold.append(item_id)

    # 转移市场 Credit 到账户里
    soup = BeautifulSoup(request_with_retry(requests.get, f"{MAIN_URL}/?s=Bazaar&ss=mk", **request_kwargs).text, "lxml")
    market_balance = re.search(r"\.value=(\d+)", soup.find_all(class_="credit_balance")[1].attrs["onclick"]).group(1)
    marketoken, action_value = [soup.find("input", attrs={"name": name}).attrs["value"] for name in ["marketoken", "account_withdraw"]]
    if market_balance != "0":
        request_with_retry(requests.post, f"{MAIN_URL}/?s=Bazaar&ss=mk", data={"marketoken": marketoken, "account_amount": market_balance, "account_withdraw": action_value}, **request_kwargs)

    return int(market_balance), items_sold


def equipment_store_bot() -> int:
    # 获取装备商店主页
    soup = BeautifulSoup(request_with_retry(requests.get, f"{MAIN_URL}/?s=Bazaar&ss=es", **request_kwargs).text, "lxml")
    
    # 卖掉各个过滤器下的物品
    filters = [filter_element.attrs["href"] for filter_element in soup.find(id="filterbar").find_all("a", href=True) if filter_element.text not in config["task_bot"]["equipment_store_bot"]["skipped_filters"]]
    equipments_sold = 0
    for href in tqdm(filters, desc="Sell Equipments"):
        soup = BeautifulSoup(request_with_retry(requests.get, href, **request_kwargs).text, "lxml")

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
        request_with_retry(requests.post, href, data={"storetoken": storetoken, "select_group": "item_pane", "select_eids": ",".join(equipments)}, **request_kwargs)
        equipments_sold += len(equipments)

    return equipments_sold


def train_henjutsu() -> str | None:
    url = f"{MAIN_URL}/?s=Character&ss=tr"
    soup = BeautifulSoup(request_with_retry(requests.get, url, **request_kwargs).text, "lxml")
    for subject in soup.find(id="train_table").find_all("tr"):
        # 跳过表头
        info_elements = subject.find_all("td")
        if not info_elements:
            continue

        # 根据名字筛选
        if (henjutsu_name := info_elements[0].text) not in config["task_bot"]["training_henjutsu"]:
            continue

        # 如果无法训练（比如还在训练，或者 Credits 不够），看看下一个的情况
        if "onclick" not in (train_button := info_elements[-1].find("img")).attrs:
            continue
        
        # 开始训练
        subject_id, = re.search(r"training.start_training\((\d+)\)", train_button.attrs["onclick"]).groups()
        request_with_retry(requests.post, url, data={"start_train": subject_id, "cancel_train": "0"}, **request_kwargs)
        return henjutsu_name


def attribute_point_allocation() -> list[str]:
    url = f"{MAIN_URL}/?s=Character&ss=ch"
    soup = BeautifulSoup(request_with_retry(requests.get, url, **request_kwargs).text, "lxml")

    # 获取剩余 EXP 和属性加点所需 EXP
    attributes = ["str", "dex", "agi", "end", "int", "wis"]
    remaining_exp = int(soup.find(id="remaining_exp").text.replace(",", ""))
    required_exp = {attr: int(soup.find(id=f"{attr}_left").text.replace(",", "")) for attr in attributes}

    # 均衡加点
    # https://ehwiki.org/wiki/Character_Stats#Primary_Attributes
    attr_delta = {attr: 0 for attr in attributes}
    for attr in sorted(attributes, key=lambda attr: required_exp[attr]):
        if required_exp[attr] <= remaining_exp:
            attr_delta[attr] = 1
            remaining_exp -= required_exp[attr]

    # 发送请求
    if any(v > 0 for v in attr_delta.values()):
        request_with_retry(requests.post, url, data={"attr_apply": "1"} | {f"{attr}_delta": str(delta) for attr, delta in attr_delta.items()}, **request_kwargs)
    return [k for k, v in attr_delta.items() if v > 0]


def settings(difficult_level: str):
    soup = BeautifulSoup(request_with_retry(requests.get, f"{MAIN_URL}/?s=Character&ss=se", **request_kwargs).text, "lxml")

    # 获取字体信息
    use_local_font = "checked" in soup.find("input", attrs={"name": "fontlocal"}).attrs
    font_family = soup.find("input", {"name": "fontface"}).attrs["value"]

    # 选择最佳称号（最后一个效果最好）和最佳 UI（Utilitarian）
    title_override = soup.find(id="settings_title").find_all("tr")[-1].find("input", {"name": "title_override"}).attrs["value"]
    vitalstyle = "d"

    # 更改设置
    request_with_retry(requests.post, f"{MAIN_URL}/?s=Character&ss=se", data={"difflevel": difficult_level, "title_override": title_override, "fontlocal": "on" if use_local_font else "off", "fontface": font_family, "vitalstyle": vitalstyle, "submit": "Apply Changes"}, **request_kwargs)


def repair_equipment() -> Callable[[], Any] | None:
    soup = BeautifulSoup(request_with_retry(requests.get, f"{MAIN_URL}/?s=Forge&ss=re", **request_kwargs).text, "lxml")

    # 如果存在至少一个装备损坏，就修复装备
    if soup.find(class_="equiplist").find(class_="eqp"):
        return lambda: request_with_retry(requests.post, f"{MAIN_URL}/?s=Forge&ss=re", data={"repair_all": "1"}, **request_kwargs)


def encounter(session_cookies: dict[str, str]) -> Callable[[], Any] | None:
    # Random Encounter is a single-round battle that places players against common foes in order to get a lot credits and EXP.
    # This battle event can occur once every 30 minutes upon visitation of the E-Hentai news page or a gallery. A message will be displayed in the ad space at the top of the page and clicking on the link will automatically open a HentaiVerse window to the battle.
    # https://ehwiki.org/wiki/Random_Encounter
    cookies = request_kwargs["cookies"]
    headers = request_kwargs["headers"]

    # 发送网页请求
    if "event" not in session_cookies:
        session_cookies["event"] = "1"
    resp = request_with_retry(requests.get, "https://e-hentai.org/news.php", headers=headers, cookies=cookies | session_cookies)
    session_cookies.update(dict(resp.cookies))

    # 解析检测随机遇敌事件，并点击遇敌链接
    soup = BeautifulSoup(resp.text, "lxml")
    if (eventpane := soup.find(id="eventpane")) is None:
        return
    if (link_element := eventpane.find("a", href=True)) is None:
        return
    battle_func = lambda: request_with_retry(requests.get, link_element.attrs["href"], cookies=cookies, headers=headers)
    return battle_func


def arena() -> Callable[[], Any] | None:
    # 每隔一个小时就会回复 1 点体力值，所以有体力的时候快去打 Arena 拿 Credit 吧
    # https://ehwiki.org/wiki/Stamina
    page = request_with_retry(requests.get, f"{MAIN_URL}/?s=Battle&ss=ar", **request_kwargs).text

    # 获取体力值并检测是否符合条件
    # | Stamina | Status | Effect |
    # | 60-99 | Great | +100% EXP but stamina drains 50% faster |
    # https://ehwiki.org/wiki/Stamina
    if int(re.search(r"Stamina: (\d+)", page).group(1)) < 85:
        return
    
    # 检测可用的 Arena，并筛选
    soup = BeautifulSoup(page, "lxml")
    battles = []
    for arena in soup.find(id="arena_list").find_all("tr"):
        info = arena.find_all("td")
        # 跳过 Table Header 行和不可用战斗
        if not info or "onclick" not in (start_button := info[-1].find("img")).attrs:
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
    battle_func = lambda: request_with_retry(requests.post, f"{MAIN_URL}/?s=Battle&ss=ar", data=best_battle_data, **request_kwargs)
    return battle_func


def main():
    # 初始化随机遇敌函数内部状态
    encounter_cookies = {}

    while True:
        # 训练 Henjutsu（游戏的技能，要花 Credit 和时间训练，可以增加爆率、EXP 倍数等。游戏有 15 个 Hentsuju 可供训练）
        # https://ehwiki.org/wiki/Training
        if config["task_bot"]["training_henjutsu"]:
            print(f"[TaskBot] [TrainHenjutsu] 尝试训练 Henjutsu ...")
            if henjutsu_trained := train_henjutsu():
                print(f"[TaskBot] [TrainHenjutsu] 成功开始训练 {henjutsu_trained}")

        print("[TaskBot] [LookForBattle] 检测战斗事件 ...")
        battle_func = None
        if battle_func := encounter(encounter_cookies):
            event_type, difficult_level, epsilon, config_override = "随机遇敌事件", config["task_bot"]["encounter_difficult_level"], 0., config["task_bot"]["battle_bot_override"]["encounter"]
        elif battle_func := arena():
            event_type, difficult_level, epsilon, config_override = "Arena 战斗", config["task_bot"]["arena_difficult_level"], config["task_bot"]["arena_epsilon"], config["task_bot"]["battle_bot_override"]["arena"]

        if battle_func is None:
            print("[TaskBot] [LookForBattle] 没有发现战斗事件，等待一会继续 ...")
            # Wiki about Random Encounter: "This battle event can occur once every 30 minutes upon visitation of the E-Hentai news page or a gallery"
            for _ in tqdm(range(random.randint(1800, 1860)), desc="Waiting"):
                time.sleep(1)
            continue

        # 战斗前准备事项
        print(f"[TaskBot] [{event_type}] [RepairEquipment] 检测装备损坏 ...")
        if repair_func := repair_equipment():
            print(f"[TaskBot] [{event_type}] [RepairEquipment] 正在修复装备 ...")
            repair_func()

        print(f"[TaskBot] [{event_type}] [AllocateAttribute] 尝试加点 ...")
        if attr := attribute_point_allocation():
            print(f"[TaskBot] [{event_type}] [AllocateAttribute] 已为属性 {', '.join(attr)} 加点！")

        # 打印当前战斗事件，并设置难度
        print(f"[TaskBot] [{event_type}] [SettingDifficultLevel] 设置难度等级为 {difficult_level} ...")
        settings(difficult_level)
        print(f"[TaskBot] [{event_type}] [Battle] 开始战斗 ...")
        battle_func()

        try:
            while True:
                battle_result = battle_with_skip_riddle(False, epsilon, difficult_level, config_override)
        except TokenNotFoundError:
            # 找不到 BattleToken，可能意味着遇到小马谜题，或者战斗结束。由于小马谜题在 battle 内已经解决，所以现在只可能是战斗结束
            pass

        # 战后变卖不需要的东西
        print(f"[TaskBot] [MarketBot] 变卖物品 ...")
        market_balance, items_sold = market_bot()
        if market_balance:
            print(f"[TaskBot] [MarketBot] 入账 {market_balance} Credits; 变卖了的物品: {items_sold}")

        print(f"[TaskBot] [EquipmentStoreBot] 变卖装备 ...")
        if equipments_sold := equipment_store_bot():
            print(f"[TaskBot] [EquipmentStoreBot] 变卖了 {equipments_sold} 件装备")

        # 养宠物
        print(f"[TaskBot] [MonsterLabBot] 检测 Monster 情况 ...")
        if any(result := monster_lab_bot()):
            feed, attrs_upgraded = result
            log = "[TaskBot] [MonsterLabBot] "
            if feed:
                log += "喂养了 Monster"
                if attrs_upgraded:
                    log += "，"
            if attrs_upgraded:
                log += f"加点了 {attrs_upgraded} 个属性"
            print(log)

        # 统计输赢信息，记录
        stats_file = pathlib.Path("world/persistent/stats_data.json")
        stats = {}
        if stats_file.exists():
            stats = json.loads(stats_file.read_text("utf-8"))
        event_stats = stats.setdefault(event_type, {})
        event_stats[battle_result.name] = event_stats.get(battle_result.name, 0) + 1
        stats_file.write_text(json.dumps(stats, ensure_ascii=False), "utf-8")


if __name__ == "__main__":
    main()
