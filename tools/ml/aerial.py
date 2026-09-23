"""
国土地理院「シームレスフォト」(航空写真タイル, 出典明示のみで商用利用可)から
道路の中心線(OSM由来、既知)に垂直な方向へ走査し、舗装帯(路肩+歩道相当)の
実幅を測る。Street Viewの「車を物差しにする」方式と違い、
  - 道路の位置は既にOSMからわかっているので検出が要らない
  - 真上からの画像なので遠近法の歪みが無く、ズームレベルから直接 実寸/画素 が求まる
という理由で、より単純で信頼できる幾何になる。

タイル: https://maps.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg (最大ズーム18)
利用規約: 地理院タイル利用規約(出典明示のみで申請不要, 商用利用可)。
このアプリでの出典表示は README / アプリのクレジット表示側で対応すること。
"""
import math
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from imutil import imread_bytes

HERE = os.path.dirname(os.path.abspath(__file__))
TILE_CACHE_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "data", "aerial_tiles"))
TILE_URL = "https://maps.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"
TILE_SIZE = 256
ZOOM = 18  # シームレスフォトの最大ズーム(≒0.6m/px、緯度により変動)

COLOR_DIST_THRESHOLD = 26.0
PATCH = 2            # (2*PATCH+1)^2 の平均をサンプル値にする(JPEG圧縮ノイズ対策)
EMA_ALPHA = 0.3       # 基準色を少しずつ更新する(日照ムラ等の緩やかな変化を許容しつつ急な変化は検出)
STEP_PX = 1
MAX_SCAN_M = 8.0  # 中心線からこれ以上離れたら「別の敷地(駐車場等)」とみなし打ち切り


def _deg2num(lat, lon, z=ZOOM):
    lat_rad = math.radians(lat)
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y  # タイル単位の小数座標(整数部=タイル番号, 小数部=タイル内位置)


def meters_per_pixel(lat, z=ZOOM):
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)


def bearing(lat1, lon1, lat2, lon2):
    la1, la2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _fetch_tile(z, x, y, delay=0.05):
    """キャッシュにあればディスクから即返す(待機なし)。無ければネットワーク取得し、
    行儀よくするための待機はネットワークに実際に問い合わせた時だけ行う。"""
    cache_path = os.path.join(TILE_CACHE_DIR, str(z), str(x), f"{y}.jpg")
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            return f.read()
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    url = TILE_URL.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": "bike-nav-aerial/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            data = None
        else:
            raise
    finally:
        time.sleep(delay)
    if data:
        with open(cache_path, "wb") as f:
            f.write(data)
    return data


# タイル取得の同時実行数。同一IPからの普通のブラウザがパン操作で複数タイルを同時に
# 読み込むのと同程度の並列度(別IPを使った回避などはしない)。
FETCH_WORKERS = 8
_executor = ThreadPoolExecutor(max_workers=FETCH_WORKERS)


class Mosaic:
    """指定座標を中心に半径radius_m以上をカバーするタイル群を1枚のnumpy画像にまとめたもの。"""

    def __init__(self, lat, lon, radius_m=12.0, delay=0.05):
        mpp = meters_per_pixel(lat)
        radius_px = radius_m / mpp
        fx, fy = _deg2num(lat, lon)
        cx_tile, cy_tile = int(fx), int(fy)
        n_tiles = max(1, math.ceil(radius_px / TILE_SIZE)) + 1  # 中心タイル+周囲

        offsets = [(dx, dy) for dx in range(-n_tiles, n_tiles + 1) for dy in range(-n_tiles, n_tiles + 1)]
        futures = {
            _executor.submit(_fetch_tile, ZOOM, cx_tile + dx, cy_tile + dy, delay): (dx, dy)
            for dx, dy in offsets
        }
        tiles = {}
        for fut, (dx, dy) in futures.items():
            data = fut.result()
            tiles[(dx, dy)] = imread_bytes(data) if data else None

        span = 2 * n_tiles + 1
        img = np.zeros((span * TILE_SIZE, span * TILE_SIZE, 3), dtype=np.uint8)
        for (dx, dy), tile_img in tiles.items():
            if tile_img is None:
                continue
            px, py = (dx + n_tiles) * TILE_SIZE, (dy + n_tiles) * TILE_SIZE
            img[py:py + TILE_SIZE, px:px + TILE_SIZE] = tile_img

        # モザイク画像内でのセンター座標(=lat,lon の位置)
        origin_x_tile = cx_tile - n_tiles
        origin_y_tile = cy_tile - n_tiles
        self.center_px = ((fx - origin_x_tile) * TILE_SIZE, (fy - origin_y_tile) * TILE_SIZE)
        self.img = img
        self.mpp = mpp

    def sample(self, px, py, patch=PATCH):
        """(px,py)を中心にした(2*patch+1)四方の平均色。JPEG圧縮ノイズ・単画素の粒を均す。"""
        h, w = self.img.shape[:2]
        x, y = int(round(px)), int(round(py))
        x0, x1 = max(0, x - patch), min(w, x + patch + 1)
        y0, y1 = max(0, y - patch), min(h, y + patch + 1)
        if x0 >= x1 or y0 >= y1:
            return None
        region = self.img[y0:y1, x0:x1]
        if region.size == 0:
            return None
        return region.reshape(-1, 3).mean(axis=0)


def _scan(mosaic, cx, cy, dx, dy, ref_color, max_px):
    """(cx,cy)から単位ベクトル(dx,dy)方向に、路面色に近い間だけ進む。進んだ画素数を返す。
    基準色は少しずつ現在値へ寄せる(EMA)ので、日照のグラデーション等の緩やかな変化には
    追従しつつ、縁石・芝生等への急な変化はきちんと検知できる。"""
    traveled = 0
    x, y = cx, cy
    ref = ref_color
    while traveled < max_px:
        x += dx * STEP_PX
        y += dy * STEP_PX
        c = mosaic.sample(x, y)
        if c is None:
            break
        if np.linalg.norm(c - ref) > COLOR_DIST_THRESHOLD:
            break
        ref = (1 - EMA_ALPHA) * ref + EMA_ALPHA * c
        traveled += STEP_PX
    return traveled


# 道路種別ごとの走査上限(片側, m)。細い道で隣接する駐車場・広場に扫き込まれるのを防ぐため、
# 実際にその種別の道が取りうる現実的な最大幅より少し余裕を持たせた値に留める。
MAX_SCAN_M_BY_CLASS = {
    "residential": 4.0, "living_street": 3.0, "service": 3.0, "track": 3.0,
    "unclassified": 5.0, "tertiary": 6.0,
    "secondary": 8.0, "primary": 8.0,
    "cycleway": 2.5, "path": 2.5,
}
DEFAULT_MAX_SCAN_M = MAX_SCAN_M


def estimate_paved_width(lat, lon, bearing_deg, highway=None):
    """道路中心線上の(lat,lon)における、進行方向(bearing_deg)に垂直な舗装帯の全幅(m)を推定する。
    highway(OSMのhighwayタグ)が分かれば、道路種別ごとに走査距離の上限を変える
    (細い道が隣の駐車場・広場に扫き込まれて過大評価するのを防ぐ)。"""
    max_scan_m = MAX_SCAN_M_BY_CLASS.get(highway, DEFAULT_MAX_SCAN_M)
    mosaic = Mosaic(lat, lon, radius_m=max_scan_m + 4)
    cx, cy = mosaic.center_px
    ref_color = mosaic.sample(cx, cy)
    if ref_color is None:
        return None

    perp = math.radians(bearing_deg + 90)
    # 画像のy軸は南向きが正(緯度が上ほど小さいpx)なので、東西南北→画素方向への変換に注意
    dx, dy = math.sin(perp), -math.cos(perp)
    max_px = max_scan_m / mosaic.mpp

    left = _scan(mosaic, cx, cy, -dx, -dy, ref_color, max_px)
    right = _scan(mosaic, cx, cy, dx, dy, ref_color, max_px)
    if left == 0 and right == 0:
        return None
    # 片側でも走査上限近くまで届いた場合、本当の境界(縁石・芝生等)を見つけたのではなく
    # 隣接する駐車場・広場等の似た色の舗装に扫き込まれて打ち切られただけの可能性が高い。
    # 大規模検証(47704 edge中の一部)で58〜92%がこの状態だったため、
    # 信頼できない値として捨てる(nullのまま=未計測、過大な確信を持って誤ったランクを
    # 付けるより誠実)。
    if left >= max_px * 0.9 or right >= max_px * 0.9:
        return None
    width_px = left + right
    return round(width_px * mosaic.mpp, 2)


def grade_paved_width(width_m):
    """道路+路肩+歩道を合わせた舗装帯の全幅(m)をS〜Dに変換。
    (Street View写真ベースの「路肩幅のみ」の閾値とは対象が違うため別関数にしている)"""
    if width_m is None:
        return None
    if width_m >= 10:
        return "S"
    if width_m >= 7:
        return "A"
    if width_m >= 5:
        return "B"
    if width_m >= 3.5:
        return "C"
    return "D"
