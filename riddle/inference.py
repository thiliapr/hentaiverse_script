# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: thiliapr/hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

# 本文件是 thiliapr/hentaiverse_script 的一部分
# thiliapr/hentaiverse_script 是自由软件，你可以依照由自由软件基金会发布的 GNU Affero 通用公共许可证分发或修改它，无论是版本 3 许可证，还是（按你的决定）任何以后版都可以。
# 发布 thiliapr/hentaiverse_script 是希望它能有用，但是并无保障，甚至连可销售和符合某个特定的目的都不保证。请参看 GNU 通用公共许可证以了解详情。
# 你应该随程序获得一份 GNU Affero 通用公共许可证的复本。如果没有，请看 <https://www.gnu.org/licenses/agpl.html>。

import pathlib
import argparse
from PIL import Image
from ultralytics import YOLO


class InferenceToolkit:
    def __init__(self, model_path: pathlib.Path):
        self.model = YOLO(model_path)

    def predict(self, images: list[Image.Image]):
        return self.model.predict([image.convert("L") for image in images])


def parse_args(args: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("filepath", type=pathlib.Path)
    parser.add_argument("--model", type=pathlib.Path, default=pathlib.Path(__file__).parent / "ckpt/train/weights/best.pt")
    return parser.parse_args(args)


def main(args: argparse.Namespace):
    tool = InferenceToolkit(args.model)
    files = [args.filepath]
    if args.filepath.is_dir():
        files = [f for f in args.filepath.rglob("*.*") if f.is_file()]

    for result in tool.predict([Image.open(file) for file in files]):
        result.show()
        input("Enter to continue.")


if __name__ == "__main__":
    main(parse_args())

