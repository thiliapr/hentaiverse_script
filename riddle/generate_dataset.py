import re
import json
import random
import pathlib
import argparse
import itertools
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter
from tqdm import tqdm

RIDDLE_IMAGE_SIZE = 1000, 550
BACKGROUND_COLOR = 215, 198, 170
PONY_BRIGHTNESS = 24
ELLIPSE_BRIGHTNESS = 40
RECTANGLE_BRIGHTNESS = 24
BLACK_NOISE_BRIGHTNESS = 90


class TooManyRetriesError(Exception):
    pass


def random_merge(board: np.ndarray, pattern: np.ndarray, areas_to_avoid: set[tuple[int, int]] | None = None) -> tuple[int, int, int, int]:
    for _ in range(10):
        y, x = [random.randint(-object_length * 1 // 8, riddle_length - object_length * 7 // 8) for object_length, riddle_length in zip(pattern.shape[:2], board.shape)]

        # 处理边界情况，裁剪立绘
        real_pattern = pattern[max(-y, 0):, max(-x, 0):]
        x, y = [max(k, 0) for k in [x, y]]

        # 计算长宽，裁剪对象
        height, width = [min(start + object_length, riddle_length) - start for object_length, riddle_length, start in zip(real_pattern.shape[:2], board.shape, [y, x])]
        real_pattern = real_pattern[:height, :width]

        # 如果重叠部分比较小，就允许通过
        rows, cols = np.where(np.sum(real_pattern, axis=-1) != 0)
        rows += y
        cols += x
        valid_positions = list(zip(cols.tolist(), rows.tolist()))

        if not areas_to_avoid or sum(position in areas_to_avoid for position in valid_positions) < len(valid_positions) / 8:
            break
    else:
        raise TooManyRetriesError()

    # 与背景融合
    board[y:y + height, x:x + width] += real_pattern

    # 如果指定，添加目前区域到避免重叠的区域集
    if areas_to_avoid is not None:
        areas_to_avoid.update(valid_positions)
    return x, y, width, height


def generate_riddle(portraits: list[tuple[str, Image.Image]]) -> tuple[Image.Image, list[tuple[str, tuple[int, int, int, int]]]]:
    # 创建画布
    riddle_image = np.tile((np.random.normal(10, 3, 3) + np.array(BACKGROUND_COLOR, dtype=float))[None, None], (*reversed(RIDDLE_IMAGE_SIZE), 1))

    # 把立绘加在画布上
    portrait_positions = []
    areas_to_avoid = set()
    for pony, original_portrait in portraits:
        while True:
            # 缩放与旋转
            portrait = original_portrait.rotate(random.random() * 180, expand=True)
            portrait = portrait.crop(portrait.getbbox())
            portrait = portrait.resize([int(x * (1 / 4 + random.random() * 1 / 4)) for x in RIDDLE_IMAGE_SIZE])

            # 随机翻转
            if random.random() > 0.5:
                portrait = portrait.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

            # 转换成数组
            portrait = np.array(portrait, dtype=float)

            # 标准化并变成灰度图
            valid_mask = portrait[..., 3] == 255
            portrait = portrait[..., :3]
            portrait -= portrait[valid_mask].mean()
            portrait /= portrait[valid_mask].std()
            portrait = portrait.mean(-1)[..., None]
            portrait[~valid_mask] = 0

            # 融入背景
            try:
                portrait_positions.append((pony, random_merge(riddle_image, portrait * PONY_BRIGHTNESS * (3 / 8 * random.random() + 3 / 4), areas_to_avoid)))
                break
            except (TooManyRetriesError, ValueError):
                pass

    # 添加椭圆
    for _ in range(random.randint(10, 20)):
        img = Image.new("L", [random.randint(10, 100) for _ in range(2)], "white")
        ImageDraw.Draw(img).ellipse([0, 0, img.width, img.height], fill=random.choice([127, 254, 0]), outline=random.choice([0, 255]), width=2)
        img = np.array(img, dtype=float)
        empty_mask = img == 255
        half_mask = img == 254
        img = img / 255 - 1
        img[empty_mask] = 0
        img[half_mask] = 0.1
        random_merge(riddle_image, img[..., None] * ELLIPSE_BRIGHTNESS * (1 / 2 * random.random() + 3 / 4))

    # 添加矩形
    for _ in range(random.randint(6, 10)):
        width, height = [random.randint(0, 400) for _ in range(2)]
        x, y = [random.randint(0, k) for k in [riddle_image.shape[1] - width, riddle_image.shape[0] - height]]
        riddle_image[y:y + height, x:x + width] -= RECTANGLE_BRIGHTNESS * (0.8 + random.random() * 0.4),

    # 添加噪声
    riddle_image -= np.random.normal(0, 10 + random.random() * 10, riddle_image.shape[:2])[..., None]
    riddle_image[np.random.random(riddle_image.shape[:2]) > (0.95 + random.random() * 0.049)] -= BLACK_NOISE_BRIGHTNESS
    riddle_image = gaussian_filter(riddle_image, sigma=0.99 + random.random() * 0.009)
    riddle_image -= np.random.normal(0, 2, riddle_image.shape[:2])[..., None]

    # 合成谜题图片
    riddle_image = np.clip(riddle_image, 0, 255)
    riddle_image = Image.fromarray(np.astype(riddle_image, np.uint8))
    return riddle_image, portrait_positions


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("trainset_proportion", type=float, help="训练集占数据集的比例")
    parser.add_argument("-n", "--count", type=int, default=1, help="为每种组合生成的谜题数量")
    return parser.parse_args(args)


def main(args: argparse.Namespace):
    # 读取小马立绘
    portraits = {}
    for portrait_path in pathlib.Path("dataset/portrait").rglob("*.*"):
        pony_name = re.search(r"(.+)(\.\d+)", portrait_path.stem).group(1)
        image = Image.open(portrait_path)
        portraits.setdefault(pony_name, []).append(image)

    # 读取小马名字转 ID 表
    pony_to_id = json.loads(pathlib.Path("pony_to_id.json").read_text())

    # 生成图片
    # https://ehwiki.org/wiki/RiddleMaster
    # This can be 1, 2 or 3 different ponies.
    for ponies, data_split in tqdm([
        [combination, data_split]
        for num_ponies in range(1, 4)
        for combination in list(itertools.combinations_with_replacement(portraits.keys(), num_ponies))
        for data_split in ["train" if random.random() < args.trainset_proportion else "val"]  # 在这里决定 data_split，防止同一组合的不同谜题落入训练集和验证集
        for _ in range(args.count)
    ]):
        riddle_image, portrait_positions = generate_riddle([(pony, random.choice(portraits[pony])) for pony in ponies])

        # 拼凑标签
        label_text = []
        for pony, (x, y, w, h) in portrait_positions:
            center_x, center_y = [k + length / 2 for k, length in [(x, w), (y, h)]]
            center_x, center_y, w, h = [x / length for x, length in [(center_x, riddle_image.width), (center_y, riddle_image.height), (w, riddle_image.width), (h, riddle_image.height)]]
            label_text.append(f"{pony_to_id[pony]} {center_x} {center_y} {w} {h}")
        label_text = "\n".join(label_text)

        # 保存图片和标签
        image_dir, label_dir = [pathlib.Path(f"dataset/{data_type}/{data_split}") for data_type in ["images", "labels"]]
        for d in [image_dir, label_dir]:
            d.mkdir(parents=True, exist_ok=True)

        riddle_id = "".join(random.choice("".join(chr(ord("a") + x) for x in range(26))) for _ in range(16))
        riddle_image.save(image_dir / f"{riddle_id}.png")
        (label_dir / f"{riddle_id}.txt").write_text(label_text)


if __name__ == "__main__":
    main(parse_args())
