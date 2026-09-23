#!/usr/bin/env python3
"""
Phase4: label_smoothness.py で付けたラベルを使い、路面の滑らかさ(S〜D)を
判定する軽量な画像分類モデルを fine-tuning する。

事前学習済み MobileNetV3-Small (ImageNet) の最終層だけ5クラス(S/A/B/C/D)に
差し替えて学習する。GPUがあれば自動で使う(このPCではRTX 3080を検出済み)。

使い方:
  python tools/ml/train_smoothness.py --city 金沢市
  python tools/ml/train_smoothness.py --selftest   # 合成データで動作確認のみ

出力:
  models/smoothness_model.pt  … apply_ml_scores.py が読み込む
"""
import argparse
import json
import os
import random
import sys

import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms
from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "data"))
MODEL_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "models"))
CLASSES = ["S", "A", "B", "C", "D"]

TRANSFORM = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


class SmoothnessDataset(Dataset):
    def __init__(self, samples):
        self.samples = samples  # [(image_path, class_index), ...]

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, i):
        path, label = self.samples[i]
        img = Image.open(path).convert("RGB")
        return TRANSFORM(img), label


def build_model():
    model = mobilenet_v3_small(weights=MobileNet_V3_Small_Weights.DEFAULT)
    in_features = model.classifier[-1].in_features
    model.classifier[-1] = torch.nn.Linear(in_features, len(CLASSES))
    return model


def train(samples, epochs, batch_size, val_split, device):
    random.shuffle(samples)
    n_val = max(1, int(len(samples) * val_split)) if len(samples) > 4 else 0
    val_samples, train_samples = samples[:n_val], samples[n_val:]

    train_loader = DataLoader(SmoothnessDataset(train_samples), batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(SmoothnessDataset(val_samples), batch_size=batch_size) if val_samples else None

    model = build_model().to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-4)
    loss_fn = torch.nn.CrossEntropyLoss()

    best_acc = -1.0
    best_state = None
    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            out = model(x)
            loss = loss_fn(out, y)
            loss.backward()
            opt.step()
            total_loss += loss.item() * x.size(0)
        avg_loss = total_loss / max(1, len(train_samples))

        acc = None
        if val_loader:
            model.eval()
            correct = 0
            with torch.no_grad():
                for x, y in val_loader:
                    x, y = x.to(device), y.to(device)
                    pred = model(x).argmax(dim=1)
                    correct += (pred == y).sum().item()
            acc = correct / len(val_samples)
            if acc >= best_acc:
                best_acc = acc
                best_state = model.state_dict()
        print(f"  epoch {epoch}/{epochs}: loss={avg_loss:.4f}" + (f" val_acc={acc:.2%}" if acc is not None else ""))

    return best_state or model.state_dict()


def load_city_samples(city):
    photo_dir = os.path.join(DATA_DIR, "photos", city)
    labels_path = os.path.join(photo_dir, "_labels_smoothness.json")
    if not os.path.exists(labels_path):
        sys.exit(f"{labels_path} がありません。先に tools/ml/label_smoothness.py --city {city} でラベル付けしてください。")
    with open(labels_path, encoding="utf-8") as f:
        labels = json.load(f)
    samples = []
    for edge_id, grade in labels.items():
        path = os.path.join(photo_dir, f"{edge_id}.jpg")
        if os.path.exists(path) and grade in CLASSES:
            samples.append((path, CLASSES.index(grade)))
    return samples


def _selftest():
    import tempfile

    import numpy as np
    import cv2

    with tempfile.TemporaryDirectory() as d:
        samples = []
        for i in range(20):
            path = os.path.join(d, f"e{i}.jpg")
            img = (np.random.rand(64, 64, 3) * 255).astype("uint8")
            cv2.imwrite(path, img)
            samples.append((path, i % len(CLASSES)))
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"selftest device={device}, samples={len(samples)}")
        train(samples, epochs=1, batch_size=4, val_split=0.2, device=device)
        print("selftest: OK（学習ループがエラー無く1エポック回った）")


def main():
    ap = argparse.ArgumentParser(description="路面の滑らかさ分類モデルの学習")
    ap.add_argument("--city", default="金沢市")
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--val-split", type=float, default=0.15)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return

    device = "cuda" if torch.cuda.is_available() else "cpu"
    samples = load_city_samples(args.city)
    if len(samples) < 10:
        sys.exit(f"ラベル済みサンプルが{len(samples)}件しかありません。学習には最低でも数十〜数百件を推奨します。")
    print(f"device={device}, samples={len(samples)}")

    state = train(samples, args.epochs, args.batch_size, args.val_split, device)
    os.makedirs(MODEL_DIR, exist_ok=True)
    out_path = os.path.join(MODEL_DIR, "smoothness_model.pt")
    torch.save({"state_dict": state, "classes": CLASSES}, out_path)
    print(f"保存: {out_path}")


if __name__ == "__main__":
    main()
