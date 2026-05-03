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

