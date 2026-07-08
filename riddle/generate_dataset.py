# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: thiliapr/hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

# 本文件是 thiliapr/hentaiverse_script 的一部分
# thiliapr/hentaiverse_script 是自由软件，你可以依照由自由软件基金会发布的 GNU Affero 通用公共许可证分发或修改它，无论是版本 3 许可证，还是（按你的决定）任何以后版都可以。
# 发布 thiliapr/hentaiverse_script 是希望它能有用，但是并无保障，甚至连可销售和符合某个特定的目的都不保证。请参看 GNU 通用公共许可证以了解详情。
# 你应该随程序获得一份 GNU 通用公共许可证的复本。如果没有，请看 <https://www.gnu.org/licenses/>。

# 先解析参数，再导入模块并定义函数，加快参数解析速度
import pathlib
import argparse


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-bg", "--background-dir", type=pathlib.Path, default=pathlib.Path(__file__).parent / "dataset/background", help="背景图像文件夹路径")
    parser.add_argument("-ta", "--target-dir", type=pathlib.Path, default=pathlib.Path(__file__).parent / "dataset/target", help="目标图像文件夹路径")
    parser.add_argument("-b", "--batch-size", type=int, default=32, help="批次大小")
    parser.add_argument("-tr", "--train-iterations", type=int, default=256, help="训练迭代次数，训练集大小为 train_iterations * batch_size")
    parser.add_argument("-vr", "--validation-iterations", type=int, default=32, help="验证迭代次数，验证集大小为 validation_iterations * batch_size")
    parser.add_argument("-l", "--label-file", type=pathlib.Path, default=pathlib.Path(__file__).parent / "dataset/labels.txt", help="标签文件路径，每一行是一个类别名称，顺序决定了类别索引")
    parser.add_argument("-o", "--output-dir", type=pathlib.Path, default=pathlib.Path(__file__).parent / "dataset", help="输出数据集文件夹路径")
    parser.add_argument("-d", "--device", default="cpu", help="设备")
    return parser.parse_args(args)


if __name__ == "__main__":
    _args = parse_args()

# 其他部分
import random
import itertools
import torch
import numpy as np
from tqdm import tqdm
from PIL import Image
from kornia.filters import gaussian_blur2d

BOARD_IMAGE_SIZE = 1000, 550
MAX_RECTANGLE_SHADOWS = 16
SHADOW_MIN_BRIGHTNESS = 32
SHADOW_RANDOM_BRIGHTNESS = 8


class NoiseFactory:
    """
    For each noise function:
        Args: 
            image: [batch_size, height, width]
            strength: [batch_size]
        Returns:
            noise: [batch_size, height, width]
    """

    @staticmethod
    def gaussian_noise(image: torch.Tensor, strength: torch.Tensor) -> torch.Tensor:
        return (12 + 12 * strength.view(-1, 1, 1)) * torch.randn_like(image)

    @staticmethod
    def salt_pepper_noise(image: torch.Tensor, strength: torch.Tensor) -> torch.Tensor:
        prob = (0.01 + 0.09 * strength).view(-1, 1, 1)
        salt_prob = prob * torch.rand(image.shape[0], 1, 1, device=image.device)
        pepper_prob = prob - salt_prob

        random_map = torch.rand_like(image)
        noise = torch.zeros_like(image)
        noise[random_map < salt_prob] = -255
        noise[random_map > 1 - pepper_prob] = 255
        return noise

    @staticmethod
    def poisson_noise(image: torch.Tensor, strength: torch.Tensor) -> torch.Tensor:
        lam = (0.3 + 0.7 * (1 - strength)).view(-1, 1, 1)
        return torch.poisson(image * lam) / lam - image

    @staticmethod
    def multiplicative_noise(image: torch.Tensor, strength: torch.Tensor) -> torch.Tensor:
        return (0.05 + 0.25 * strength.view(-1, 1, 1)) * torch.randn_like(image) * image

    @staticmethod
    def gaussian_blur(image: torch.Tensor, strength: torch.Tensor) -> torch.Tensor:
        sigma = 0.5 + 2.5 * strength.view(-1, 1).expand(-1, 2)
        return gaussian_blur2d(image.unsqueeze(1), kernel_size=7, sigma=sigma).squeeze(1) - image

    noise_functions = [
        gaussian_noise,
        salt_pepper_noise,
        poisson_noise,
        multiplicative_noise,
        gaussian_blur,
    ]


class BackgroundLoader:
    def __init__(self, image_dir: pathlib.Path):
        self.cache = {k: None for k in image_dir.rglob("*.*")}
        self.cache_items = list(self.cache.items())

    def cache_image(self, image_path: pathlib.Path):
        image = Image.open(image_path)
        image = image.resize(BOARD_IMAGE_SIZE)
        image = image.convert("RGB")
        image = torch.from_numpy(np.array(image))
        self.cache[image_path] = image
        return image

    def random(self) -> torch.Tensor:
        image_path, image = random.choice(self.cache_items)
        if image is None:
            image = self.cache_image(image_path)
        return image


class TargetLoader:
    """
    加载目标图像，并根据文件名将它们分成不同的类别。每个类别对应一个目标类型，文件名的格式为 "<class_name>#<variant>.<ext>"
    """

    def __init__(self, image_dir: pathlib.Path):
        images = [(image_path.stem.rsplit("#", 1)[0], image_path) for image_path in image_dir.rglob("*.*")]
        self.targets = {
            class_name: [self.load_and_process(image_path) for _, image_path in image_paths]
            for class_name, image_paths in itertools.groupby(images, key=lambda x: x[0])
        }

    def load_and_process(self, image_path: pathlib.Path) -> Image.Image:
        image = Image.open(image_path)
        image = image.crop(image.getbbox(alpha_only=True))
        return image

    def random(self) -> tuple[str, Image.Image]:
        class_name, target_images = random.choice(list(self.targets.items()))
        target_image = random.choice(target_images)
        return class_name, target_image


class ShadowGenerator:
    def __init__(self, device: torch.device):
        self.device = device
        self.xx, self.yy = [
            torch.arange(length, device=device).view(shape).expand(BOARD_IMAGE_SIZE[1], BOARD_IMAGE_SIZE[0])[None, None, ...]
            for length, shape in zip(BOARD_IMAGE_SIZE, [[1, -1], [-1, 1]])
        ]

    def generate_rectangle_shadow(self, batch_size: int) -> torch.Tensor:
        # 生成随机数量的长方形参数
        rect_params = torch.rand(batch_size, 4, MAX_RECTANGLE_SHADOWS, device=self.device)
        rect_masked = torch.arange(MAX_RECTANGLE_SHADOWS, device=self.device).view(1, 1, -1).expand(batch_size, 4, -1) < torch.randint(0, MAX_RECTANGLE_SHADOWS + 1, [batch_size, 1, 1], device=self.device)

        # 调整长方形的长和宽，并应用长方形数量掩码
        rect_params[:, 2:] = 1 / 6 + 1 / 3 * rect_params[:, 2:]
        rect_params = rect_params.masked_fill(~rect_masked, 0)

        # 解包并解析长方形参数
        rect_x, rect_y, rect_width, rect_height = rect_params[..., None, None].unbind(dim=1)  # [batch_size, MAX_RECTANGLE_SHADOWS, 1, 1]
        rect_x, rect_y = [rect_param * (length - 1) for length, rect_param in zip(BOARD_IMAGE_SIZE, [rect_x, rect_y])]
        rect_width = rect_width * (BOARD_IMAGE_SIZE[0] - rect_x)
        rect_height = rect_height * (BOARD_IMAGE_SIZE[1] - rect_y)

        # 生成长方形阴影
        shadow = (rect_x <= self.xx) & (self.xx < rect_x + rect_width) & (rect_y <= self.yy) & (self.yy < rect_y + rect_height)
        shadow = shadow.float() * torch.where(torch.randint(high=2, size=[batch_size, MAX_RECTANGLE_SHADOWS, 1, 1], dtype=bool, device=self.device), -1, 1)
        shadow = shadow * (SHADOW_MIN_BRIGHTNESS + SHADOW_RANDOM_BRIGHTNESS * torch.rand(batch_size, MAX_RECTANGLE_SHADOWS, 1, 1))
        
        # 合并每个批次阴影
        shadow = shadow.sum(dim=1)
        return shadow


class RiddleGenerator:
    def __init__(self, background_dir: pathlib.Path, target_dir: pathlib.Path, device: torch.device = torch.device("cpu")):
        self.background_loader = BackgroundLoader(background_dir)
        self.target_loader = TargetLoader(target_dir)
        self.shadow_generator = ShadowGenerator(device)
        self.device = device

    def __image_getbbox(self, image: Image.Image) -> tuple[int, int, int, int]:
        # 裁剪白色区域
        image = np.array(image)
        image[np.mean(image[..., :3], axis=2) == 255] = 0
        return Image.fromarray(image).getbbox(alpha_only=False)

    def generate_background(self, batch_size: int) -> torch.Tensor:
        rgb_weights = 0.1 + 0.9 * torch.rand(batch_size, 1, 1, 3, device=self.device)
        rgb_weights = rgb_weights / rgb_weights.sum(dim=3, keepdim=True)

        board = torch.stack([self.background_loader.random() for _ in range(batch_size)]).float().to(self.device)
        board = (board * rgb_weights).sum(dim=3)
        board = 64 + board / 255 * 128
        return board

    def generate_targets(self, batch_size: int) -> tuple[torch.Tensor, list[list[tuple[str, float, float, float, float]]]]:
        image = torch.zeros((batch_size, *reversed(BOARD_IMAGE_SIZE)), dtype=torch.float32, device=self.device)
        labels = []

        # 生成目标并添加到图像
        for batch_idx in range(batch_size):
            batch_labels = []
            for _ in range(random.randint(0, len(self.target_loader.targets))):
                class_name, target_image = self.target_loader.random()

                # 随机翻转、旋转、缩放
                if random.random() < 0.5:
                    target_image = target_image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                target_image = target_image.rotate(random.uniform(0, 360), expand=True, fillcolor="white")
                target_image = target_image.crop(self.__image_getbbox(target_image))
                pattern_size = pattern_width, pattern_height = [max(1, int(random.uniform(length * 0.15, length * 0.3))) for length in BOARD_IMAGE_SIZE]
                target_image = target_image.resize((pattern_width, pattern_height))

                # 随机放置目标图像
                pattern_position = pattern_x, pattern_y = [random.randint(0, board_length - pattern_length) for board_length, pattern_length in zip(BOARD_IMAGE_SIZE, target_image.size)]

                # 转换成 tensor，并变成灰度图
                target_image = torch.from_numpy(np.array(target_image)).float().to(self.device)
                if target_image.ndim != 3:
                    target_image = target_image.unsqueeze(2)

                valid_mask = target_image[..., :3].mean(dim=2) < 255
                if target_image.shape[2] == 4:
                    valid_mask &= target_image[..., 3] == 255
                target_image = target_image * (0.1 + torch.rand(1, 1, target_image.shape[2], device=self.device) * 0.9)
                target_image = target_image.sum(dim=2)

                target_image = (target_image - target_image.mean()) / (target_image.std() + 1e-8)
                target_image = target_image * valid_mask * (24 + 12 * torch.rand(1, 1, device=self.device))

                # 将目标图像添加到背景图像
                image[batch_idx, pattern_y:pattern_y + pattern_height, pattern_x:pattern_x + pattern_width] += target_image

                # 计算 YOLO 格式的标签，并添加到标签列表中
                center_x, center_y = [(pattern_pos + pattern_length / 2) / board_length for pattern_pos, pattern_length, board_length in zip(pattern_position, pattern_size, BOARD_IMAGE_SIZE)]
                pattern_width, pattern_height = [pattern_length / board_length for pattern_length, board_length in zip(pattern_size, BOARD_IMAGE_SIZE)]
                batch_labels.append((class_name, center_x, center_y, pattern_width, pattern_height))
            labels.append(batch_labels)
        return image, labels

    def generate_shadow(self, batch_size: int) -> torch.Tensor:
        # 生成长方形阴影
        shadow = self.shadow_generator.generate_rectangle_shadow(batch_size)
        return shadow

    def add_noise(self, image: torch.Tensor) -> torch.Tensor:
        batch_size = image.shape[0]
        functions = NoiseFactory.noise_functions.copy()

        # 随机分配噪声强度
        num_noises = torch.randint(0, len(functions) + 1, [1, batch_size], device=self.device)  # [1, batch_size]
        noise_mask = torch.arange(len(functions), device=self.device).unsqueeze(1) < num_noises  # [len(functions), batch_size]
        noise_mask = noise_mask.gather(dim=0, index=torch.argsort(torch.rand(len(functions), batch_size, device=self.device), dim=0))

        strength = torch.rand(len(functions), batch_size, device=self.device)
        strength = strength * noise_mask.float()
        strength = strength / torch.clamp(strength.sum(dim=0, keepdim=True), min=1e-8)
        strength = strength * torch.rand(1, batch_size, device=self.device)

        # 随机应用噪声函数
        random.shuffle(functions)
        for function, batch_strength, batch_mask in zip(functions, strength, noise_mask):
            noise = function(image, batch_strength) * batch_mask.view(-1, 1, 1)
            image = torch.clamp(image + noise, 0, 255)

        return image


    def generate(self, batch_size: int) -> tuple[torch.Tensor, list[list[tuple[str, float, float, float, float]]]]:
        """
        生成一个批次的谜题数据

        Returns:
            images: [batch_size, height, width]
            labels: [batch_size, tuple[ClassName, CenterX, CenterY, Width, Height]]
        """
        # 加载背景图像
        board = self.generate_background(batch_size)

        # 生成目标并添加到图像
        image, labels = self.generate_targets(batch_size)
        board = torch.clamp(board + image, 0, 255)

        # 添加阴影
        board = torch.clamp(board + self.generate_shadow(batch_size), 0, 255)

        # 添加噪声
        board = self.add_noise(board)
        return board.to(dtype=torch.uint8), labels


def main(args: argparse.Namespace):
    # 创建生成器实例
    generator = RiddleGenerator(args.background_dir, args.target_dir, torch.device(args.device))
    
    # 获取标签列表
    label = generator_label = [class_name for class_name in generator.target_loader.targets.keys()]
    if args.label_file.exists():
        label = [class_name for class_name in args.label_file.read_text().splitlines()]
        if not set(generator_label).issuperset(set(label)):
            err = RuntimeError("标签文件未包含所有目标类别: " + ", ".join(set(generator_label) - set(label)))
            err.add_note("您可以删除标签文件，让程序自动生成一个新的标签文件，或者手动编辑标签文件以包含所有目标类别。")
            raise err
    else:
        args.label_file.write_text("\n".join(label))

    # 生成数据集
    for dataset_split, num_iterations in [("train", args.train_iterations), ("valid", args.validation_iterations)]:
        image_dir, label_dir = [args.output_dir / data_type / dataset_split for data_type in ["images", "labels"]]
        image_dir.mkdir(parents=True, exist_ok=True)
        label_dir.mkdir(parents=True, exist_ok=True)

        for _ in tqdm(range(num_iterations), desc=f"Generating {dataset_split} dataset"):
            images, labels = generator.generate(args.batch_size)
            for batch_idx in range(args.batch_size):
                file_id = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=16))
                Image.fromarray(images[batch_idx].cpu().numpy()).save(image_dir / f"{file_id}.png")
                (label_dir / f"{file_id}.txt").write_text("\n".join(
                    f"{label.index(class_name)} {center_x:.6f} {center_y:.6f} {width:.6f} {height:.6f}"
                    for class_name, center_x, center_y, width, height in labels[batch_idx]
                ))


if __name__ == "__main__":
    main(_args)
