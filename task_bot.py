# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import json, pathlib, time, re, requests
from bs4 import BeautifulSoup
from utils.network import request_with_retry
from utils.battle import TokenNotFoundError
from battle_bot import battle

GALLERY_URL = "https://e-hentai.org/g/3502641/17246a289f/"
config = json.loads(pathlib.Path("config.json").read_text("utf-8"))


def encounter() -> bool:
    # Random Encounter is a single-round battle that places players against common foes in order to get a lot credits and EXP.
    # 请见 Wiki: https://ehwiki.org/wiki/Random_Encounter
    cookies = {"ipb_member_id": config["ipb_member_id"], "ipb_pass_hash": config["ipb_pass_hash"]}
    headers = {"User-Agent": config["user_agent"]}

    # 发送网页请求。这里的 event 代表时间刻（UNIX 时间），服务器会检测 event 并相应地返回是否存在随机遇敌事件，然后返回一个新的 cookie
    # 不过想靠这个无限刷随机遇敌是不可行的，因为服务器会隔着一定时间才刷新随机遇敌事件，在刷新前即使 event=1 也无济于事
    # 那么你问我 event 有何意义？那你就得去问服务器开发者了，反正我不知道
    resp = request_with_retry(requests.get, GALLERY_URL, headers=headers, cookies=cookies | {"event": "1"})

    # 解析检测随机遇敌事件，并点击遇敌链接
    soup = BeautifulSoup(resp.text, "lxml")
    if (eventpane := soup.find(id="eventpane")) is None:
        return False
    if (link_element := eventpane.find("a", href=True)) is None:
        return False
    request_with_retry(requests.get, link_element.attrs["href"], cookies=cookies, headers=headers)
    return True


def arena() -> bool:
    # 每隔一个小时就会回复 1 点体力值，所以有体力的时候快去打 Arena 拿 Credit 吧
    # 请见 Wiki: https://ehwiki.org/wiki/Stamina
    kw = {"cookies": {"ipb_member_id": config["ipb_member_id"], "ipb_pass_hash": config["ipb_pass_hash"]}, "headers": {"User-Agent": config["user_agent"]}}
    page = request_with_retry(requests.get, "https://hentaiverse.org/?s=Battle&ss=ar", **kw).text

    # 获取体力值并检测是否符合条件
    stamina, = re.search(r"Stamina: (\d+)", page).groups()
    if int(stamina) < 64:
        return False
    
    # 检测可用的 Arena，并筛选
    soup = BeautifulSoup(page, "lxml")
    for arena in soup.find(id="arena_list").find_all("tr"):
        info = arena.find_all("td")
        # 跳过 Table Header 行
        if not info:
            continue
        # 检查是否可用
        if "onclick" not in (start_button := info[-1].find("img")).attrs:
            continue
        # 检查奖励是否符合要求（1000 Credits）
        clear_bonus = info[-2].text.replace(",", "").removesuffix(" C")
        if int(clear_bonus) < 1000:
            continue
        # 开始战斗
        initid, inittoken = re.search(r"init_battle\((\d+),\d+,'(\w+)'\)", start_button.attrs["onclick"]).groups()
        request_with_retry(requests.post, "https://hentaiverse.org/?s=Battle&ss=ar", data={"initid": initid, "inittoken": inittoken}, **kw)
        return True

    # 如果没找到，则返回 False
    return False


def battle_with_skip_riddle(*args, **kwargs):
    while True:
        try:
            return battle(*args, **kwargs)
        except TokenNotFoundError as e:
            print("遇到小马谜题了！")
            if "function check_submit_button() {" in e.page:
                # 让谜题自动过期。如果还没过期，那就再等这么多秒
                time.sleep(20)
                continue
            raise e


def main():
    while True:
        if (arena_flag := arena()) or encounter():
            print("正在进行" + ("Arena 战斗" if arena_flag else "随机遇敌事件") + " ...")
            try:
                while True:
                    battle_with_skip_riddle()
            except TokenNotFoundError:
                # 找不到 BattleToken，可能意味着遇到小马谜题，或者战斗结束。由于小马谜题在 battle 内已经解决，所以现在只可能是战斗结束
                pass
        else:
            print("等待 10 分钟 ...")
            time.sleep(600)


if __name__ == "__main__":
    main()
