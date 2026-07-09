# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: thiliapr/hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

# 本文件是 thiliapr/hentaiverse_script 的一部分
# thiliapr/hentaiverse_script 是自由软件，你可以依照由自由软件基金会发布的 GNU Affero 通用公共许可证分发或修改它，无论是版本 3 许可证，还是（按你的决定）任何以后版都可以。
# 发布 thiliapr/hentaiverse_script 是希望它能有用，但是并无保障，甚至连可销售和符合某个特定的目的都不保证。请参看 GNU Affero 通用公共许可证以了解详情。
# 你应该随程序获得一份 GNU Affero 通用公共许可证的复本。如果没有，请看 <https://www.gnu.org/licenses/agpl.html>。

import argparse


def parse_args(args: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--num-epochs", type=int, default=1000, help="训练的轮数，默认为 %(default)s")
    parser.add_argument("-b", "--batch-size", type=int, default=16, help="每个批次的样本数量，默认为 %(default)s")
    parser.add_argument("-d", "--device", type=int, action="append", default=[], help="使用的 CUDA 设备编号，不指定则代表使用 CPU")
    parser.add_argument("-p", "--patience", type=int, default=20, help="早停的耐心值，默认为 %(default)s")
    return parser.parse_args(args)


# 先解析命令行参数（快速返回帮助信息），再导入（导入是个费时的活）
if __name__ == "__main__":
    _args = parse_args()

# 其他部分
import pathlib
from ultralytics import YOLO


DATASET_CONFIG = """
path: ./dataset
train: images/train
val: images/valid

nc: {num_classes}
names: [{names}]
channels: 1
""".strip()


def main(args: argparse.Namespace):
    # 显示版权声明、无担保说明、许可证信息和查看方式
    print("[Info] riddle.train_model - 谜题模型训练脚本")
    print("[Info] Copyright (C) 2026 thiliapr <thiliapr@tutanota.com>")
    print("[Info] 本脚本是 thiliapr/hentaiverse 的一部分，是一个自由软件，遵循 GNU AGPL v3 or later 进行分发")
    print("[Info] thiliapr/hentaiverse_script 不提供任何保障，甚至连可销售和符合某个特定的目的都不保证")
    print("[Info] 您应该已收到一份 AGPL 副本。如果没有，请访问 https://www.gnu.org/licenses/agpl.html")
    print()

    # 根据标签文件写配置
    labels = pathlib.Path("dataset/labels.txt").read_text().strip().splitlines()
    pathlib.Path("dataset/dataset.yaml").write_text(DATASET_CONFIG.format(
        num_classes=len(labels),
        names=", ".join(f"'{label}'" for label in labels)
    ))

    # 创建模型
    model = YOLO()

    # 开始训练
    model.train(
        data="dataset/dataset.yaml",
        rect=True,
        epochs=args.num_epochs,
        imgsz=640,
        batch=args.batch_size,
        device=args.device or "cpu",
        workers=0,
        patience=args.patience,
        seed=19890604,
        project=pathlib.Path(__file__).parent / "ckpt",
        exist_ok=True,
    )


if __name__ == "__main__":
    main(_args)
