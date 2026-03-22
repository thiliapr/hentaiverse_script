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
from battle_bot import battle

config = json.loads(pathlib.Path("config.json").read_text("utf-8"))
request_kwargs = {"cookies": {"ipb_member_id": config["authentication"]["ipb_member_id"], "ipb_pass_hash": config["authentication"]["ipb_pass_hash"]}, "headers": {"User-Agent": config["authentication"]["user_agent"]}}


def attribute_point_allocation() -> list[str]:
    resp = request_with_retry(requests.get, f"{MAIN_URL}/?s=Character&ss=ch", **request_kwargs)
    soup = BeautifulSoup(resp.text, "lxml")

    # 获取剩余 EXP 和属性加点所需 EXP
    attributes = ["str", "dex", "agi", "end", "int", "wis"]
    remaining_exp = int(soup.find(id="remaining_exp").text.replace(",", ""))
    required_exp = {attr: int(soup.find(id=f"{attr}_left").text.replace(",", "")) for attr in attributes}

    # 给 Endurance（增加最大血量、物理减伤、魔法减伤）、Intelligence（增加伤害）、Wisdom（增加最大蓝量、蓝量恢复） 均衡加点
    # https://ehwiki.org/wiki/Character_Stats#Primary_Attributes
    attr_delta = {attr: 0 for attr in attributes}
    for attr in sorted(["end", "int", "wis"], key=lambda attr: required_exp[attr]):
        if (required_exp := int(soup.find(id=f"{attr}_left").text.replace(",", ""))) <= remaining_exp:
            attr_delta[attr] = 1
            remaining_exp -= required_exp

    # 发送请求
    if any(v > 0 for v in attr_delta.values()):
        request_with_retry(requests.post, f"{MAIN_URL}/?s=Character&ss=ch", data={"attr_apply": "1"} | {f"{attr}_delta": str(delta) for attr, delta in attr_delta.items()}, **request_kwargs)
    return [k for k, v in attr_delta.items() if v > 0]


def settings(difficult_level: str):
    resp = request_with_retry(requests.get, f"{MAIN_URL}/?s=Character&ss=se", **request_kwargs)
    soup = BeautifulSoup(resp.text, "lxml")

    # 获取字体信息
    use_local_font = "checked" in soup.find("input", attrs={"name": "fontlocal"}).attrs
    font_family = soup.find("input", {"name": "fontface"}).attrs["value"]

    # 选择最佳称号（最后一个效果最好）和最佳 UI（Utilitarian）
    title_override = soup.find(id="settings_title").find_all("tr")[-1].find("input", {"name": "title_override"}).attrs["value"]
    vitalstyle = "d"

    # 更改设置
    request_with_retry(requests.post, f"{MAIN_URL}/?s=Character&ss=se", data={"difflevel": difficult_level, "title_override": title_override, "fontlocal": "on" if use_local_font else "off", "fontface": font_family, "vitalstyle": vitalstyle, "submit": "Apply Changes"}, **request_kwargs)


def repair_equipment() -> Callable[[], Any] | None:
    resp = request_with_retry(requests.get, f"{MAIN_URL}/?s=Forge&ss=re", **request_kwargs)
    soup = BeautifulSoup(resp.text, "lxml")
    # 如果存在至少一个装备损坏，就修复装备
    if soup.find(class_="equiplist").find(class_="eqp"):
        return lambda: request_with_retry(requests.post, f"{MAIN_URL}/?s=Forge&ss=re", data={"repair_all": "1"}, **request_kwargs)


def encounter(session_cookies: dict[str, str]) -> Callable[[], Any] | None:
    # Random Encounter is a single-round battle that places players against common foes in order to get a lot credits and EXP.
    # 请见 Wiki: https://ehwiki.org/wiki/Random_Encounter
    cookies = request_kwargs["cookies"]
    headers = request_kwargs["headers"]

    # 从主页获取最新的 Gallery
    resp = request_with_retry(requests.get, "https://e-hentai.org/", **request_kwargs)
    soup = BeautifulSoup(resp.text, "lxml")
    galleries = [x.find(class_="glink").parent.attrs["href"] for x in soup.find("table", class_="gltc").find_all("tr") if x.find(class_="glink") is not None]
    session_cookies.update(dict(resp.cookies))

    # 发送网页请求
    if "event" not in session_cookies:
        session_cookies["event"] = "1"
    resp = request_with_retry(requests.get, random.choice(galleries), headers=headers, cookies=cookies | session_cookies)
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
    # 请见 Wiki: https://ehwiki.org/wiki/Stamina
    page = request_with_retry(requests.get, f"{MAIN_URL}/?s=Battle&ss=ar", **request_kwargs).text

    # 获取体力值并检测是否符合条件
    stamina, = re.search(r"Stamina: (\d+)", page).groups()
    if int(stamina) < 64:
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


def battle_with_skip_riddle(*args, **kwargs):
    while True:
        try:
            return battle(*args, **kwargs)
        except TokenNotFoundError as e:
            if "function check_submit_button() {" in e.page:
                # 让谜题自动过期。如果还没过期，那就再等这么多秒
                # 我知道不选也算是错误，但是 Wiki 说不选的惩罚比乱选的惩罚小，所以就拖吧
                # 请见 Wiki: https://ehwiki.org/wiki/RiddleMaster
                print("遇到小马谜题了！")
                time.sleep(20)
                continue
            raise e


def main():
    # 初始化随机遇敌函数内部状态
    encounter_cookies = {}

    while True:
        print("检测战斗事件 ...")
        battle_func = None
        if battle_func := encounter(encounter_cookies):
            # 随机遇敌只有 1 个回合，比较容易打，而且不消耗体力，所以提升难度，拿更多 EXP
            event, difficult_level = "随机遇敌事件", "4"
        elif battle_func := arena():
            # Arena 有十几个回合，高难度下可能失败，打的目的主要是拿 Credit，而且本身消耗体力，所以降低难度，提高成功率
            event, difficult_level = "Arena 战斗", "1"

        if battle_func:
            # 战斗前准备事项
            print("检测装备损坏 ...")
            if repair_func := repair_equipment():
                print("正在修复装备 ...")
                repair_func()

            print("正在尝试加点 ...")
            if attr := attribute_point_allocation():
                print(f"已为属性 {', '.join(attr)} 加了一点！")

            # 打印当前战斗事件，并设置难度
            print(f"正在进行 {event} ...")
            settings(difficult_level)
            print(f"开始战斗 ...")
            battle_func()

            try:
                while True:
                    battle_with_skip_riddle()
            except (TokenNotFoundError, TypeError):
                # 找不到 BattleToken，可能意味着遇到小马谜题，或者战斗结束。由于小马谜题在 battle 内已经解决，所以现在只可能是战斗结束
                pass
        else:
            print("没有发现战斗事件，等待一会继续 ...")
            # Wiki about Random Encounter: "This battle event can occur once every 30 minutes upon visitation of the E-Hentai news page or a gallery"
            for _ in tqdm(range(random.randint(1800, 2050)), desc="Waiting"):
                time.sleep(1)


if __name__ == "__main__":
    main()
