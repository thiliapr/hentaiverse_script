# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import random, json, pathlib, time, re, requests
from collections.abc import Callable
from tqdm import tqdm
from bs4 import BeautifulSoup
from utils.network import request_with_retry
from utils.battle import TokenNotFoundError
from battle_bot import battle

GALLERY_URL = "https://e-hentai.org/g/3502641/17246a289f/"
config = json.loads(pathlib.Path("config.json").read_text("utf-8"))
request_kwargs = {"cookies": {"ipb_member_id": config["ipb_member_id"], "ipb_pass_hash": config["ipb_pass_hash"]}, "headers": {"User-Agent": config["user_agent"]}}


def settings(difficult_level: str):
    resp = request_with_retry(requests.get, "https://hentaiverse.org/?s=Character&ss=se", **request_kwargs)
    soup = BeautifulSoup(resp.text, "lxml")

    # 获取字体信息
    use_local_font = "checked" in soup.find("input", attrs={"name": "fontlocal"}).attrs
    font_family = soup.find("input", {"name": "fontface"}).attrs["value"]

    # 选择最佳称号（最后一个效果最好）和最佳 UI（Utilitarian）
    title_override = soup.find(id="settings_title").find_all("tr")[-1].find("input", {"name": "title_override"}).attrs["value"]
    vitalstyle = "d"

    # 更改设置
    request_with_retry(requests.post, "https://hentaiverse.org/?s=Character&ss=se", data={"difflevel": difficult_level, "title_override": title_override, "fontlocal": "on" if use_local_font else "off", "fontface": font_family, "vitalstyle": vitalstyle, "submit": "Apply Changes"}, **request_kwargs)


def repair_equipment() -> Callable[[], None] | None:
    resp = request_with_retry(requests.get, "https://hentaiverse.org/?s=Forge&ss=re", **request_kwargs)
    soup = BeautifulSoup(resp.text, "lxml")
    # 如果存在至少一个装备损坏，就修复装备
    if soup.find(class_="equiplist").find(class_="eqp"):
        return lambda: request_with_retry(requests.post, "https://hentaiverse.org/?s=Forge&ss=re", data={"repair_all": "1"}, **request_kwargs)


def encounter(session_cookies: dict[str, str]) -> Callable[[], None] | None:
    # Random Encounter is a single-round battle that places players against common foes in order to get a lot credits and EXP.
    # 请见 Wiki: https://ehwiki.org/wiki/Random_Encounter
    cookies = request_kwargs["cookies"]
    headers = request_kwargs["headers"]

    # 发送网页请求
    if not session_cookies:
        session_cookies.update(dict(request_with_retry(requests.get, GALLERY_URL, **request_kwargs).cookies))
    resp = request_with_retry(requests.get, GALLERY_URL, headers=headers, cookies=cookies | session_cookies)
    session_cookies.update(dict(resp.cookies))

    # 解析检测随机遇敌事件，并点击遇敌链接
    soup = BeautifulSoup(resp.text, "lxml")
    if (eventpane := soup.find(id="eventpane")) is None:
        return
    if (link_element := eventpane.find("a", href=True)) is None:
        return
    battle_func = lambda: request_with_retry(requests.get, link_element.attrs["href"], cookies=cookies, headers=headers)
    return battle_func


def arena() -> Callable[[], None] | None:
    # 每隔一个小时就会回复 1 点体力值，所以有体力的时候快去打 Arena 拿 Credit 吧
    # 请见 Wiki: https://ehwiki.org/wiki/Stamina
    page = request_with_retry(requests.get, "https://hentaiverse.org/?s=Battle&ss=ar", **request_kwargs).text

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
    battle_func = lambda: request_with_retry(requests.post, "https://hentaiverse.org/?s=Battle&ss=ar", data=best_battle_data, **request_kwargs)
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
        # 随机遇敌只有 1 个回合，比较容易打，而且不消耗体力，所以提升难度，拿更多 EXP
        if battle_func := encounter(encounter_cookies):
            event, difficult_level = "随机遇敌事件", "2"
        # Arena 有十几个回合，高难度下可能失败，打的目的主要是拿 Credit，而且本身消耗体力，所以降低难度，提高成功率
        elif battle_func := arena():
            event, difficult_level = "Arena 战斗", "1"
        else:
            battle_func = None

        if battle_func:
            # 战斗前准备事项
            print("检测装备损坏 ...")
            if repair_func := repair_equipment():
                print("正在修复装备 ...")
                repair_func()

            # 打印当前战斗事件，并设置难度
            print(f"正在进行 {event} ...")
            settings(difficult_level)
            print(f"开始战斗 ...")
            battle_func()

            try:
                while True:
                    battle_with_skip_riddle()
            except TokenNotFoundError:
                # 找不到 BattleToken，可能意味着遇到小马谜题，或者战斗结束。由于小马谜题在 battle 内已经解决，所以现在只可能是战斗结束
                pass
        else:
            print("没有发现战斗事件，等待一会继续 ...")
            for _ in tqdm(range(random.randint(450, 750)), desc="Waiting"):
                time.sleep(1)


if __name__ == "__main__":
    main()
