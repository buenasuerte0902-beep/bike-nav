#!/usr/bin/env python3
"""
静的ファイル配信サーバー。

  python serve.py                 # http://127.0.0.1:8778 (開発・ブラウザ確認用)
  python serve.py --lan           # http://<LAN-IP>:8778  (同じ Wi-Fi の別端末から)
  python serve.py --tls --lan     # https://<LAN-IP>:8444 (スマホの GPS を使うテスト)

スマホの位置情報 API (watchPosition) は「安全なコンテキスト」でしか動かないため、
実機テストは --tls 必須。初回に openssl で自己署名証明書を .certs/ に作成する
(スマホでは証明書の警告が出るので「詳細」→「アクセスする」で進む)。

ポートは環境変数 PORT でも指定可。
"""
import argparse
import os
import socket
import ssl
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CERT_DIR = os.path.join(HERE, ".certs")
CERT = os.path.join(CERT_DIR, "cert.pem")
KEY = os.path.join(CERT_DIR, "key.pem")


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".webmanifest": "application/manifest+json",
        ".json": "application/json",
    }

    def end_headers(self):
        # 開発中はブラウザHTTPキャッシュを無効化（Service Worker のオフライン保存には影響しない）
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))


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


def main():
    ap = argparse.ArgumentParser(description="静的配信サーバー")
    ap.add_argument("--host", default=None, help="バインド先 (既定 127.0.0.1、--lan で 0.0.0.0)")
    ap.add_argument("--lan", action="store_true", help="0.0.0.0 で待ち受け（LAN の別端末から見える）")
    ap.add_argument("--tls", action="store_true", help="HTTPS（自己署名）。スマホ GPS テストに必要")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("port_pos", nargs="?", type=int, help=argparse.SUPPRESS)
    args = ap.parse_args()

    port = args.port or args.port_pos or int(os.environ.get("PORT") or 0) or (8444 if args.tls else 8778)
    host = args.host or ("0.0.0.0" if args.lan else "127.0.0.1")
    ip = lan_ip()

    os.chdir(HERE)
    httpd = ThreadingHTTPServer((host, port), Handler)
    scheme = "http"
    if args.tls:
        ensure_cert(ip)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT, KEY)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"

    print(f"\n配信中: {HERE}")
    print(f"  この PC:      {scheme}://127.0.0.1:{port}/")
    if host == "0.0.0.0":
        print(f"  スマホ等から: {scheme}://{ip}:{port}/   ← 同じ Wi-Fi でこれを開く")
        if args.tls:
            print("  （証明書の警告は「詳細設定」→「アクセスする」で進む）")
    print("Ctrl+C で停止\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
