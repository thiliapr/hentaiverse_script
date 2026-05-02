from ultralytics import YOLO

model = YOLO()

# 训练
model.train(
    data="dataset.yaml",
    epochs=8,
    imgsz=640,
    batch=16,
    device="cpu",
    workers=8,
    patience=20,
    seed=19890604,
    project="ckpt",
    exist_ok=True,
)
