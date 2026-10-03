"""Mapillary の公開画像を範囲指定で取り込む Producer の取り込み元。

Mapillary の利用規約は学習・データセット開発目的の利用を認めている
（README の「Mapillary」の節を参照）。Google マップの画像はここでは扱わない。
"""
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API_URL = "https://graph.mapillary.com/images"
USER_AGENT = "CycleVision/1.0"
# Mapillary は広すぎる bbox を拒否するので、この大きさ（度）のタイルに分けて検索する
TILE_DEG = 0.01
SEEN_FILE = os.path.join("output", "mapillary_seen.txt")


def _get(url, params=None, max_tries=4):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, max_tries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as ex:
            if ex.code in (401, 403):
                raise SystemExit("[Producer] Mapillary のアクセストークンが無効です。config.json を確認してください。")
            if attempt == max_tries:
                raise
        except urllib.error.URLError:
            if attempt == max_tries:
                raise
        wait = 5 * 2 ** (attempt - 1)
        print(f"[Producer] 通信エラー、{wait}秒後に再試行します ({attempt}/{max_tries})")
        time.sleep(wait)


def tiles(bbox):
    west, south, east, north = bbox
    cols = max(1, math.ceil(round((east - west) / TILE_DEG, 6)))
    rows = max(1, math.ceil(round((north - south) / TILE_DEG, 6)))
    for r in range(rows):
        for c in range(cols):
            yield (west + c * TILE_DEG, south + r * TILE_DEG,
                   min(west + (c + 1) * TILE_DEG, east), min(south + (r + 1) * TILE_DEG, north))


def _load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def _token(cfg):
    token = cfg.get("access_token") or os.environ.get("MAPILLARY_ACCESS_TOKEN", "")
    if not token:
        raise SystemExit(
            "[Producer] Mapillary のアクセストークンがありません。\n"
            "https://www.mapillary.com/dashboard/developers で発行し、"
            "config.json の mapillary.access_token に書いてください。"
        )
    return token


def run(cfg, publish_image):
    token = _token(cfg)
    max_images = cfg["max_images"]
    delay = cfg["delay_sec"]
    os.makedirs("output", exist_ok=True)
    seen = _load_seen()
    print("--- キャプチャプロデューサー起動（Mapillary） ---")
    print(f"範囲 {cfg['bbox']} から最大 {max_images} 枚を取り込みます（取り込み済み {len(seen)} 枚は除外）。")

    fetched = 0
    try:
        for tile in tiles(cfg["bbox"]):
            if fetched >= max_images:
                break
            body = _get(API_URL, {
                "access_token": token,
                "fields": "id,geometry,compass_angle,captured_at,thumb_1024_url",
                "bbox": ",".join(f"{v:.6f}" for v in tile),
                "limit": 100,
            })
            for item in json.loads(body).get("data", []):
                if fetched >= max_images:
                    break
                image_id = str(item["id"])
                url = item.get("thumb_1024_url")
                coords = (item.get("geometry") or {}).get("coordinates")
                if image_id in seen or not url or not coords:
                    continue
                image = _get(url)

                def write(path, data=image):
                    with open(path, "wb") as f:
                        f.write(data)

                publish_image(f"mly_{image_id}.jpg", write, {
                    "source": "mapillary",
                    "source_id": image_id,
                    "lat": coords[1],
                    "lon": coords[0],
                    "compass_angle": item.get("compass_angle"),
                    "captured_at": item.get("captured_at"),
                })
                with open(SEEN_FILE, "a", encoding="utf-8") as f:
                    f.write(image_id + "\n")
                seen.add(image_id)
                fetched += 1
                print(f"[Producer] 取り込み {fetched}/{max_images}: mly_{image_id}.jpg")
                time.sleep(delay)

        print(f"[Producer] 指定範囲の取り込みが終わりました（今回 {fetched} 枚）。推論の完了を待っています。")
        # Producer が終わると全体が止まるので、Consumer が残りを処理し終えるまで待機する
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Producer] 取り込みを終了します。")
