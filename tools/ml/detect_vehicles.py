#!/usr/bin/env python3
"""
Phase4: 事前学習済みYOLOv8(COCO)で写真内の車(car/bus/truck)を検出する。
学習は不要（既存の重みをそのまま使う）。estimate_shoulder_width.py から呼ばれる。

単体実行での動作確認:
  python tools/ml/detect_vehicles.py path/to/photo.jpg
"""
import os
import sys

from ultralytics import YOLO

MODEL_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "models", "yolov8n.pt"))

# COCOのクラスID: 2=car, 5=bus, 7=truck
VEHICLE_CLASS_IDS = {2: "car", 5: "bus", 7: "truck"}
# 車種ごとの平均的な実幅(m)。路肩幅推定の「物差し」に使う
VEHICLE_WIDTH_M = {"car": 1.75, "bus": 2.5, "truck": 2.3}

_model = None


def get_model():
    global _model
    if _model is None:
        _model = YOLO(MODEL_PATH)  # 初回はmodels/にCOCO事前学習済み重みを自動ダウンロード
    return _model


def detect_vehicles(image, conf=0.35):
    """画像内の車両を検出して [{cls, widthM, box:[x1,y1,x2,y2], conf}, ...] を返す（画像下端に近い順）。
    imageはファイルパス、またはBGRのnumpy配列（equirect.to_perspective()の出力など）どちらでも良い。"""
    model = get_model()
    source = str(image) if isinstance(image, (str, os.PathLike)) else image
    results = model.predict(source=source, conf=conf, verbose=False)
    out = []
    for r in results:
        for box in r.boxes:
            cls_id = int(box.cls[0])
            if cls_id not in VEHICLE_CLASS_IDS:
                continue
            cls_name = VEHICLE_CLASS_IDS[cls_id]
            x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
            out.append({
                "cls": cls_name,
                "widthM": VEHICLE_WIDTH_M[cls_name],
                "box": [x1, y1, x2, y2],
                "conf": float(box.conf[0]),
            })
    # 画像下端(=カメラに近い)ほど信頼できる物差しになるのでy2降順で並べる
    out.sort(key=lambda v: v["box"][3], reverse=True)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("使い方: python tools/ml/detect_vehicles.py path/to/photo.jpg")
    for v in detect_vehicles(sys.argv[1]):
        print(v)
