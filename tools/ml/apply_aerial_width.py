#!/usr/bin/env python3
"""
tools/ml/aerial.py の航空写真ベースの舗装帯幅推定を全edgeに適用し、
グラフJSONの grades.shoulderWidth (これまでnull)を埋める。

Mapillary写真ベースの推定(tools/ml/apply_ml_scores.py)と違い、
  - Street Viewの規約問題が無い(国土地理院タイル、出典明示のみで商用利用可)
  - 全区間をカバーできる(Mapillaryの実測網羅率は約50%だった)
という利点がある一方、
  - 「道路+路肩+歩道」を合わせた全幅であり、路肩だけを切り出せているわけではない
  - 密集市街地で隣接する駐車場・広場に扫き込まれて過大評価することがある
    (道路種別ごとに走査上限を設けて緩和しているが完全ではない、aerial.py参照)
という限界がある。

47,000区間規模だと時間がかかる処理なので、--limit で少数だけ試してから
本番(全件)を実行するのを推奨。タイルはdata/aerial_tiles/にキャッシュされ、
隣接edgeで重複するタイルは再ダウンロードしない。

使い方:
  python tools/ml/apply_aerial_width.py --city 金沢市 --limit 50   # 動作確認
  python tools/ml/apply_aerial_width.py --city 金沢市              # 全区間(時間がかかる)
  python tools/ml/apply_aerial_width.py --city 金沢市 --resume     # 中断から再開
"""
import argparse
import json
import os
import sys
import time

from aerial import bearing, estimate_paved_width, grade_paved_width

HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "data", "graph"))

GRADE_SCORE = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}


def save_graph_atomic(graph, path):
    """中断(Ctrl+C, kill)がJSONファイルの途中で起きても壊れないよう、一時ファイルに
    書いてから os.replace で丸ごと置き換える(置き換え自体は割り込まれない)。"""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, path)


def composite(grades):
    vals = [GRADE_SCORE[g] for g in grades if g]
    if not vals:
        return None
    avg = sum(vals) / len(vals)
    for g, s in sorted(GRADE_SCORE.items(), key=lambda kv: -kv[1]):
        if avg >= s - 0.5:
            return g
    return "D"


def midpoint_and_bearing(points):
    mid = points[len(points) // 2]
    a, b = points[0], points[-1]
    return mid[0], mid[1], bearing(a[0], a[1], b[0], b[1])


def main():
    ap = argparse.ArgumentParser(description="航空写真から舗装帯幅を推定しグラフに反映")
    ap.add_argument("--city", default="金沢市")
    ap.add_argument("--limit", type=int, default=None, help="動作確認用に先頭N edgeだけ処理")
    ap.add_argument("--resume", action="store_true", help="既にshoulderWidthが入っているedgeはスキップ")
    ap.add_argument("--save-every", type=int, default=200, help="このedge数ごとに中間保存する")
    args = ap.parse_args()

    graph_path = os.path.join(GRAPH_DIR, f"{args.city}.json")
    if not os.path.exists(graph_path):
        sys.exit(f"{graph_path} がありません。先に tools/build_graph.py --city {args.city} を実行してください。")
    with open(graph_path, encoding="utf-8") as f:
        graph = json.load(f)

    edges = graph["edges"]
    targets = [e for e in edges if not (args.resume and e["grades"].get("shoulderWidth"))]
    if args.limit:
        targets = targets[: args.limit]

    print(f"対象: {len(targets)}/{len(edges)} edges")
    t0 = time.time()
    done, failed = 0, 0
    for i, e in enumerate(targets):
        lat, lon, brg = midpoint_and_bearing(e["points"])
        try:
            width_m = estimate_paved_width(lat, lon, brg, highway=e["tags"].get("highway"))
        except Exception as ex:  # noqa: BLE001
            print(f"  {e['id']}: 推定に失敗 {ex}", file=sys.stderr)
            failed += 1
            continue

        if width_m is not None:
            e["grades"]["shoulderWidth"] = grade_paved_width(width_m)
            e["tags"]["pavedWidthM"] = width_m
            e["total"] = composite(e["grades"].values())
            done += 1

        if (i + 1) % args.save_every == 0:
            save_graph_atomic(graph, graph_path)
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            eta_min = (len(targets) - i - 1) / rate / 60 if rate > 0 else float("inf")
            print(f"  [{i+1}/{len(targets)}] 成功{done} 失敗{failed} "
                  f"({rate:.1f}件/秒, 残り約{eta_min:.0f}分) … 中間保存")

    save_graph_atomic(graph, graph_path)
    print(f"完了: 成功 {done} / 失敗 {failed} (計 {len(targets)}) → {graph_path}")


if __name__ == "__main__":
    main()
