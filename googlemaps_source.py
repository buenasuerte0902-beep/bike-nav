"""Google マップの Street View をブラウザで開き、ルート沿いに自動でスクショする取り込み元。

注意: Google マップ利用規約 3.3（コンテンツの大量ダウンロード）と 3.4（地図・ナビ用の
データセット作成）に当たる使い方であり、アカウントや IP の利用制限を受けるおそれがある。
利用者の判断で config.json の source を "googlemaps" にした時だけ動く。
"""
import math
import time
import urllib.parse

EARTH_R = 6371008.8
PANO_URL = "https://www.google.com/maps/@?"


def _distance(a, b):
    la1, la2 = math.radians(a[0]), math.radians(b[0])
    dla = la2 - la1
    dlo = math.radians(b[1] - a[1])
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(h)))


def _bearing(a, b):
    la1, la2 = math.radians(a[0]), math.radians(b[0])
    dlo = math.radians(b[1] - a[1])
    y = math.sin(dlo) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlo)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def sample_route(route, step_m):
    """折れ線 route（[[緯度, 経度], ...]）を step_m ごとに区切り、(緯度, 経度, 進行方向) を返す。"""
    points = []
    carry = 0.0  # 前の区間の残り距離
    for a, b in zip(route, route[1:]):
        seg = _distance(a, b)
        if seg == 0:
            continue
        heading = _bearing(a, b)
        d = carry
        while d < seg:
            t = d / seg
            points.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, heading))
            d += step_m
        carry = d - seg
    if len(route) >= 2:
        points.append((route[-1][0], route[-1][1], _bearing(route[-2], route[-1])))
    return points


def pano_url(lat, lon, heading, pitch, fov):
    return PANO_URL + urllib.parse.urlencode({
        "api": 1,
        "map_action": "pano",
        "viewpoint": f"{lat:.6f},{lon:.6f}",
        "heading": round(heading),
        "pitch": pitch,
        "fov": fov,
    })


def _open_browser(cfg):
    from selenium import webdriver

    width, height = cfg["window_size"]
    if cfg["browser"] == "chrome":
        options = webdriver.ChromeOptions()
        options.add_argument(f"--window-size={width},{height}")
        options.add_argument("--lang=ja")
        driver = webdriver.Chrome(options=options)
    else:
        options = webdriver.EdgeOptions()
        options.add_argument(f"--window-size={width},{height}")
        options.add_argument("--lang=ja")
        driver = webdriver.Edge(options=options)
    return driver


def run(cfg, publish_image):
    route = cfg["route"]
    if len(route) < 2:
        raise SystemExit("[Producer] config.json の googlemaps.route に2点以上の [緯度, 経度] を書いてください。")
    points = sample_route(route, cfg["step_m"])
    headings = cfg["headings"]  # 進行方向に対する向き（0 = 前方, 90 = 左右など）
    print("--- キャプチャプロデューサー起動（Googleマップ Street View） ---")
    print(f"ルート上の {len(points)} 地点 × {len(headings)} 方向を撮影します（{cfg['step_m']}m 間隔）。")

    driver = _open_browser(cfg)
    try:
        for i, (lat, lon, heading) in enumerate(points, 1):
            for offset in headings:
                view = (heading + offset) % 360
                driver.get(pano_url(lat, lon, view, cfg["pitch"], cfg["fov"]))
                # パノラマの描画を待つ
                time.sleep(cfg["load_wait_sec"])
                png = driver.get_screenshot_as_png()

                def write(path, data=png):
                    with open(path, "wb") as f:
                        f.write(data)

                stamp = time.strftime("%Y%m%d_%H%M%S")
                filename = f"gmap_{stamp}_{i:04d}_{round(view):03d}.png"
                publish_image(filename, write, {
                    "source": "googlemaps",
                    "source_id": f"{lat:.6f},{lon:.6f},{round(view)}",
                    "lat": lat,
                    "lon": lon,
                    "heading": view,
                    "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                print(f"[Producer] 撮影 {i}/{len(points)} 向き{round(view)}°: {filename}")
                time.sleep(cfg["interval_sec"])

        print("[Producer] ルートの撮影が終わりました。推論の完了を待っています。")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Producer] 撮影を終了します。")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
