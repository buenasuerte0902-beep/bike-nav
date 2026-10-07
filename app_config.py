import json
import os

# exe（または main_runner.py）と同じフォルダの config.json を読む。
# 無ければ既定値で作るので、利用者はそれを書き換えて再起動すればよい。
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    # 画像の取り込み元: "screen"（画面キャプチャ）、"mapillary"（Mapillary 公開画像）、
    # "googlemaps"（Googleマップの Street View をブラウザで自動撮影。利用規約 3.3/3.4 に当たるので自己責任）
    "source": "screen",
    "screen": {
        # true なら interval_sec ごとに自動で撮る。false ならホットキーを押した時だけ撮る
        "auto": True,
        "interval_sec": 3,
        "hotkey": "ctrl+space",
    },
    "mapillary": {
        # https://www.mapillary.com/dashboard/developers で発行する Client Token。
        # 空なら環境変数 MAPILLARY_ACCESS_TOKEN を使う
        "access_token": "",
        # 取り込む範囲 [西経度, 南緯度, 東経度, 北緯度]（既定は金沢駅周辺）
        "bbox": [136.640, 36.570, 136.660, 36.585],
        "max_images": 200,
        "delay_sec": 1.0,
    },
    "googlemaps": {
        # 撮影するルート [[緯度, 経度], ...]。step_m ごとに区切って Street View を開く（既定は金沢駅→香林坊）
        "route": [[36.5781, 136.6480], [36.5700, 136.6530], [36.5615, 136.6560]],
        "step_m": 50,
        # 進行方向に対する撮影の向き（度）。0 = 前方
        "headings": [0],
        "pitch": -10,
        "fov": 90,
        # "edge" または "chrome"（PC にインストール済みのものを使う）
        "browser": "edge",
        "window_size": [1280, 800],
        "load_wait_sec": 4,
        "interval_sec": 3,
    },
}


def _merge(defaults, loaded):
    merged = dict(defaults)
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(defaults.get(key), dict):
            merged[key] = _merge(defaults[key], value)
        else:
            merged[key] = value
    return merged


def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with open(CONFIG_FILE, encoding="utf-8-sig") as f:
        return _merge(DEFAULT_CONFIG, json.load(f))
