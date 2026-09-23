#!/usr/bin/env python3
"""
Phase3: build_graph.py が作った道路グラフの各edge(≒500mごとの区間)に対し、
Mapillary の公開画像から最も近い1枚を取得してローカルに保存する。

これは「学習データの収集」だけを行うツールで、学習(モデル作成)はしない
(Phase4は別途、ラベリング・GPU学習環境が必要)。

事前準備:
  1. https://www.mapillary.com/dashboard/developers でアプリを登録し、
     Client Token (アクセストークン) を無料発行する
  2. 環境変数 MAPILLARY_ACCESS_TOKEN にセットする
     (PowerShell例: $env:MAPILLARY_ACCESS_TOKEN = "MLY|..."）

使い方:
  python tools/fetch_mapillary_images.py --city 金沢市
  python tools/fetch_mapillary_images.py --city 金沢市 --limit 50   # 動作確認用に少数だけ
  python tools/fetch_mapillary_images.py --city 金沢市 --resume     # 続きから

出力:
  data/photos/<city>/<edgeId>.jpg        # 最寄り画像のサムネイル
  data/photos/<city>/_meta.json          # edgeId -> {imageId, capturedAt, compassAngle, distanceM}

標準ライブラリのみ / Python 3.9+
"""
import argparse
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.normpath(os.path.join(HERE, "..", "data"))
GRAPH_DIR = os.path.join(DATA_DIR, "graph")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")

EARTH_R = 6371008.8
BBOX_PAD_DEG = 0.0015  # 検索範囲(片側) ≒ 165m


def _save_meta_atomic(meta, path):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=0)
    os.replace(tmp_path, path)


def _rad(d):
    return d * math.pi / 180.0


def distance(a, b):
    la1, la2 = _rad(a[0]), _rad(b[0])
    dla = _rad(b[0] - a[0])
    dlo = _rad(b[1] - a[1])
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(h)))


def midpoint(points):
    mid = points[len(points) // 2]
    return mid[0], mid[1]


def api_get(url, params, max_tries=5):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": "bike-nav-photofetch/1.0"})
    backoff = 0.0
    for attempt in range(1, max_tries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as ex:
            if ex.code == 401:
                sys.exit("Mapillaryのアクセストークンが無効です。MAPILLARY_ACCESS_TOKEN を確認してください。")
            if attempt == max_tries:
                raise
            backoff = min(backoff * 2 if backoff else 5.0, 60.0)
            print(f"    APIエラー({attempt}/{max_tries}): {ex} — {backoff:.0f}s 待機", file=sys.stderr)
            time.sleep(backoff)
        except KeyboardInterrupt:
            raise
        except Exception as ex:  # noqa: BLE001
            if attempt == max_tries:
                raise
            backoff = min(backoff * 2 if backoff else 5.0, 60.0)
            print(f"    通信エラー({attempt}/{max_tries}): {ex} — {backoff:.0f}s 待機", file=sys.stderr)
            time.sleep(backoff)


def nearest_image(lat, lon, token):
    bbox = (lon - BBOX_PAD_DEG, lat - BBOX_PAD_DEG, lon + BBOX_PAD_DEG, lat + BBOX_PAD_DEG)
    res = api_get(
        "https://graph.mapillary.com/images",
        {
            "access_token": token,
            "fields": "id,geometry,compass_angle,captured_at,thumb_1024_url",
            "bbox": ",".join(str(round(v, 6)) for v in bbox),
            "limit": 10,
        },
    )
    best = None
    best_d = None
    for item in res.get("data", []):
        coords = item.get("geometry", {}).get("coordinates")
        if not coords:
            continue
        ilon, ilat = coords[0], coords[1]
        d = distance((lat, lon), (ilat, ilon))
        if best_d is None or d < best_d:
            best_d, best = d, item
    return best, best_d


def main():
    ap = argparse.ArgumentParser(description="Mapillaryから道路edgeごとに最寄り画像を収集")
    ap.add_argument("--city", default="金沢市")
    ap.add_argument("--limit", type=int, default=None, help="動作確認用に先頭N edgeだけ処理")
    ap.add_argument("--sample", type=int, default=None, help="市内全域からランダムN edgeを処理(網羅率調査向け)")
    ap.add_argument("--seed", type=int, default=0, help="--sampleの乱数シード")
    ap.add_argument("--resume", action="store_true", help="既に取得済みのedgeはスキップ")
    ap.add_argument("--gap-only", action="store_true",
                     help="grades.shoulderWidthがまだ無いedgeだけを対象にする"
                          "(航空写真(apply_aerial_width.py)で埋まらなかった区間をMapillaryで補う用途)")
    ap.add_argument("--delay", type=float, default=0.5, help="1リクエストごとの待機秒(既定0.5)")
    args = ap.parse_args()

    token = os.environ.get("MAPILLARY_ACCESS_TOKEN")
    if not token:
        token_file = os.path.join(HERE, ".mapillary_token")
        if os.path.exists(token_file):
            with open(token_file, encoding="utf-8") as f:
                token = f.read().strip()
    if not token:
        sys.exit(
            "環境変数 MAPILLARY_ACCESS_TOKEN が未設定です。\n"
            "https://www.mapillary.com/dashboard/developers でアプリを登録しトークンを発行してから、\n"
            '  PowerShell: $env:MAPILLARY_ACCESS_TOKEN = "MLY|..."\n'
            "または tools/.mapillary_token というファイルにトークンだけを書いて保存してください。\n"
            "を実行してください。"
        )

    graph_path = os.path.join(GRAPH_DIR, f"{args.city}.json")
    if not os.path.exists(graph_path):
        sys.exit(f"{graph_path} がありません。先に tools/build_graph.py --city {args.city} を実行してください。")
    with open(graph_path, encoding="utf-8") as f:
        graph = json.load(f)

    out_dir = os.path.join(PHOTOS_DIR, args.city)
    os.makedirs(out_dir, exist_ok=True)
    meta_path = os.path.join(out_dir, "_meta.json")
    meta = {}
    if args.resume and os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)

    edges = graph["edges"]
    if args.gap_only:
        edges = [e for e in edges if not e["grades"].get("shoulderWidth")]
        print(f"未計測(gap)のedge: {len(edges)}/{len(graph['edges'])}")
    if args.sample:
        random.seed(args.seed)
        edges = random.sample(edges, min(args.sample, len(edges)))
    elif args.limit:
        edges = edges[: args.limit]

    done, skipped, missed = 0, 0, 0
    distances = []
    for i, e in enumerate(edges):
        eid = e["id"]
        jpg_path = os.path.join(out_dir, f"{eid}.jpg")
        if args.resume and eid in meta and os.path.exists(jpg_path):
            skipped += 1
            continue

        lat, lon = midpoint(e["points"])
        try:
            best, best_d = nearest_image(lat, lon, token)
        except Exception as ex:  # noqa: BLE001
            print(f"  [{i+1}/{len(edges)}] {eid}: 取得失敗 {ex}", file=sys.stderr)
            missed += 1
            continue

        if not best:
            missed += 1
            meta[eid] = None
            continue

        thumb_url = best.get("thumb_1024_url")
        if thumb_url:
            try:
                req = urllib.request.Request(thumb_url, headers={"User-Agent": "bike-nav-photofetch/1.0"})
                with urllib.request.urlopen(req, timeout=30) as r, open(jpg_path, "wb") as out:
                    out.write(r.read())
            except Exception as ex:  # noqa: BLE001
                print(f"  [{i+1}/{len(edges)}] {eid}: 画像ダウンロード失敗 {ex}", file=sys.stderr)
                missed += 1
                continue

        meta[eid] = {
            "imageId": best.get("id"),
            "capturedAt": best.get("captured_at"),
            "compassAngle": best.get("compass_angle"),
            "distanceM": round(best_d, 1) if best_d is not None else None,
        }
        if best_d is not None:
            distances.append(best_d)
        done += 1
        if done % 20 == 0:
            _save_meta_atomic(meta, meta_path)
            print(f"  [{i+1}/{len(edges)}] {done}件取得済み …")
        time.sleep(args.delay)

    _save_meta_atomic(meta, meta_path)
    print(f"完了: 取得 {done} / スキップ {skipped} / 見つからず {missed} (計 {len(edges)} edges)")
    print(f"保存先: {out_dir}")
    if distances:
        distances.sort()
        n = len(distances)
        within50 = sum(1 for d in distances if d <= 50)
        within100 = sum(1 for d in distances if d <= 100)
        print(f"網羅率の目安(取得できた{n}件中): 中央値={distances[n//2]:.0f}m  "
              f"50m以内={within50}件({within50/n:.0%})  100m以内={within100}件({within100/n:.0%})")


if __name__ == "__main__":
    main()
