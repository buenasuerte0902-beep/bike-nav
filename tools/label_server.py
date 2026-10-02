#!/usr/bin/env python3
"""
路肩の広さ・路面の滑らかさを「自分の目で見て」判定するローカルのラベリングツール。

  python tools/label_server.py            # http://127.0.0.1:8795 を開く
  python tools/label_server.py --port 8796

- data/graph/ にある任意の都市(build_graph.py --city <名前> で作ったもの)で使える。
  画面左上で都市を切り替え、「この範囲で並べ直す」で地図上の好きな場所に絞れる。
- 道路を同じ道・約200mごとのまとまり(区間)にして、1区間=キー2回(路肩→路面)で判定。
- 見せるもの: 国土地理院の航空写真、手元のMapillary写真(data/photos/<city>/)。
  Google Street View は自動取得せず、ボタンで普通のGoogle Maps URLを開くだけ。
- 判定は data/manual_labels/<city>.json に即時保存。「グラフに反映」で
  grades.shoulderWidth / smoothness に書き込む(label_via_streetview.py --merge と同じ処理)。
- 路面の滑らかさは、写真のある区間について data/photos/<city>/_labels_smoothness.json にも
  書き、tools/ml/train_smoothness.py の学習データとして使える。

ローカル(127.0.0.1)専用。標準ライブラリのみ。
"""
import argparse
import json
import math
import os
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DATA_DIR = os.path.join(ROOT, "data")
GRAPH_DIR = os.path.join(DATA_DIR, "graph")
LABELS_DIR = os.path.join(DATA_DIR, "manual_labels")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
UI_DIR = os.path.join(HERE, "label")
LEAFLET_DIR = os.path.join(ROOT, "vendor", "leaflet")

sys.path.insert(0, HERE)
import label_via_streetview as lvs  # noqa: E402  (do_merge を再利用)

CHUNK_MAX_M = 200
PHOTO_MAX_M = 150        # 区間の判定材料として見せる写真の最大距離
PHOTO_TRAIN_MAX_M = 80   # 学習データに書き込む写真の最大距離(区間のラベルを写真に当てるため厳しめ)
VALID = {"S", "A", "B", "C", "D"}
CLASS_RANK = {
    "primary": 0, "secondary": 1, "tertiary": 2, "unclassified": 3, "residential": 4,
    "living_street": 5, "cycleway": 6, "service": 7, "path": 8, "track": 9,
}
CITY_RE = re.compile(r"^[^\\/:*?\"<>|.]{1,40}$")
EDGE_RE = re.compile(r"^e\d{1,9}$")

_lock = threading.Lock()
_cities = {}


def atomic_write_json(path, obj, **kw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, **kw)
    os.replace(tmp, path)


def read_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def bearing(p, q):
    la1, la2 = math.radians(p[0]), math.radians(q[0])
    dl = math.radians(q[1] - p[1])
    y = math.sin(dl) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


class City:
    def __init__(self, name):
        self.name = name
        graph_path = os.path.join(GRAPH_DIR, f"{name}.json")
        with open(graph_path, encoding="utf-8") as f:
            g = json.load(f)
        self.labels_path = os.path.join(LABELS_DIR, f"{name}.json")
        self.labels = read_json(self.labels_path, {})
        self.photo_dir = os.path.join(PHOTOS_DIR, name)
        self.meta = read_json(os.path.join(self.photo_dir, "_meta.json"), {})
        self.smooth_path = os.path.join(self.photo_dir, "_labels_smoothness.json")
        self.photo_ids = set()
        if os.path.isdir(self.photo_dir):
            self.photo_ids = {f[:-4] for f in os.listdir(self.photo_dir) if f.endswith(".jpg")}
        self.chunks = self._build_chunks(g["edges"])
        self.chunk_by_id = {c["id"]: c for c in self.chunks}
        self.default_bbox = self._densest_bbox()

    def _build_chunks(self, edges):
        chunks, cur, length = [], [], 0.0

        def close():
            if cur:
                chunks.append(self._make_chunk(cur, length))

        for e in edges:
            if cur and (e["wayId"] != cur[-1]["wayId"] or e["from"] != cur[-1]["to"] or length >= CHUNK_MAX_M):
                close()
                cur, length = [], 0.0
            cur.append(e)
            length += e["lengthM"]
        close()
        return chunks

    def _make_chunk(self, es, length):
        pts = [p for e in es for p in e["points"]]
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        photos = []
        for e in es:
            m = self.meta.get(e["id"])
            if e["id"] in self.photo_ids and m and m.get("distanceM", 0) <= PHOTO_MAX_M:
                p = e["points"]
                photos.append({
                    "edgeId": e["id"], "distanceM": m.get("distanceM"),
                    "compass": m.get("compassAngle"), "capturedAt": m.get("capturedAt"),
                    "bearing": round(bearing(p[0], p[-1])),
                })
        photos.sort(key=lambda p: p["distanceM"] if p["distanceM"] is not None else 1e9)
        hw = es[0]["tags"].get("highway", "")
        return {
            "id": es[0]["id"], "edgeIds": [e["id"] for e in es], "highway": hw,
            "lengthM": round(length), "center": [lat, lon],
            "lines": [[[round(p[0], 5), round(p[1], 5)] for p in e["points"]] for e in es],
            "photos": photos[:4], "guess": es[0]["grades"].get("shoulderWidth"),
        }

    def _densest_bbox(self):
        cell = 0.02
        count = {}
        for c in self.chunks:
            k = (int(c["center"][0] // cell), int(c["center"][1] // cell))
            count[k] = count.get(k, 0) + 1
        if not count:
            return None
        (cy, cx), _ = max(count.items(), key=lambda kv: kv[1])
        return [(cy - 1) * cell, (cx - 1) * cell, (cy + 2) * cell, (cx + 2) * cell]

    def queue(self, bbox):
        s, w, n, e = bbox
        sel = [c for c in self.chunks if s <= c["center"][0] <= n and w <= c["center"][1] <= e]
        cy, cx = (s + n) / 2, (w + e) / 2
        sel.sort(key=lambda c: (
            CLASS_RANK.get(c["highway"], 10),
            (c["center"][0] - cy) ** 2 + ((c["center"][1] - cx) * 0.8) ** 2,
        ))
        out = []
        for c in sel:
            d = dict(c)
            lab = self.labels.get(c["id"])
            d["label"] = {k: lab.get(k) for k in ("shoulderWidth", "smoothness")} if lab else None
            out.append(d)
        return out

    def set_label(self, chunk_id, shoulder, smooth):
        c = self.chunk_by_id[chunk_id]
        for eid in c["edgeIds"]:
            entry = self.labels.get(eid, {})
            if shoulder:
                entry["shoulderWidth"] = shoulder
            if smooth:
                entry["smoothness"] = smooth
            entry["chunk"] = chunk_id
            self.labels[eid] = entry
        atomic_write_json(self.labels_path, self.labels, indent=0)
        if smooth:
            train = read_json(self.smooth_path, {})
            for p in c["photos"]:
                if (p["distanceM"] or 0) <= PHOTO_TRAIN_MAX_M:
                    train[p["edgeId"]] = smooth
            atomic_write_json(self.smooth_path, train, indent=0)

    def clear_label(self, chunk_id):
        c = self.chunk_by_id[chunk_id]
        for eid in c["edgeIds"]:
            if self.labels.get(eid, {}).get("chunk") == chunk_id:
                del self.labels[eid]
        atomic_write_json(self.labels_path, self.labels, indent=0)
        if os.path.exists(self.smooth_path):
            train = read_json(self.smooth_path, {})
            for p in c["photos"]:
                train.pop(p["edgeId"], None)
            atomic_write_json(self.smooth_path, train, indent=0)


def get_city(name):
    if not CITY_RE.match(name) or not os.path.exists(os.path.join(GRAPH_DIR, f"{name}.json")):
        raise KeyError(name)
    with _lock:
        if name not in _cities:
            t = time.time()
            _cities[name] = City(name)
            print(f"loaded {name}: {len(_cities[name].chunks)} chunks ({time.time() - t:.1f}s)")
        return _cities[name]


def list_cities():
    out = []
    for f in sorted(os.listdir(GRAPH_DIR)):
        if f.endswith(".json") and not f.startswith("_"):
            name = f[:-5]
            labels = read_json(os.path.join(LABELS_DIR, f"{name}.json"), {})
            out.append({"name": name, "labeledEdges": len(labels)})
    return out


STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/label.js": ("label.js", "text/javascript; charset=utf-8"),
    "/label.css": ("label.css", "text/css; charset=utf-8"),
}
LEAFLET_TYPES = {".js": "text/javascript", ".css": "text/css", ".png": "image/png"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype, cache=False):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return self._send(404, {"error": "not found"})
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        try:
            if p in STATIC:
                name, ctype = STATIC[p]
                return self._file(os.path.join(UI_DIR, name), ctype)
            m = re.match(r"^/vendor/leaflet/([\w.\-/]+)$", p)
            if m and ".." not in m.group(1):
                full = os.path.join(LEAFLET_DIR, *m.group(1).split("/"))
                return self._file(full, LEAFLET_TYPES.get(os.path.splitext(full)[1], "application/octet-stream"), cache=True)
            m = re.match(r"^/photo/([^/]+)/(e\d+)\.jpg$", p)
            if m:
                from urllib.parse import unquote
                city = unquote(m.group(1))
                if not CITY_RE.match(city):
                    return self._send(400, {"error": "bad city"})
                return self._file(os.path.join(PHOTOS_DIR, city, m.group(2) + ".jpg"), "image/jpeg", cache=True)
            if p == "/api/cities":
                return self._send(200, list_cities())
            if p == "/api/queue":
                city = get_city(q.get("city", [""])[0])
                if "bbox" in q:
                    bbox = [float(x) for x in q["bbox"][0].split(",")]
                    if len(bbox) != 4:
                        return self._send(400, {"error": "bbox"})
                else:
                    bbox = city.default_bbox
                chunks = city.queue(bbox)
                return self._send(200, {"city": city.name, "bbox": bbox, "chunks": chunks})
            return self._send(404, {"error": "not found"})
        except KeyError:
            return self._send(404, {"error": "unknown city"})
        except (ValueError, IndexError):
            return self._send(400, {"error": "bad request"})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n) or b"{}")
            city = get_city(str(body.get("city", "")))
            if u.path == "/api/label":
                cid = str(body.get("chunk", ""))
                if not EDGE_RE.match(cid) or cid not in city.chunk_by_id:
                    return self._send(400, {"error": "bad chunk"})
                with _lock:
                    if body.get("clear"):
                        city.clear_label(cid)
                    else:
                        sw, sm = body.get("shoulderWidth"), body.get("smoothness")
                        if sw not in VALID | {None} or sm not in VALID | {None} or (sw is None and sm is None):
                            return self._send(400, {"error": "bad grade"})
                        city.set_label(cid, sw, sm)
                return self._send(200, {"ok": True, "labeledEdges": len(city.labels)})
            if u.path == "/api/merge":
                import argparse as _ap
                import contextlib
                import io
                buf = io.StringIO()
                with _lock, contextlib.redirect_stdout(buf):
                    try:
                        lvs.do_merge(_ap.Namespace(city=city.name))
                    except SystemExit as ex:
                        return self._send(400, {"error": str(ex)})
                return self._send(200, {"ok": True, "message": buf.getvalue().strip()})
            return self._send(404, {"error": "not found"})
        except KeyError:
            return self._send(404, {"error": "unknown city"})
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "bad request"})


def main():
    ap = argparse.ArgumentParser(description="手動ラベリングツール(ローカル)")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"ラベリングツール: {url}  (Ctrl+Cで終了。判定は即時保存されます)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
