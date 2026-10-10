#!/usr/bin/env python3
"""全国の信号機(highway=traffic_signals)を OSM(Overpass) から取得し、
1度x1度のタイルJSONとして data/signals/ に書き出す。アプリのナビ中の信号表示が使う。

  python3 tools/fetch_signals.py            # 全国(未取得のタイルだけ)
  python3 tools/fetch_signals.py --force    # 取得済みも再取得

出力: data/signals/<lat>_<lon>.json = [[lat, lon, name|""], ...]  /  data/signals/index.json = {"tiles": [...]}
(タイルの左下が整数の緯度・経度。信号が1つも無いタイルは作らない)
"""
import argparse, json, os, sys, time, urllib.parse, urllib.request

ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "signals")
# 日本の範囲(沖縄・離島を含む)
LAT_RANGE = range(24, 46)
LON_RANGE = range(122, 154)


def query(s, w, n, e):
    q = f'[out:json][timeout:180];node["highway"="traffic_signals"]({s},{w},{n},{e});out body qt;'
    last = None
    for attempt in range(4):
        for url in ENDPOINTS:
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": q}).encode())
                with urllib.request.urlopen(req, timeout=240) as r:
                    return json.load(r)["elements"]
            except Exception as ex:  # 混雑・タイムアウトは待って再試行
                last = ex
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"取得失敗 {s},{w}: {last}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    tiles = []
    for la in LAT_RANGE:
        for lo in LON_RANGE:
            path = os.path.join(OUT, f"{la}_{lo}.json")
            if os.path.exists(path) and not args.force:
                tiles.append(f"{la}_{lo}")
                continue
            els = query(la, lo, la + 1, lo + 1)
            rows = []
            for el in els:
                if el.get("type") != "node":
                    continue
                t = el.get("tags", {})
                # 境界上のノードが隣タイルと重複しないよう、左下基準の半開区間で割り当てる
                if not (la <= el["lat"] < la + 1 and lo <= el["lon"] < lo + 1):
                    continue
                rows.append([round(el["lat"], 6), round(el["lon"], 6), t.get("name") or t.get("name:ja") or ""])
            if rows:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
                tiles.append(f"{la}_{lo}")
            print(f"{la}_{lo}: {len(rows)}", flush=True)
            time.sleep(2)  # サーバーへの配慮
    with open(os.path.join(OUT, "index.json"), "w") as f:
        json.dump({"tiles": sorted(tiles)}, f)
    print(f"完了: {len(tiles)} タイル")


if __name__ == "__main__":
    sys.exit(main())
