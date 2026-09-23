#!/usr/bin/env python3
"""
tools/fetch_traffic_census.py が取得した実測交通量(道路交通センサス)を、
道路グラフの各edgeの grades.traffic (これまでOSM由来の粗いヒューリスティック)
に上書きする。実測値がある区間だけ上書きし、無い区間(住宅街の細い道など、
そもそも調査対象外)はヒューリスティックのまま残す。

使い方:
  python tools/fetch_traffic_census.py --city 金沢市   # 先にこれで取得
  python tools/apply_traffic_census.py --city 金沢市
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict

from shapely.geometry import LineString, Point

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.normpath(os.path.join(HERE, "..", "data"))
GRAPH_DIR = os.path.join(DATA_DIR, "graph")
CENSUS_DIR = os.path.join(DATA_DIR, "census")

MAX_MATCH_M = 40   # これより遠い区間は「対応する実測道路が無い」とみなす
GRID_CELL_M = 200  # 近傍探索用の粗いグリッドのマス目

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


def grade_from_volume(trf24):
    """24時間交通量(台/日)を自転車の走りやすさ観点でS〜Dに変換。"""
    if trf24 <= 1000:
        return "S"
    if trf24 <= 3000:
        return "A"
    if trf24 <= 8000:
        return "B"
    if trf24 <= 20000:
        return "C"
    return "D"


def build_projector(ref_lat):
    m_per_lat = 111320.0
    m_per_lon = 111320.0 * math.cos(math.radians(ref_lat))

    def to_xy(lon, lat):
        return (lon * m_per_lon, lat * m_per_lat)

    return to_xy


def midpoint(points):
    lat, lon = points[len(points) // 2]
    return lat, lon


def main():
    ap = argparse.ArgumentParser(description="実測交通量(道路交通センサス)をグラフに反映")
    ap.add_argument("--city", default="金沢市")
    args = ap.parse_args()

    graph_path = os.path.join(GRAPH_DIR, f"{args.city}.json")
    census_path = os.path.join(CENSUS_DIR, f"{args.city}.geojson")
    if not os.path.exists(graph_path):
        sys.exit(f"{graph_path} がありません。先に tools/build_graph.py を実行してください。")
    if not os.path.exists(census_path):
        sys.exit(f"{census_path} がありません。先に tools/fetch_traffic_census.py --city {args.city} を実行してください。")

    with open(graph_path, encoding="utf-8") as f:
        graph = json.load(f)
    with open(census_path, encoding="utf-8") as f:
        census = json.load(f)

    all_lats = [lat for lat, lon in graph["nodes"].values()]
    to_xy = build_projector(sum(all_lats) / len(all_lats))

    census_lines = []
    for feat in census["features"]:
        trf24 = feat["properties"].get("24H_trf")
        if trf24 is None:
            continue
        geom = feat["geometry"]
        # LineString: [ [lon,lat], ... ] / MultiLineString: [ [[lon,lat],...], [[lon,lat],...] ]
        parts = geom["coordinates"] if geom["type"] == "MultiLineString" else [geom["coordinates"]]
        for coords in parts:
            if len(coords) < 2:
                continue
            xy = [to_xy(lon, lat) for lon, lat in coords]
            census_lines.append({"line": LineString(xy), "trf24": trf24})

    grid = defaultdict(list)
    for i, cl in enumerate(census_lines):
        minx, miny, maxx, maxy = cl["line"].bounds
        for gx in range(int(minx // GRID_CELL_M), int(maxx // GRID_CELL_M) + 1):
            for gy in range(int(miny // GRID_CELL_M), int(maxy // GRID_CELL_M) + 1):
                grid[(gx, gy)].append(i)

    matched = 0
    for e in graph["edges"]:
        lat, lon = midpoint(e["points"])
        x, y = to_xy(lon, lat)
        gx, gy = int(x // GRID_CELL_M), int(y // GRID_CELL_M)
        candidates = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                candidates.update(grid.get((gx + dx, gy + dy), []))
        if not candidates:
            continue

        pt = Point(x, y)
        best_d, best_trf = None, None
        for i in candidates:
            d = census_lines[i]["line"].distance(pt)
            if best_d is None or d < best_d:
                best_d, best_trf = d, census_lines[i]["trf24"]

        if best_d is not None and best_d <= MAX_MATCH_M:
            e["grades"]["traffic"] = grade_from_volume(best_trf)
            e["tags"]["trf24"] = best_trf  # 参考値として保持(UIのツールチップ等で使える)
            e["total"] = composite(e["grades"].values())
            matched += 1

    tmp_path = graph_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, graph_path)  # 書き込み中の中断でファイルが壊れないよう一時ファイル経由にする
    print(f"完了: {matched}/{len(graph['edges'])} edge に実測交通量を反映 → {graph_path}")


if __name__ == "__main__":
    main()
