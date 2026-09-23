#!/usr/bin/env python3
"""
Phase4: 「路面の滑らかさ」の学習データをつくるための手動ラベリングツール。
data/photos/<city>/ の写真を1枚ずつ表示し、キー入力でS〜Dを付ける。

操作:
  S/A/B/C/D キー … そのグレードを付けて次の写真へ
  X キー         … 判定不能(路面が写っていない等)としてスキップ
  B キー(戻る)   … 使わない。誤操作時は再実行時に --resume を外せば上書き可能
  Q キー         … 中断（それまでのラベルは保存済み）

使い方:
  python tools/ml/label_smoothness.py --city 金沢市
  python tools/ml/label_smoothness.py --city 金沢市 --resume   # 続きから

出力:
  data/photos/<city>/_labels_smoothness.json  # {edgeId: "S"|"A"|"B"|"C"|"D"}

注意: cv2.imshow はGUI環境が必要。SSH等の画面が無い環境では動かない
(このツールはユーザーの手元のPCで写真を見ながら実行する想定)。
"""
import argparse
import json
import os
import sys

import cv2

from imutil import imread

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "data"))

GRADE_KEYS = {ord("s"): "S", ord("a"): "A", ord("b"): "B", ord("c"): "C", ord("d"): "D"}


def main():
    ap = argparse.ArgumentParser(description="路面の滑らかさラベリングツール")
    ap.add_argument("--city", default="金沢市")
    ap.add_argument("--resume", action="store_true", help="ラベル済みはスキップ")
    args = ap.parse_args()

    photo_dir = os.path.join(DATA_DIR, "photos", args.city)
    if not os.path.isdir(photo_dir):
        sys.exit(f"{photo_dir} がありません。先に tools/fetch_mapillary_images.py を実行してください。")

    labels_path = os.path.join(photo_dir, "_labels_smoothness.json")
    labels = {}
    if os.path.exists(labels_path):
        with open(labels_path, encoding="utf-8") as f:
            labels = json.load(f)

    jpgs = sorted(f for f in os.listdir(photo_dir) if f.lower().endswith(".jpg"))
    if args.resume:
        jpgs = [f for f in jpgs if os.path.splitext(f)[0] not in labels]

    if not jpgs:
        print("ラベル付けする写真がありません(全て済み、または写真が無い)")
        return

    print(f"{len(jpgs)}枚。S/A/B/C/D=グレード付与, X=スキップ, Q=中断")
    win = "路面の滑らかさラベリング — S/A/B/C/D, X=skip, Q=quit"
    for i, fname in enumerate(jpgs):
        edge_id = os.path.splitext(fname)[0]
        img = imread(os.path.join(photo_dir, fname))
        if img is None:
            continue
        cv2.putText(img, f"[{i+1}/{len(jpgs)}] {edge_id}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        cv2.imshow(win, img)
        key = cv2.waitKey(0) & 0xFF
        if key == ord("q"):
            break
        if key == ord("x"):
            continue
        if key in GRADE_KEYS:
            labels[edge_id] = GRADE_KEYS[key]
            with open(labels_path, "w", encoding="utf-8") as f:
                json.dump(labels, f, ensure_ascii=False, indent=0)

    cv2.destroyAllWindows()
    print(f"保存: {labels_path} ({len(labels)}件ラベル済み)")


if __name__ == "__main__":
    main()
