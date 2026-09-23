"""
cv2.imread/imwrite はWindowsで日本語パス(例: data/photos/金沢市/...)を読み書きできない
既知の問題があるため、これらの代わりに使う。numpy経由で読み書きすればパスのエンコード
問題を回避できる。
"""
import numpy as np
import cv2


def imread(path):
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imread_bytes(data):
    """メモリ上のバイト列(ダウンロードした画像データ等)をBGR画像として読む。"""
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def imwrite(path, img):
    ext = "." + str(path).rsplit(".", 1)[-1]
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        raise IOError(f"encode failed: {path}")
    buf.tofile(str(path))
