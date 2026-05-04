import argparse


def parse_args(args: list[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument("-f", "--validation-frequency", type=int, default=2 ** 10, help="验证频率（每多少次训练步验证一次），默认为 %(default)s")
    parser.add_argument("-r", "--validation-rounds", type=int, default=10, help="验证总轮数（即总共验证多少次），默认为 %(default)s")
    parser.add_argument("-b", "--batch-size", type=int, default=16, help="每个批次的样本数量，默认为 %(default)s")
    parser.add_argument("-d", "--device", type=int, action="append", default=[], help="使用的 CUDA 设备编号，不指定则代表使用 CPU")
    parser.add_argument("-p", "--patience", type=int, default=20, help="早停的耐心值，默认为 %(default)s")
    return parser.parse_args(args)


# 先解析命令行参数（快速返回帮助信息），再导入（导入是个费时的活）
if __name__ == "__main__":
    _args = parse_args()

# 其他部分
import json
import math
import pathlib
from functools import partial
from typing import Any, TypeVar
from collections.abc import Callable
import numpy as np
from ultralytics import YOLO
from ultralytics.utils import colorstr
from ultralytics.data import YOLODataset
from ultralytics.utils.torch_utils import unwrap_model
from ultralytics.models.yolo.detect.train import DetectionTrainer
from generate_dataset import RiddleGenerator

T = TypeVar("T")


def placeholder(factory: Callable[[], T]) -> Callable[..., T]:
    def do_anything(*args, **kwargs) -> T:
        return factory()
    return do_anything


class RiddleDataset(YOLODataset):
    def __init__(self, num_samples: int, pony_to_id: dict[str, int], riddle_generator_kwargs: dict[str, Any], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.num_samples = num_samples
        self.pony_to_id = pony_to_id
        self.riddle_generator = RiddleGenerator(**riddle_generator_kwargs)
        self.batch_shapes = [0]

    get_img_files = placeholder(list)
    get_labels = placeholder(list)
    update_labels = placeholder(lambda: None)
    set_rectangle = placeholder(lambda: None)

    def __len__(self) -> int:
        return self.num_samples

    def get_image_and_label(self, index: int) -> dict[str, Any]:
        # 生成谜题图像和标签
        image, label = self.riddle_generator.generate_riddle()
        image_width, image_height = image.size

        # 处理图像
        image = image.resize((self.imgsz, math.ceil(self.imgsz * image_height /  image_width)))
        image = np.array(image)

        # 处理标签
        label = {
            "im_file": "natsuiro_matsuri",
            "img": image[..., None],
            "ori_shape": (image_height, image_width),
            "resized_shape": tuple(image.shape),
            "cls": np.reshape(np.array([self.pony_to_id[pony] for pony, _ in label], dtype=np.float32), (-1, 1)),
            "bboxes": np.reshape(np.array([
                [(x + box_width / 2) / image_width, (y + box_height / 2) / image_height, box_width / image_width, box_height / image_height]
                for _, (x, y, box_width, box_height) in label
            ], dtype=np.float32), (-1, 4)),
            "normalized": True,
            "bbox_format": "xywh",
        }

        return self.update_labels_info(label)


class RiddleTrainer(DetectionTrainer):
    def __init__(self, riddle_dataset_args: list[Any], *args, **kwargs):
        self.riddle_dataset_kwargs = riddle_dataset_args
        super().__init__(*args, **kwargs)

    plot_training_labels = placeholder(lambda: None)

    def build_dataset(self, img_path: str, mode: str = "train", batch: int | None = None):
        if mode != "train":
            return super().build_dataset(img_path, mode, batch)
        stride = max(int(unwrap_model(self.model).stride.max()), 32)
        return RiddleDataset(
            *self.riddle_dataset_kwargs,
            img_path="",
            imgsz=self.args.imgsz,
            batch_size=batch,
            augment=True,
            hyp=self.args,
            rect=self.args.rect,
            cache=self.args.cache,
            single_cls=self.args.single_cls or False,
            stride=stride,
            pad=0.0,
            prefix=colorstr(f"{mode}: "),
            task=self.args.task,
            classes=self.args.classes,
            data=self.data,
            fraction=self.args.fraction,
        )


def main(args: argparse.Namespace):
    # 读取小马名字转 ID 表
    pony_to_id = json.loads(pathlib.Path("pony_to_id.json").read_text())

    # 创建模型
    model = YOLO()

    # 开始训练
    model.train(
        data="dataset.yaml",
        rect=True,
        epochs=args.validation_rounds,
        imgsz=640,
        batch=args.batch_size,
        device=args.device or "cpu",
        workers=0,
        patience=args.patience,
        seed=19890604,
        project=pathlib.Path(__file__).parent / "ckpt",
        exist_ok=True,
        trainer=partial(RiddleTrainer, (args.validation_frequency, pony_to_id, {"portrait_dir": pathlib.Path("dataset/portrait"), "background_dir": pathlib.Path("dataset/background"), "image_pin_memory": False}))
    )


if __name__ == "__main__":
    main(_args)
