#!/usr/bin/env python3
"""
ユーザー投稿バックエンド。走行中に撮った写真を受け取り、最寄りの道路グラフedgeに
スナップして data/photos/<city>/<edgeId>.jpg に保存する（Mapillary写真と同じ場所・
同じ命名規則なので tools/ml/apply_ml_scores.py は無改修でそのまま処理できる）。

このアプリのPWA本体もこのFlaskサーバーが配信する(アップロード機能を使わない
純粋なプレビューだけなら引き続き serve.py でも良い)。

起動:
  pip install flask   # 初回のみ
  python server/app.py                # http://127.0.0.1:8779 (PC確認用)
  python server/app.py --tls --lan    # https://<LAN-IP>:8779 (スマホ実機テスト用)

スマホでカメラ・位置情報を使うには HTTPS が必須(serve.py --tls --lan と同じ理由)。
初回は openssl で自己署名証明書を .certs/ に作る(serve.pyと共有、スマホでは
証明書の警告が出るので「詳細」→「アクセスする」で進む)。
"""
import argparse
import io
import os
import socket
import ssl
import subprocess
import sys
import time

from flask import Flask, Response, jsonify, request, send_from_directory
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
import geo_match

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PHOTOS_DIR = os.path.join(ROOT, "data", "photos")
CERT_DIR = os.path.join(ROOT, ".certs")
CERT = os.path.join(CERT_DIR, "cert.pem")
KEY = os.path.join(CERT_DIR, "key.pem")

MAX_IMAGE_BYTES = 15 * 1024 * 1024

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_BYTES + 1024 * 1024  # 画像+フォーム分の余裕


@app.post("/api/photos")
def upload_photo():
    file = request.files.get("image")
    if file is None:
        return jsonify(error="image is required"), 400

    try:
        lat = float(request.form["lat"])
        lon = float(request.form["lon"])
    except (KeyError, ValueError):
        return jsonify(error="lat/lon is required and must be numbers"), 400

    city = (request.form.get("city") or "").strip()
    device_id = (request.form.get("deviceId") or "").strip()
    if not city or not device_id:
        return jsonify(error="city/deviceId is required"), 400

    compass_angle = request.form.get("compassAngle")
    compass_angle = float(compass_angle) if compass_angle not in (None, "") else None

    raw = file.read()
    if len(raw) > MAX_IMAGE_BYTES:
        return jsonify(error="image too large"), 400
    try:
        Image.open(io.BytesIO(raw)).verify()
    except Exception:  # noqa: BLE001
        return jsonify(error="invalid image file"), 400

    edge_id, distance_m = geo_match.match(city, lat, lon)
    if edge_id is None:
        return jsonify(error="この位置に対応する道路データが見つかりません"), 400

    out_dir = os.path.join(PHOTOS_DIR, city)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{edge_id}.jpg")
    with open(out_path, "wb") as f:
        f.write(raw)

    conn = db.get_conn()
    try:
        db.insert_photo(
            conn, city=city, edge_id=edge_id, device_id=device_id, source="user",
            lat=lat, lon=lon, distance_m=distance_m, compass_angle=compass_angle,
        )
    finally:
        conn.close()

    return jsonify(edgeId=edge_id, distanceM=round(distance_m, 1)), 201


@app.get("/api/photos/coverage")
def coverage():
    city = request.args.get("city", "")
    if not city:
        return jsonify(error="city is required"), 400
    conn = db.get_conn()
    try:
        return jsonify(city=city, bySource=db.coverage_by_city(conn, city))
    finally:
        conn.close()


@app.get("/sw.js")
def service_worker():
    # Service Worker登録はレスポンスの検証が厳しいため、send_file由来の余計な
    # ヘッダ(ETag/Content-Disposition等)を避けて最小限のレスポンスで返す
    with open(os.path.join(ROOT, "sw.js"), "rb") as f:
        content = f.read()
    return Response(content, mimetype="text/javascript")


@app.get("/")
def index():
    return send_from_directory(ROOT, "index.html", conditional=False)


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(ROOT, path, conditional=False)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ensure_cert(ip):
    if os.path.exists(CERT) and os.path.exists(KEY):
        return
    os.makedirs(CERT_DIR, exist_ok=True)
    san = f"subjectAltName=DNS:localhost,IP:127.0.0.1,IP:{ip}"
    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", KEY, "-out", CERT, "-days", "3650",
        "-subj", "/CN=bike-nav", "-addext", san,
    ]
    print("自己署名証明書を作成中 …", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        sys.exit(
            "openssl が見つからない/失敗しました。openssl を入れるか、"
            f"手動で {CERT_DIR} に cert.pem / key.pem を用意してください。\n{e}"
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ユーザー投稿バックエンド")
    ap.add_argument("--lan", action="store_true", help="0.0.0.0で待ち受け(同じWi-Fiのスマホから見える)")
    ap.add_argument("--tls", action="store_true", help="HTTPS(自己署名)。スマホのカメラ/位置情報に必要")
    args = ap.parse_args()

    port = int(os.environ.get("PORT", 8779))
    host = "0.0.0.0" if args.lan else "127.0.0.1"
    ip = lan_ip()
    scheme = "http"
    ssl_context = None
    if args.tls:
        ensure_cert(ip)
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(CERT, KEY)
        scheme = "https"

    print(f"\n配信中: {ROOT}")
    print(f"  この PC:      {scheme}://127.0.0.1:{port}/")
    if host == "0.0.0.0":
        print(f"  スマホ等から: {scheme}://{ip}:{port}/   ← 同じ Wi-Fi でこれを開く")
        if args.tls:
            print("  （証明書の警告は「詳細設定」→「アクセスする」で進む）")
    print("Ctrl+C で停止\n")
    app.run(host=host, port=port, debug=False, threaded=True, ssl_context=ssl_context)
