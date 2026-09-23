#!/usr/bin/env python3
"""
国土交通省「令和3年度 一般交通量調査（道路交通センサス）」の公開Web地図が使っている
静的GeoJSONタイル(認証不要, 商用可能な政府オープンデータ)から、指定エリアの
実測交通量を取得する。写真ベースの交通量ヒューリスティックより遥かに正確。

データ源: https://www.mlit.go.jp/road/ir/ir-data/census_visualizationR3/
タイル形式: {drmXX}/{z}/{x}/{y}.geojson （標準スリッピータイル座標, z=13固定）
drmXX = 道路種別区分（10/20=国道, 31/32=都道府県道, 40_50=市町村道, 60_70=その他）
属性: census(区間ID) / 24H_trf(24時間交通量,台/日) / 12H_trf / konz(混雑度) / speed_ME,speed_DT(旅行速度)

使い方:
  python tools/fetch_traffic_census.py --city 金沢市   # data/graph/<city>.json のbboxを自動で使う
  python tools/fetch_traffic_census.py --bbox 136.55,36.37,136.82,36.68 --out 石川県

出力: data/census/<city>.geojson （区間ごとのLineString + 交通量、build_graph.py等が使う）

標準ライブラリのみ / Python 3.9+
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.normpath(os.path.join(HERE, "..", "data"))
GRAPH_DIR = os.path.join(DATA_DIR, "graph")
CENSUS_DIR = os.path.join(DATA_DIR, "census")

BASE_URL = "https://www.mlit.go.jp/road/ir/ir-data/census_visualizationR3"
ZOOM = 13
DRM_CATEGORIES = ["drm10", "drm20", "drm31", "drm32", "drm40_50", "drm60_70"]


def lonlat_to_tile(lon, lat, z=ZOOM):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def fetch_tile(drm, x, y, z=ZOOM):
    url = f"{BASE_URL}/{drm}/{z}/{x}/{y}.geojson"
    req = urllib.request.Request(url, headers={"User-Agent": "bike-nav-census-fetch/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            return None  # そのタイル・区分に該当道路が無いだけ
        raise


def fetch_area(west, south, east, north, delay=0.05):
    x0, y1 = lonlat_to_tile(west, south)
    x1, y0 = lonlat_to_tile(east, north)
    xs = range(min(x0, x1), max(x0, x1) + 1)
    ys = range(min(y0, y1), max(y0, y1) + 1)
    total_tiles = len(list(xs)) * len(list(ys)) * len(DRM_CATEGORIES)
    print(f"タイル数: {total_tiles} (x:{min(x0,x1)}-{max(x0,x1)} y:{min(y0,y1)}-{max(y0,y1)} × {len(DRM_CATEGORIES)}区分)")

    features_by_id = {}
    done = 0
    for drm in DRM_CATEGORIES:
        for x in xs:
            for y in ys:
                done += 1
                try:
                    data = fetch_tile(drm, x, y)
                except Exception as ex:  # noqa: BLE001
                    print(f"  {drm}/{x}/{y}: 取得失敗 {ex}", file=sys.stderr)
                    continue
                if data:
                    for feat in data.get("features", []):
                        cid = feat.get("properties", {}).get("census")
                        key = cid if cid is not None else id(feat)
                        features_by_id[key] = feat
                if done % 40 == 0:
                    print(f"  {done}/{total_tiles} タイル処理済み … (区間 {len(features_by_id)}件)")
                time.sleep(delay)
    return list(features_by_id.values())


def graph_bbox(city):
    path = os.path.join(GRAPH_DIR, f"{city}.json")
    if not os.path.exists(path):
        sys.exit(f"{path} がありません。--bbox を直接指定するか、先に tools/build_graph.py を実行してください。")
    with open(path, encoding="utf-8") as f:
        graph = json.load(f)
    lats = [lat for lat, lon in graph["nodes"].values()]
    lons = [lon for lat, lon in graph["nodes"].values()]
    return min(lons), min(lats), max(lons), max(lats)


def main():
    ap = argparse.ArgumentParser(description="道路交通センサスの実測交通量を取得")
    ap.add_argument("--city", help="data/graph/<city>.json のbboxを使う")
    ap.add_argument("--bbox", help="west,south,east,north を直接指定（--cityの代わり）")
    ap.add_argument("--out", help="出力ファイル名(拡張子無し)。既定は--cityと同じ")
    args = ap.parse_args()

    if args.bbox:
        west, south, east, north = (float(v) for v in args.bbox.split(","))
        out_name = args.out or "custom"
    elif args.city:
        west, south, east, north = graph_bbox(args.city)
        out_name = args.out or args.city
    else:
        sys.exit("--city か --bbox のどちらかを指定してください")

    features = fetch_area(west, south, east, north)
    os.makedirs(CENSUS_DIR, exist_ok=True)
    out_path = os.path.join(CENSUS_DIR, f"{out_name}.geojson")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"完了: 区間 {len(features)}件 → {out_path}")


if __name__ == "__main__":
    main()
