"""
Mapillaryの写真の多くは360度全天球パノラマ(equirectangular, 横:縦=2:1)。
このままYOLOや「車幅を物差しにした距離推定」に掛けると、画像の端に行くほど
横方向が伸び縮みする歪みのせいで実寸換算が狂う。そこで指定した向き(yaw)を
中心にした「普通の写真」相当(透視投影)に一度変換してから使う。
"""
import numpy as np
import cv2


def is_equirectangular(img, tol=0.15):
    h, w = img.shape[:2]
    return abs(w / h - 2.0) <= tol


def to_perspective(img, yaw_deg, pitch_deg=-8.0, fov_deg=100.0, out_w=1024, out_h=768):
    """equirectangular画像から、yaw_deg方向を中心にした透視投影画像を切り出す。
    pitch_degは既定でやや下向き(-8度)=路面・車が写りやすい向き。"""
    src_h, src_w = img.shape[:2]
    yaw = np.deg2rad(yaw_deg)
    pitch = np.deg2rad(pitch_deg)
    fov = np.deg2rad(fov_deg)

    f = (out_w / 2) / np.tan(fov / 2)
    xs, ys = np.meshgrid(np.arange(out_w, dtype=np.float64), np.arange(out_h, dtype=np.float64))
    x = xs - out_w / 2
    y = ys - out_h / 2
    z = np.full_like(x, f)

    norm = np.sqrt(x ** 2 + y ** 2 + z ** 2)
    x, y, z = x / norm, y / norm, z / norm

    # pitch: x軸まわりの回転
    cp, sp = np.cos(pitch), np.sin(pitch)
    y2 = y * cp - z * sp
    z2 = y * sp + z * cp

    # yaw: y軸まわりの回転
    cy, sy = np.cos(yaw), np.sin(yaw)
    x3 = x * cy + z2 * sy
    z3 = -x * sy + z2 * cy
    y3 = y2

    lon = np.arctan2(x3, z3)                      # -pi..pi
    lat = np.arcsin(np.clip(-y3, -1.0, 1.0))       # -pi/2..pi/2 (画像下方向がlat負)

    src_x = ((lon / (2 * np.pi)) + 0.5) * src_w
    src_y = (0.5 - lat / np.pi) * src_h

    return cv2.remap(
        img, src_x.astype(np.float32), src_y.astype(np.float32),
        interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP,
    )


def candidate_yaws(compass_angle):
    """物差しにする車を探す候補の向き。真横(進行方向±90度)は路肩が見えやすく、
    正面(compass_angle)は対向車が見えやすい。compass_angleが無ければ4方位を試す。"""
    if compass_angle is None:
        return [0, 90, 180, 270]
    return [
        (compass_angle + 90) % 360,
        (compass_angle - 90) % 360,
        compass_angle % 360,
        (compass_angle + 180) % 360,
    ]
