# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 thiliapr <thiliapr@tutanota.com>
# SPDX-Package: hentaiverse_script
# SPDX-PackageHomePage: https://github.com/thiliapr/hentaiverse_script

import re
import json
import random
import pathlib
import argparse
from collections.abc import Callable
import cv2
import numpy as np
from PIL import Image, ImageDraw
from tqdm import tqdm

RIDDLE_IMAGE_SIZE = 1000, 550
PONY_BRIGHTNESS = 24
ELLIPSE_SHADOW_BRIGHTNESS = 32
TRIANGLE_SHADOW_BRIGHTNESS = 32
RECTANGLE_SHADOW_BRIGHTNESS = 32
BLACK_NOISE_BRIGHTNESS = 90
OVERLAY_THRESHOLD = 2 / 3


class ImageDataLoader:
    def __init__(self, images_dir: pathlib.Path, pin_memory: bool):
        self.pin_memory = pin_memory
        self.cache = {f: None for f in images_dir.rglob("*.*")}

    @staticmethod
    def process_image(image: Image.Image) -> np.ndarray:
        return np.array(image.convert("L").resize(RIDDLE_IMAGE_SIZE), dtype=float)

    def random_image(self) -> np.ndarray:            
        if (image := self.cache[filepath := random.choice(list(self.cache.keys()))]) is None:
            image = self.process_image(Image.open(filepath))
            if self.pin_memory:
                self.cache[filepath] = image
        return image.copy()


class NoiseFactory:
    # You must make sure that image is stored with dtype=float
    @staticmethod
    def gaussian_noise(image: np.ndarray, strength: float):
        image += np.random.normal(scale=12 + 12 * strength, size=image.shape)

    @staticmethod
    def salt_pepper_noise(image: np.ndarray, strength: float):
        prob = 0.01 + 0.04 * strength
        random_map = np.random.random(image.shape)
        image[random_map < prob] = 0
        image[random_map > (1 - prob)] = 255

    @staticmethod
    def poisson_noise(image: np.ndarray, strength: float):
        lam = 0.3 + 0.7 * (1 - strength)
        image[:] = np.random.poisson(np.clip(image, 0, 255) * lam) / lam

    @staticmethod
    def multiplicative_noise(image: np.ndarray, strength: float):
        image *= np.random.normal(1, 0.05 + 0.25 * strength, image.shape)

    @staticmethod
    def gaussian_blur(image: np.ndarray, strength: float):
        image[:] = cv2.GaussianBlur(image, (7, 7), 0.5 + 2.5 * strength)

    @staticmethod
    def motion_blur(image: np.ndarray, strength: float):
        # 创建一维运动核
        kernel_size = 5 + 2 * int(7 * strength)
        kernel_1d = np.linspace(0.1, 1.0, kernel_size) if random.randint(0, 1) else np.ones(kernel_size)
        kernel_1d = kernel_1d / np.sum(kernel_1d)

        # 根据角度生成二维核的投影
        shape = random.choice([(1, -1), (-1, 1)])
        kernel = kernel_1d.reshape(*shape)

        # 归一化并应用
        kernel = kernel / np.sum(kernel)
        image[:] = cv2.filter2D(image, -1, kernel)

    @staticmethod
    def downsample_upsample(image: np.ndarray, strength: float):
        scale = 0.3 + 0.6 * (1 - strength)
        h, w = image.shape
        x = image
        for new_w, new_h in [(int(w * scale), int(h * scale)), (w, h)]:
            x = cv2.resize(x, (new_w, new_h), interpolation=random.choice([cv2.INTER_NEAREST, cv2.INTER_AREA]))
        image[:] = x

    @classmethod
    def functions(cls) -> list[tuple[str, Callable[[np.ndarray, float], None]]]:
        return [(name, getattr(NoiseFactory, name)) for name in [
            "downsample_upsample",
            "gaussian_blur",
            "gaussian_noise",
            "motion_blur",
            "multiplicative_noise",
            "poisson_noise",
            "salt_pepper_noise"
        ]]


class RiddleGenerator:
    def __init__(self, portrait_dir: pathlib.Path, background_dir: pathlib.Path, image_pin_memory: bool):
        # 读取小马立绘
        self.portraits: dict[str, list[Image.Image]] = {}
        for portrait_path in portrait_dir.rglob("*.*"):
            pony_name = re.search(r"(.+)(\.\d+)", portrait_path.stem).group(1)
            image = Image.open(portrait_path)
            self.portraits.setdefault(pony_name, []).append(image)

        # 创建背景图片加载器
        self.background_loader = ImageDataLoader(background_dir, image_pin_memory)

    def generate_background(self) -> np.ndarray:
        if random.random() < 0.9:
            image = self.background_loader.random_image()
            image = 64 + image / np.clip(image.max(), 1, 255) * 128
        else:
            image = np.full(tuple(reversed(RIDDLE_IMAGE_SIZE)), random.randint(100, 200), dtype=float)
        return image

    def add_noise(self, image: np.ndarray):
        if random.random() < 0.1:
            return
        weights = [random.random() for _ in range(random.randint(1, len(NoiseFactory.functions())))]
        weights = [weight / sum(weights) for weight in weights]
        for weight, (_, func) in zip(weights, random.choices(NoiseFactory.functions(), k=len(weights))):
            func(image, weight)

    def add_ponies(self, board: np.ndarray, portraits: list[Image.Image]) -> list[tuple[int, int, int, int]]:
        pony_positions = []
        for original_portrait in portraits:
            while True:
                # 随机缩放、旋转、翻转，并转换为 NumPy 数组
                portrait = original_portrait.rotate(random.randint(1, 359), expand=True)
                portrait = portrait.crop(portrait.getbbox())
                portrait = portrait.resize([int(x * (1 / 4 + 1 / 2 * random.random())) for x in RIDDLE_IMAGE_SIZE])
                if random.random() > 0.5:
                    portrait = portrait.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                portrait = np.array(portrait, dtype=float)

                # 标准化并变成灰度图
                valid_mask = portrait[..., 3] == 255
                portrait = portrait[..., :3]
                portrait = (portrait - portrait[valid_mask].mean()) / portrait[valid_mask].std()
                portrait = portrait.mean(-1)
                portrait[~valid_mask] = 0

                # 随机变亮/暗
                portrait[valid_mask] += 2 - 4 * random.random()

                # 随机选取一个位置放置立绘，并处理边界情况，裁剪立绘
                for _ in range(10):
                    pattern_y, pattern_x = [random.randint(-object_length * 1 // 8, board_length - object_length * 7 // 8) for object_length, board_length in zip(portrait.shape[:2], reversed(RIDDLE_IMAGE_SIZE))]
                    pattern = portrait[max(-pattern_y, 0):, max(-pattern_x, 0):]
                    pattern_x, pattern_y = [max(k, 0) for k in [pattern_x, pattern_y]]
                    pattern_height, pattern_width = [min(start + object_length, board_length) - start for object_length, board_length, start in zip(pattern.shape[:2], reversed(RIDDLE_IMAGE_SIZE), [pattern_y, pattern_x])]
                    pattern = pattern[:pattern_height, :pattern_width]

                    # 计算重叠部分，决定是否通过
                    for area_x, area_y, area_width, area_height in pony_positions:
                        overlay_width = max(0, min(pattern_x + pattern_width, area_x + area_width) - max(pattern_x, area_x))
                        overlay_height = max(0, min(pattern_y + pattern_height, area_y + area_height) - max(pattern_y, area_y))
                        if overlay_width * overlay_height > min(area_width * area_height, pattern_width * pattern_height) * OVERLAY_THRESHOLD:
                            break
                    else:
                        break
                else:
                    # 如果试了若干次位置都放不下，说明立绘可能太大了，重新调整
                    continue
                break

            # 现在放置立绘
            board[pattern_y:pattern_y + pattern_height, pattern_x:pattern_x + pattern_width] += pattern * (0.8 + 0.4 * random.random()) * PONY_BRIGHTNESS
            pony_positions.append((pattern_x, pattern_y, pattern_width, pattern_height))

        return pony_positions

    def add_rectangle_shadow(self, board: np.ndarray):
        width, height = [random.randint(board_length // 8, board_length // 4) for board_length in RIDDLE_IMAGE_SIZE]
        x, y = [random.randint(0, board_length - pattern_length) for board_length, pattern_length in zip(RIDDLE_IMAGE_SIZE, (width, height))]
        bright_or_dark = random.choice([1, -1])
        brightness = (0.4 + 0.8 * random.random()) * RECTANGLE_SHADOW_BRIGHTNESS
        board[y:y + height, x:x + width] += bright_or_dark * brightness

    def add_non_rectangular_shadow(self, board: np.ndarray, pattern: Image.Image, brightness: float):
        # 读取边界和内容
        pattern = np.array(pattern)
        outline_mask = pattern == 1
        content_mask = pattern == 2
        
        # 构造图案
        pattern = np.zeros_like(pattern, dtype=float)
        pattern[outline_mask] = random.choice([1, -1])
        pattern[content_mask] = random.choice([1, -1]) * (0.5 + 0.4 * random.random())

        # 找个位置放上去
        height, width = pattern.shape
        x, y = [random.randint(0, board_length - pattern_length) for board_length, pattern_length in zip(RIDDLE_IMAGE_SIZE, (width, height))]
        board[y:y + height, x:x + width] += pattern * brightness

    def add_shadow(self, board: np.ndarray):
        # 长方形阴影
        for _ in range(random.randint(0, 16)):
            self.add_rectangle_shadow(board)

        # 椭圆阴影
        for _ in range(random.randint(0, 16)):
            pattern = Image.new("L", [random.randint(board_length // 8, board_length // 6) for board_length in RIDDLE_IMAGE_SIZE], 0)
            ImageDraw.Draw(pattern).ellipse([0, 0, pattern.width, pattern.height], fill=2, outline=1, width=random.randint(0, 3))
            self.add_non_rectangular_shadow(board, pattern, (0.8 + 0.4 * random.random()) * ELLIPSE_SHADOW_BRIGHTNESS)

        # 三角形阴影
        for _ in range(random.randint(0, 16)):
            pattern = Image.new("L", [random.randint(board_length // 8, board_length // 6) for board_length in RIDDLE_IMAGE_SIZE], 0)
            vertices = [(
                random.randint(0, pattern.width) if edge in [0, 1] else ((edge - 2) * pattern.width),
                random.randint(0, pattern.height) if edge in [2, 3] else (edge * pattern.height),
            ) for edge in random.sample([0, 1, 2, 3], 3)]  # up, down, left, right
            ImageDraw.Draw(pattern).polygon(vertices, fill=2, outline=1, width=random.randint(0, 3))
            self.add_non_rectangular_shadow(board, pattern, (0.8 + 0.4 * random.random()) * TRIANGLE_SHADOW_BRIGHTNESS)

    def generate_riddle(self) -> tuple[Image.Image, list[tuple[str, tuple[int, int, int, int]]]]:
        ponies, portraits = [], []
        if result := list(zip(*[(pony, random.choice(portrait_versions)) for pony, portrait_versions in random.sample(list(self.portraits.items()), k=random.randint(0, 3))])):
            ponies, portraits = result
        
        riddle = self.generate_background()
        pony_positions = self.add_ponies(riddle, portraits)
        pony_labels = [(pony, position) for pony, position in zip(ponies, pony_positions, strict=True)]
        self.add_shadow(riddle)
        self.add_noise(riddle)
        riddle = Image.fromarray(np.clip(riddle, 0, 255).astype(np.uint8))
        return riddle, pony_labels


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--count", type=int, default=1, help="生成的谜题数量")
    parser.add_argument("-d", "--portrait-dir", type=pathlib.Path, default=pathlib.Path("dataset/portrait"), help="小马立绘图片文件夹，默认为 %(default)s")
    parser.add_argument("-b", "--background-dir", type=pathlib.Path, required=True, help="背景图片文件夹")
    parser.add_argument("-p", "--pin-memory", action="store_true", help="是否在内存储存背景图片以更快生成谜题，同时消耗更多内存")
    return parser.parse_args(args)


def main(args: argparse.Namespace):
    # 读取小马名字转 ID 表
    pony_to_id = json.loads(pathlib.Path("pony_to_id.json").read_text())

    # 获取小马谜题生成器
    riddle_generator = RiddleGenerator(args.portrait_dir, args.background_dir, args.pin_memory)

    # 生成图片
    # https://ehwiki.org/wiki/RiddleMaster
    # This can be 1, 2 or 3 different ponies.
    for _ in tqdm(range(args.count)):
        riddle_image, labels = riddle_generator.generate_riddle()

        # 拼凑标签
        label_text = []
        for pony, (x, y, w, h) in labels:
            center_x, center_y = [k + length / 2 for k, length in [(x, w), (y, h)]]
            center_x, center_y, w, h = [x / length for x, length in [(center_x, riddle_image.width), (center_y, riddle_image.height), (w, riddle_image.width), (h, riddle_image.height)]]
            label_text.append(f"{pony_to_id[pony]} {center_x} {center_y} {w} {h}")
        label_text = "\n".join(label_text)

        # 保存图片和标签
        image_dir, label_dir = [pathlib.Path(f"dataset/{data_type}/val") for data_type in ["images", "labels"]]
        for d in [image_dir, label_dir]:
            d.mkdir(parents=True, exist_ok=True)

        riddle_id = "".join(random.choice("".join(chr(ord("a") + x) for x in range(26))) for _ in range(16))
        riddle_image.save(image_dir / f"{riddle_id}.png")
        (label_dir / f"{riddle_id}.txt").write_text(label_text)


if __name__ == "__main__":
    main(parse_args())
