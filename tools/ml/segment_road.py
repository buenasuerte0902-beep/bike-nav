"""
道路セグメンテーション。Cityscapesで事前学習済みのSegFormer(b0)で画像をクラス分けし、
「路面(road)がどこまで続くか」を色ではなく意味的なクラスで判定できるようにする。
色ベースの単純なスキャン(旧実装)は影・反射・水たまり・舗装の質感差に弱かったが、
セグメンテーションはそれらに対してずっと頑健(ただし完璧ではない。README参照)。

単体実行での動作確認:
  python tools/ml/segment_road.py path/to/photo.jpg
"""
import sys

import numpy as np
import torch
from PIL import Image
from transformers import SegformerForSemanticSegmentation, SegformerImageProcessor

from imutil import imread

MODEL_NAME = "nvidia/segformer-b0-finetuned-cityscapes-1024-1024"

# Cityscapes 19クラスのtrainId
ROAD = 0
SIDEWALK = 1
TERRAIN = 9
VEGETATION = 8
CAR, TRUCK, BUS = 13, 14, 15

# 「路面(=路肩を含む舗装された走行可能面)」とみなすクラス。
# Cityscapesの標準19クラスには専用の"parking"クラスが無く、駐車場等の広い舗装面も
# road(0)に分類されがちな点は既知の限界(README参照)。
ROAD_CLASSES = {ROAD}

_model = None
_processor = None


def _load():
    global _model, _processor
    if _model is None:
        _processor = SegformerImageProcessor.from_pretrained(MODEL_NAME)
        _model = SegformerForSemanticSegmentation.from_pretrained(MODEL_NAME)
        _model.eval()
        if torch.cuda.is_available():
            _model = _model.to("cuda")
    return _model, _processor


def segment(img_bgr):
    """BGR画像(numpy) -> 元解像度にアップサンプルしたクラスIDマップ(H,W int)を返す。"""
    model, processor = _load()
    rgb = np.ascontiguousarray(img_bgr[:, :, ::-1])
    pil = Image.fromarray(rgb)
    inputs = processor(images=pil, return_tensors="pt")
    device = next(model.parameters()).device
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        logits = model(**inputs).logits
    upsampled = torch.nn.functional.interpolate(
        logits, size=img_bgr.shape[:2], mode="bilinear", align_corners=False
    )
    return upsampled.argmax(dim=1)[0].cpu().numpy()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("使い方: python tools/ml/segment_road.py path/to/photo.jpg")
    img = imread(sys.argv[1])
    seg = segment(img)
    total = seg.size
    print(f"road比率: {(seg == ROAD).sum() / total:.1%}  "
          f"sidewalk比率: {(seg == SIDEWALK).sum() / total:.1%}  "
          f"vegetation比率: {(seg == VEGETATION).sum() / total:.1%}")
