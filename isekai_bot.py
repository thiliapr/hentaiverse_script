import random, json, pathlib, time, re, requests
from typing import Any
from collections.abc import Callable
from bs4 import BeautifulSoup
from tqdm import tqdm
from battle_bot import battle_with_skip_riddle
from utils.battle import TokenNotFoundError
from utils.constants import MAIN_URL
from utils.network import request_with_retry

config = json.loads(pathlib.Path("world/isekai/config.json").read_text("utf-8"))
request_kwargs = {"cookies": {"ipb_member_id": config["authentication"]["ipb_member_id"], "ipb_pass_hash": config["authentication"]["ipb_pass_hash"]}, "headers": {"User-Agent": config["authentication"]["user_agent"]}}


def repair_equipment() -> Callable[[], Any] | None:
    url = f"{MAIN_URL}/isekai/?s=Bazaar&ss=am&screen=repair&filter=equipped"
    soup = BeautifulSoup(request_with_retry(requests.get, url, **request_kwargs).text, "lxml")

    # 如果存在至少一个装备损坏，就修复装备
    equipments = []
    for equipment in soup.find(id="equiplist").find_all("tr", onclick=True):
        equipment_id = re.search(r"hover_equip\((\d+)\)", equipment.attrs["onmouseover"]).group(1)
        equipments.append(equipment_id)
    if not equipments:
        return
    
    # 准备网络请求
    postoken = soup.find("input", attrs={"name": "postoken"}).attrs["value"]
    return lambda: request_with_retry(requests.post, url, data={"postoken": postoken, "eqids[]": equipments, "replace_charms": "on"}, **request_kwargs)


def attribute_point_allocation() -> list[str]:
    url = f"{MAIN_URL}/isekai/?s=Character&ss=ch"
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


def equipment_store_bot() -> int:
    # 获取装备商店主页
    soup = BeautifulSoup(request_with_retry(requests.get, f"{MAIN_URL}/isekai/?s=Bazaar&ss=am&screen=sell", **request_kwargs).text, "lxml")
    
    # 卖掉各个过滤器下的物品
    filters = [filter_element.attrs["href"] for filter_element in soup.find(id="filterbar").find_all("a", href=True)]
    equipments_sold = 0
    for href in tqdm(filters, desc="Sell Equipments"):
        soup = BeautifulSoup(request_with_retry(requests.get, href, **request_kwargs).text, "lxml")

        # 遍历每一个物品
        equipments = []
        for equipment in soup.find(id="equiplist").find_all("tr", onclick=True):
            equipments.append(re.search(r"hover_equip\((\d+)\)", equipment.attrs["onmouseover"]).group(1))

        # 卖出物品
        if not equipments:
            continue
        storetoken = soup.find("input", attrs={"name": "postoken"}).attrs["value"]
        request_with_retry(requests.post, href, data={"postoken": storetoken, "eqids[]": equipments}, **request_kwargs)
        equipments_sold += len(equipments)

    return equipments_sold


def arnea() -> Callable[[], Any] | None:
    page = request_with_retry(requests.get, f"{MAIN_URL}/isekai/?s=Battle&ss=ar", **request_kwargs).text
    if int(re.search(r"Stamina: (\d+)", page).group(1)) < 85:
        return

    soup = BeautifulSoup(page, "lxml")
    postoken = soup.find("input", attrs={"name": "postoken"}).attrs["value"]
    for arena in reversed(soup.find(id="arena_list").find_all("tr")):
        info = arena.find_all("td")
        if not info or "onclick" not in (start_button := info[-1].find("img")).attrs:
            continue
        initid = re.search(r"init_battle\((\d+),0\)", start_button.attrs["onclick"]).group(1)
        return lambda: request_with_retry(requests.post, f"{MAIN_URL}/isekai/?s=Battle&ss=ar", data={"initid": initid, "postoken": postoken}, **request_kwargs)


def main():
    while True:
        print("[isekai_bot.main] [LookForBattle] 检测战斗事件 ...")
        if not (battle_func := arnea()):
            print("[isekai_bot.main] [LookForBattle] 没有发现战斗事件，等待一会继续 ...")
            for _ in tqdm(range(random.randint(1800, 1860)), desc="Waiting"):
                time.sleep(1)
            continue

        # 战斗前准备事项
        print(f"[isekai_bot.main] [RepairEquipment] 检测装备损坏 ...")
        if repair_func := repair_equipment():
            print(f"[isekai_bot.main] [RepairEquipment] 正在修复装备 ...")
            repair_func()

        print(f"[isekai_bot.main] [AllocateAttribute] 尝试加点 ...")
        if attr := attribute_point_allocation():
            print(f"[isekai_bot.main] [AllocateAttribute] 已为属性 {', '.join(attr)} 加点！")

        # 开始战斗
        print(f"[isekai_bot.main] [Battle] 开始战斗 ...")
        battle_func()

        try:
            while True:
                battle_with_skip_riddle(True, 0.1, "default")
        except TokenNotFoundError:
            pass

        # 卖东西
        print(f"[isekai_bot.mai] [EquipmentStoreBot] 变卖装备 ...")
        if items_sold := equipment_store_bot():
            print(f"[isekai_bot.mai] [EquipmentStoreBot] 变卖了 {items_sold} 件装备")


if __name__ == "__main__":
    main()
