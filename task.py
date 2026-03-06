# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import json, pathlib, time, requests
from bs4 import BeautifulSoup
from utils.network import request_with_retry
from utils.battle import TokenNotFoundError
from battle_bot import battle

GALLERY_URL = "https://e-hentai.org/g/3502641/17246a289f/"
config = json.loads(pathlib.Path("config.json").read_text("utf-8"))


def encounter() -> bool:
    # 你会在 E-Hentai 上随机遇到怪兽，打这些怪兽不消耗体力，所以尽情打去吧
    cookies = {"ipb_member_id": config["ipb_member_id"], "ipb_pass_hash": config["ipb_pass_hash"]}
    headers = {"User-Agent": config["user_agent"]}

    # 发送网页请求
    resp = request_with_retry(requests.get, GALLERY_URL, headers=headers, cookies=cookies | {"event": "1"})

    # 解析检测随机遇敌事件，并点击遇敌链接
    soup = BeautifulSoup(resp.text, "lxml")
    if (eventpane := soup.find(id="eventpane")) is None:
        return False
    if (link_element := eventpane.find("a", href=True)) is None:
        return False
    request_with_retry(requests.get, link_element.attrs["href"], cookies=cookies, headers=headers)
    return True


def battle_with_skip_riddle(*args, **kwargs):
    while True:
        try:
            return battle(*args, **kwargs)
        except TokenNotFoundError as e:
            if "function check_submit_button() {" in e.page:
                # 让谜题自动过期。如果还没过期，那就再等这么多秒
                time.sleep(20)
                continue
            raise e


def main():
    while True:
        if encounter():
            print("启动战斗 ...")
            battle_with_skip_riddle()
        print("等待 10 分钟 ...")
        time.sleep(600)


if __name__ == "__main__":
    main()
