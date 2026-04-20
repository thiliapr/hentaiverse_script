from ultralytics import YOLO

model = YOLO(r"runs/detect/ckpt/train/weights/best.pt")
results = model("dataset/uncategorized/7.jpg")
results[0].show()
