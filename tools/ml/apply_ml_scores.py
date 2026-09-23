#!/usr/bin/env python3
"""
Phase4の仕上げ: 収集済み写真(data/photos/<city>/)から
  - 路肩の広さ  … estimate_shoulder_width.py (学習不要、車を物差しにした幾何推定)
  - 路面の滑らかさ … train_smoothness.py で学習した models/smoothness_model.pt
を計算し、data/graph/<city>.json の該当edgeの grades.shoulderWidth / grades.smoothness
(これまで null だった欄)に書き込み、総合ランクを再計算する。

アプリ側(js/route.js, js/map.js)のコード変更は不要
— null を含む/含まないに関わらず同じ composite ロジックで平均するため。

使い方:
  python tools/ml/apply_ml_scores.py --city 金沢市
  python tools/ml/apply_ml_scores.py --city 金沢市 --skip-smoothness  # モデル未学習でも路肩幅だけ先に反映
"""
import argparse
import json
import os
import sys

import torch
from PIL import Image

from estimate_shoulder_width import estimate_shoulder_width, grade_shoulder_width
from train_smoothness import CLASSES, TRANSFORM, build_model

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "data"))
MODEL_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "models"))
GRAPH_DIR = os.path.join(DATA_DIR, "graph")

# build_graph.py と同じ定義（グラフJSONの再計算に使う。ロジックを分けているのは
# 各ツールが単体で完結するようにするため）
GRADE_SCORE = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}


def composite(grades):
    vals = [GRADE_SCORE[g] for g in grades if g]
    if not vals:
        return None
    avg = sum(vals) / len(vals)
    for g, s in sorted(GRADE_SCORE.items(), key=lambda kv: -kv[1]):
        if avg >= s - 0.5:
            return g
    return "D"


def load_smoothness_model(device):
    path = os.path.join(MODEL_DIR, "smoothness_model.pt")
    if not os.path.exists(path):
        return None
    ckpt = torch.load(path, map_location=device)
    model = build_model()
    model.load_state_dict(ckpt["state_dict"])
    model.to(device).eval()
    return model


def predict_smoothness(model, image_path, device):
    img = Image.open(image_path).convert("RGB")
    x = TRANSFORM(img).unsqueeze(0).to(device)
    with torch.no_grad():
        pred = model(x).argmax(dim=1).item()
    return CLASSES[pred]


def main():
    ap = argparse.ArgumentParser(description="ML推定結果を道路グラフに反映")
    ap.add_argument("--city", default="金沢市")
    ap.add_argument("--skip-smoothness", action="store_true", help="smoothness_model.pt が無くても路肩幅だけ反映する")
    args = ap.parse_args()

    graph_path = os.path.join(GRAPH_DIR, f"{args.city}.json")
    if not os.path.exists(graph_path):
        sys.exit(f"{graph_path} がありません。先に tools/build_graph.py --city {args.city} を実行してください。")
    with open(graph_path, encoding="utf-8") as f:
        graph = json.load(f)

    photo_dir = os.path.join(DATA_DIR, "photos", args.city)
    meta_path = os.path.join(photo_dir, "_meta.json")
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    model = None
    if not args.skip_smoothness:
        model = load_smoothness_model(device)
        if model is None:
            print("models/smoothness_model.pt が無いため路面の滑らかさはスキップします"
                  "（tools/ml/label_smoothness.py → train_smoothness.py が未実施）")

    n_width, n_smooth = 0, 0
    for e in graph["edges"]:
        photo_path = os.path.join(photo_dir, f"{e['id']}.jpg")
        if not os.path.exists(photo_path):
            continue

        compass_angle = (meta.get(e["id"]) or {}).get("compassAngle")
        try:
            w = estimate_shoulder_width(photo_path, compass_angle=compass_angle)
        except Exception as ex:  # noqa: BLE001
            print(f"  {e['id']}: 路肩幅推定に失敗 {ex}", file=sys.stderr)
            w = None
        if w is not None:
            e["grades"]["shoulderWidth"] = grade_shoulder_width(w["widthM"])
            n_width += 1

        if model is not None:
            try:
                e["grades"]["smoothness"] = predict_smoothness(model, photo_path, device)
                n_smooth += 1
            except Exception as ex:  # noqa: BLE001
                print(f"  {e['id']}: 滑らかさ推定に失敗 {ex}", file=sys.stderr)

        e["total"] = composite(e["grades"].values())

    tmp_path = graph_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, graph_path)  # 書き込み中の中断でファイルが壊れないよう一時ファイル経由にする
    print(f"完了: 路肩幅 {n_width}件 / 滑らかさ {n_smooth}件 を反映 → {graph_path}")


if __name__ == "__main__":
    main()
