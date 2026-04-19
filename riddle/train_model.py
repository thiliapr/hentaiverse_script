from ultralytics import YOLO

model = YOLO("yolov8n.pt")

# 训练
model.train(
    data="dataset.yaml",
    epochs=1,
    imgsz=640,
    batch=16,
    device="cpu",
    workers=8,
    patience=20,
    seed=19890604,
    project="ckpt",
    exist_ok=True,
)