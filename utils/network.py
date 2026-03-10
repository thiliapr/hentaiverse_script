# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import requests


def request_with_retry(func, *args, retries: int = 3, **kwargs):
    for _ in range(retries):
        try:
            return func(*args, **kwargs, timeout=30)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
            print(f"网络错误: {e}")
    raise ConnectionError("网络接连发生错误，力竭了")
