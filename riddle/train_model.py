# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

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
val: images/val

nc: [{num_classes}]
names: [{names}]
channels: 1
""".strip()


def main(args: argparse.Namespace):
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
