# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: thiliapr/hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

# 本文件是 thiliapr/hentaiverse_script 的一部分
# thiliapr/hentaiverse_script 是自由软件，你可以依照由自由软件基金会发布的 GNU Affero 通用公共许可证分发或修改它，无论是版本 3 许可证，还是（按你的决定）任何以后版都可以。
# 发布 thiliapr/hentaiverse_script 是希望它能有用，但是并无保障，甚至连可销售和符合某个特定的目的都不保证。请参看 GNU 通用公共许可证以了解详情。
# 你应该随程序获得一份 GNU Affero 通用公共许可证的复本。如果没有，请看 <https://www.gnu.org/licenses/agpl.html>。

import time
import requests


def request_with_retry(func, *args, **kwargs):
    while True:
        try:
            return func(*args, **kwargs, timeout=15)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            print(f"[network.request_with_retry] 网络错误: {e}")
            time.sleep(1)
