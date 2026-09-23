#!/usr/bin/env python3
"""
「路肩の広さ」「路面の滑らかさ」を、あなた自身がStreet Viewを見て判定するための
手動ラベリングツール。自動でStreet View画像を取得・保存することは一切しない
— 表示するのはブラウザで開くための普通のGoogle MapsのURL(リンク)だけで、
判定結果として保存するのはあなた自身が入力したS〜Dの文字だけ。

(Googleの規約が禁止しているのは「コンテンツの自動取得・保存・インデックス化」で、
人が見て自分で判断した結果をメモすることは制限されていない)

使い方:
  python tools/label_via_streetview.py --city 金沢市 --sample 30     # ランダム30区間
  python tools/label_via_streetview.py --city 金沢市 --limit 30      # 先頭から30区間
  python tools/label_via_streetview.py --city 金沢市 --edge-id e123  # 1区間だけ
  python tools/label_via_streetview.py --city 金沢市 --merge         # ラベルをグラフJSONに反映

操作: 各区間でリンクをブラウザで開いて確認し、S/A/B/C/D を入力（Enterでスキップ、
qで中断）。それまでの入力は逐次保存される。

出力: data/manual_labels/<city>.json  # {edgeId: {shoulderWidth, smoothness, note}}
"""
import argparse
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.normpath(os.path.join(HERE, "..", "data"))
GRAPH_DIR = os.path.join(DATA_DIR, "graph")
LABELS_DIR = os.path.join(DATA_DIR, "manual_labels")

VALID_GRADES = {"S", "A", "B", "C", "D"}
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


def load_graph(city):
    path = os.path.join(GRAPH_DIR, f"{city}.json")
    if not os.path.exists(path):
        sys.exit(f"{path} がありません。先に tools/build_graph.py --city {city} を実行してください。")
    with open(path, encoding="utf-8") as f:
        return path, json.load(f)


def load_labels(city):
    path = os.path.join(LABELS_DIR, f"{city}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return path, json.load(f)
    return path, {}


def streetview_url(lat, lon):
    # Street View(写真)レイヤーを指定座標で開く普通のGoogle Maps URL。API呼び出しではない。
    return f"https://www.google.com/maps?q=&layer=c&cbll={lat:.6f},{lon:.6f}"


def midpoint(points):
    p = points[len(points) // 2]
    return p[0], p[1]


def ask_grade(prompt):
    while True:
        v = input(prompt).strip().upper()
        if v in ("", "Q"):
            return v or None
        if v in VALID_GRADES:
            return v
        print("  S/A/B/C/D のいずれか、Enterでスキップ、qで中断してください")


def do_label(args):
    _, graph = load_graph(args.city)
    labels_path, labels = load_labels(args.city)
    os.makedirs(LABELS_DIR, exist_ok=True)

    edges = graph["edges"]
    if args.edge_id:
        edges = [e for e in edges if e["id"] == args.edge_id]
        if not edges:
            sys.exit(f"edge {args.edge_id} が見つかりません")
    else:
        edges = [e for e in edges if e["id"] not in labels]
        if args.sample:
            edges = random.sample(edges, min(args.sample, len(edges)))
        elif args.limit:
            edges = edges[: args.limit]

    if not edges:
        print("対象の区間がありません（サンプル/件数指定を見直すか、既に全てラベル済みです）")
        return

    print(f"{len(edges)}区間。各区間でリンクを開いて確認 → S/A/B/C/D入力（Enter=スキップ, q=中断）")
    for i, e in enumerate(edges):
        lat, lon = midpoint(e["points"])
        print(f"\n[{i+1}/{len(edges)}] edge={e['id']}  道路種別={e['tags'].get('highway','?')}"
              f"  現状の総合ランク={e.get('total')}")
        print(f"  Street View: {streetview_url(lat, lon)}")

        w = ask_grade("  路肩の広さ S/A/B/C/D> ")
        if w == "Q":
            break
        s = ask_grade("  路面の滑らかさ S/A/B/C/D> ")
        if s == "Q":
            break
        if w is None and s is None:
            continue

        entry = labels.get(e["id"], {})
        if w:
            entry["shoulderWidth"] = w
        if s:
            entry["smoothness"] = s
        labels[e["id"]] = entry
        with open(labels_path, "w", encoding="utf-8") as f:
            json.dump(labels, f, ensure_ascii=False, indent=0)

    print(f"\n保存: {labels_path} ({len(labels)}区間ラベル済み)")


def do_merge(args):
    graph_path, graph = load_graph(args.city)
    labels_path, labels = load_labels(args.city)
    if not labels:
        sys.exit(f"{labels_path} が空です。先に手動ラベリングを行ってください。")

    n = 0
    by_id = {e["id"]: e for e in graph["edges"]}
    for edge_id, entry in labels.items():
        e = by_id.get(edge_id)
        if not e:
            continue
        if "shoulderWidth" in entry:
            e["grades"]["shoulderWidth"] = entry["shoulderWidth"]
        if "smoothness" in entry:
            e["grades"]["smoothness"] = entry["smoothness"]
        e["total"] = composite(e["grades"].values())
        n += 1

    tmp_path = graph_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, graph_path)  # 書き込み中の中断でファイルが壊れないよう一時ファイル経由にする
    print(f"反映: {n}区間 → {graph_path}")


def main():
    ap = argparse.ArgumentParser(description="Street Viewを見た手動判定でラベル付け/反映する")
    ap.add_argument("--city", default="金沢市")
    ap.add_argument("--sample", type=int, help="未ラベルからランダムN区間")
    ap.add_argument("--limit", type=int, help="未ラベルから先頭N区間")
    ap.add_argument("--edge-id", help="特定の1区間だけラベリングする")
    ap.add_argument("--merge", action="store_true", help="ラベル済みデータをグラフJSONに反映する")
    args = ap.parse_args()

    if args.merge:
        do_merge(args)
    else:
        do_label(args)


if __name__ == "__main__":
    main()
