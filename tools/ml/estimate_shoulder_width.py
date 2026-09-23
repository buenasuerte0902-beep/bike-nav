#!/usr/bin/env python3
"""
Phase4: 「写っている車を物差しにして路肩の実幅を推定する」ユーザー案の実装。
追加学習は不要（YOLOv8のCOCO事前学習重みで車を検出 + SegFormer(Cityscapes事前学習)
で道路領域を意味的に区別する。どちらも既存の重みをそのまま使う）。

考え方:
  1. detect_vehicles.py で写真内の車(車種)を検出する
  2. カメラに一番近い(=画像下端に近い)車を選び、その実幅(車種ごとの平均値)と
     画素幅から「その車がいる奥行きでの pixels/meter」を求める(物差し)
  3. segment_road.py で画像全体を意味的にクラス分け(road/sidewalk/vegetation/...)し、
     車の脇から road クラスが続く画素数を測る(色ではなくクラスで判定するので
     影・反射・水たまり・舗装の質感差に強い — 旧版は色の閾値判定だったが、
     実写真での検証で信頼性不足が分かり、この方式に置き換えた)
  4. その画素距離を pixels/meter で実寸に変換したものを路肩幅の推定値とする

既知の限界（README参照）:
  - 車と同じ奥行きに路肩があるという前提（奥に伸びる路肩やカーブでは誤差が出る）
  - Cityscapesの標準19クラスには専用の"parking"クラスが無く、駐車場や広場のような
    「舗装されてはいるが道路の路肩ではない」領域もroadと分類されがちで過大評価しうる
  - 進行方向に対してどちら側が「路肩」かはカメラの向き次第で確定できないため、
    左右両方を計算し大きい方を採用する(将来: 走行方向タグと突き合わせて精度改善)
  - Mapillaryの写真の多くは360度パノラマ(equirectangular)なので、そのままでは
    歪みで実寸換算が狂う。アスペクト比2:1を検出したら equirect.py で複数方向に
    透視投影してから車検出・スキャンする

単体実行での動作確認:
  python tools/ml/estimate_shoulder_width.py path/to/photo.jpg
  python tools/ml/estimate_shoulder_width.py --selftest   # セグメンテーションモデル無しで幾何ロジックのみ検証
"""
import sys

import numpy as np

import equirect
import segment_road
from detect_vehicles import detect_vehicles
from imutil import imread

STEP_PX = 3
MIN_BOX_W_FRAC = 0.06        # 画像幅に対するこの割合未満の車幅は物差しとして使わない
MAX_BOX_W_FRAC = 0.45        # これを超える幅は「撮影車両自身のボンネット等」の誤検出とみなし除外


def _scan_road_run(seg, y, x_start, direction, max_dist, road_classes=segment_road.ROAD_CLASSES):
    """x_startからdirection(+1/-1)方向に、道路クラスが続く間だけ進む。進んだ画素数を返す。"""
    h, w = seg.shape[:2]
    y = max(0, min(h - 1, y))
    x = x_start
    traveled = 0
    while 0 <= x < w and traveled < max_dist:
        if seg[y, x] not in road_classes:
            break
        x += direction * STEP_PX
        traveled += STEP_PX
    return traveled


def _estimate_from_perspective_image(img):
    """普通の(透視投影の)画像1枚に対して路肩幅推定を試みる。車が無ければNone。"""
    h, w = img.shape[:2]
    vehicles = detect_vehicles(img)
    # 遠く(=画素幅が小さい)車は物差しとしての誤差が大きいので除外する。
    # 逆に画面幅の半分近くを占める検出は、Mapillary撮影車両自身のボンネット/ダッシュボードを
    # 誤検出している可能性が高いので併せて除外する(実写真での検証で発見)
    min_box_w = w * MIN_BOX_W_FRAC
    max_box_w = w * MAX_BOX_W_FRAC
    vehicles = [v for v in vehicles if min_box_w <= (v["box"][2] - v["box"][0]) <= max_box_w]
    if not vehicles:
        return None
    veh = vehicles[0]  # 画像下端に一番近い=カメラに一番近い車
    x1, y1, x2, y2 = veh["box"]
    box_w_px = x2 - x1
    px_per_m = box_w_px / veh["widthM"]

    seg = segment_road.segment(img)
    # 車のbboxのすぐ下（＝車と同じ奥行きの路面）から左右にスキャンする
    sample_y = int(min(h - 1, y2 + 4))
    max_scan_px = int(px_per_m * 6)  # 6m以上は非現実的なので打ち切り
    left_px = _scan_road_run(seg, sample_y, int(x1), -1, max_scan_px)
    right_px = _scan_road_run(seg, sample_y, int(x2), +1, max_scan_px)
    shoulder_px = max(left_px, right_px)

    if left_px == 0 and right_px == 0:
        # 車のすぐ脇が両方ともroadクラスでない(=セグメンテーション自体が車体を
        # roadと誤って隣接判定した等)可能性が高く、0mと断定するのは危険なので未計測扱い
        return None

    return {
        "widthM": round(shoulder_px / px_per_m, 2),
        "pxPerMeter": round(px_per_m, 1),
        "vehicleUsed": veh["cls"],
        "leftPx": left_px,
        "rightPx": right_px,
    }


def estimate_shoulder_width(image_path, compass_angle=None):
    """路肩幅(m)を推定する。車が検出できなければ None を返す(未評価のまま)。
    compass_angle(撮影方位, 0-360)が分かればMapillaryの360度パノラマを
    その進行方向の左右を優先して透視投影してから推定する。"""
    img = imread(image_path)
    if img is None:
        raise FileNotFoundError(image_path)

    if not equirect.is_equirectangular(img):
        return _estimate_from_perspective_image(img)

    for yaw in equirect.candidate_yaws(compass_angle):
        persp = equirect.to_perspective(img, yaw_deg=yaw)
        result = _estimate_from_perspective_image(persp)
        if result is not None:
            result["yawDeg"] = yaw
            return result
    return None


def grade_shoulder_width(width_m):
    """路肩幅(m)をS〜Dに変換（自転車が安心して走れる目安）。"""
    if width_m is None:
        return None
    if width_m >= 1.5:
        return "S"
    if width_m >= 1.0:
        return "A"
    if width_m >= 0.5:
        return "B"
    if width_m >= 0.2:
        return "C"
    return "D"


def _selftest():
    """SegFormerモデルは呼ばずに、_scan_road_run()の幾何ロジックだけを
    既知のラベルマップ(合成)で検証する。"""
    ROAD = segment_road.ROAD
    TERRAIN = segment_road.TERRAIN
    w, h = 800, 400
    seg = np.full((h, w), ROAD, dtype=np.int64)
    car_x1, car_x2 = 300, 440
    row = 324
    shoulder_px_true = 70
    seg[row - 5:row + 5, :car_x1] = TERRAIN                      # 左はすぐ路肩ゼロ
    seg[row - 5:row + 5, car_x2 + shoulder_px_true:] = TERRAIN   # 右は70px先で道路が終わる

    px_per_m = 140 / 1.75  # 車幅140px=1.75mと仮定
    max_scan_px = int(px_per_m * 6)
    left_px = _scan_road_run(seg, row, car_x1, -1, max_scan_px)
    right_px = _scan_road_run(seg, row, car_x2, +1, max_scan_px)
    shoulder_px = max(left_px, right_px)
    est_m = shoulder_px / px_per_m
    print(f"合成ラベル: 路肩={shoulder_px_true}px(既知, 左={left_px}px 右={right_px}px) "
          f"→ 推定{est_m:.2f}m (真値≈{shoulder_px_true/px_per_m:.2f}m)")
    ok = abs(shoulder_px - shoulder_px_true) <= STEP_PX * 2
    print("selftest:", "OK" if ok else "NG(誤差が大きい)")
    return ok


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(0 if _selftest() else 1)
    if len(sys.argv) < 2:
        sys.exit("使い方: python tools/ml/estimate_shoulder_width.py path/to/photo.jpg  (または --selftest)")
    result = estimate_shoulder_width(sys.argv[1])
    if result is None:
        print("車が検出できず、路肩幅は推定できませんでした")
    else:
        print(result, "grade=", grade_shoulder_width(result["widthM"]))
