if __name__ == "__main__":
    # 解析命令行参数
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=8, help="训练的轮数，默认为 %(default)s")
    parser.add_argument("--batch-size", type=int, default=16, help="每个批次的样本数量，默认为 %(default)s")
    parser.add_argument("--device", type=int, action="append", default=[], help="使用的 CUDA 设备编号，不指定则代表使用 CPU")
    parser.add_argument("--workers", type=int, default=8, help="数据加载的工作线程数量，默认为 %(default)s")
    parser.add_argument("--patience", type=int, default=20, help="早停的耐心值，默认为 %(default)s")
    args = parser.parse_args()

    # 训练
    import pathlib
    from generate_dataset import RIDDLE_IMAGE_SIZE
    from ultralytics import YOLO
    model = YOLO()
    model.train(
        data="dataset.yaml",
        rect=True,
        epochs=args.epochs,
        imgsz=RIDDLE_IMAGE_SIZE[0],
        batch=args.batch_size,
        device="cpu" if args.device else args.device,
        workers=args.workers,
        patience=args.patience,
        seed=19890604,
        project=pathlib.Path(__file__).parent / "runs",
        name="ckpt",
        exist_ok=True,
    )
