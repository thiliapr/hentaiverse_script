if __name__ == "__main__":
    # 解析命令行参数
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-e", "--epochs", type=int, default=40, help="训练的轮数，默认为 %(default)s")
    parser.add_argument("-b", "--batch-size", type=int, default=16, help="每个批次的样本数量，默认为 %(default)s")
    parser.add_argument("-d", "--device", type=int, action="append", default=[], help="使用的 CUDA 设备编号，不指定则代表使用 CPU")
    parser.add_argument("-w", "--workers", type=int, default=2, help="数据加载的工作线程数量，默认为 %(default)s")
    parser.add_argument("-p", "--patience", type=int, default=20, help="早停的耐心值，默认为 %(default)s")
    parser.add_argument("-r", "--resume", action="store_true", help="是否继续上次的训练")
    args = parser.parse_args()

    # 训练
    import pathlib
    from ultralytics import YOLO
    model = YOLO()
    model.train(
        data="dataset.yaml",
        rect=True,
        epochs=args.epochs,
        imgsz=640,
        batch=args.batch_size,
        device=args.device or "cpu",
        workers=args.workers,
        patience=args.patience,
        seed=19890604,
        project=pathlib.Path(__file__).parent / "ckpt",
        exist_ok=True,
        resume=args.resume,
    )
