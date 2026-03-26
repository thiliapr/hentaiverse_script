# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import time
import requests


def request_with_retry(func, *args, **kwargs):
    while True:
        try:
            return func(*args, **kwargs, timeout=15)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            print(f"[network.request_with_retry] 网络错误: {e}")
            time.sleep(1)
