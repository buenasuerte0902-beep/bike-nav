#!/usr/bin/env python3
"""
Google Street View Static API(公式API・自分のAPIキー)で道路区間ごとの画像を取得し、
その場(メモリ上)で路肩幅を推定して、**数値だけ**を data/streetview_scores/<city>.json に
保存する。画像そのものはディスクに保存しない(APIの利用規約はコンテンツの保存・再利用を
制限しているため)。推定は tools/ml/estimate_shoulder_width.py の「車を物差しにした
幾何推定」で、学習(ML訓練)にGoogleの画像は使わない。

事前準備(キーはコマンドやチャットに書かない):
  1. Google Cloud で Street View Static API を有効化し、請求先とAPIキーを用意
     (キーはHTTPリファラ/APIの制限を付けておく。予算アラートも設定すること)
  2. キーを環境変数 GOOGLE_MAPS_API_KEY か tools/.streetview_key(gitignore済み)に置く

使い方:
  python tools/fetch_streetview_scores.py --city 金沢市 --dry-run          # 件数と概算費用だけ表示(API呼び出しなし)
  python tools/fetch_streetview_scores.py --city 金沢市 --limit 30         # 30区間だけ試す
  python tools/fetch_streetview_scores.py --city 金沢市                    # 初期範囲(市街地)を実行
  python tools/fetch_streetview_scores.py --city 金沢市 --bbox 36.55,136.64,36.58,136.67
  python tools/fetch_streetview_scores.py --city 金沢市 --apply            # 結果をグラフに反映
  python tools/fetch_streetview_scores.py --selftest                        # APIを呼ばずに処理の流れだけ確認

費用の目安: 画像取得は有料(メタデータ取得は無料)。1区間あたり最大 --shots 枚
(最初に成功した時点で打ち切る)。--max-images を超えたら自動停止する。料金は公式の
料金表で必ず確認すること。手動ラベル済みの区間は取得しない。結果は区間(約200m)単位で、
区間内の全辺に適用される(label_server.py と同じ区間分け)。
"""
import argparse
import json
import math
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "ml"))

DATA_DIR = os.path.normpath(os.path.join(HERE, "..", "data"))
GRAPH_DIR = os.path.join(DATA_DIR, "graph")
LABELS_DIR = os.path.join(DATA_DIR, "manual_labels")
RESULT_DIR = os.path.join(DATA_DIR, "streetview_scores")

API = "https://maps.googleapis.com/maps/api/streetview"
DEFAULT_CLASSES = "primary,secondary,tertiary,unclassified,residential"
PRICE_PER_1000_USD = 7.0  # 目安。公式料金表で要確認
FATAL_STATUS = {"REQUEST_DENIED", "OVER_QUERY_LIMIT", "INVALID_REQUEST"}


class ApiError(Exception):
    pass


def load_key():
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        path = os.path.join(HERE, ".streetview_key")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                key = f.read().strip()
    return key


def http_get(url, timeout=20):
    # 例外メッセージにURL(=APIキー)を含めない
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise ApiError(f"HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ApiError(type(e).__name__) from None


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


def metadata(get, key, lat, lon):
    qs = urllib.parse.urlencode({"location": f"{lat:.6f},{lon:.6f}", "radius": 30, "source": "outdoor", "key": key})
    return json.loads(get(f"{API}/metadata?{qs}"))


def static_image(get, key, pano_id, heading):
    qs = urllib.parse.urlencode({
        "size": "640x400", "pano": pano_id, "heading": f"{heading:.0f}", "fov": 90, "pitch": -5,
        "return_error_code": "true", "key": key,
    })
    return get(f"{API}/?{qs}")


class Runner:
    def __init__(self, get, key, shots, max_images, result_path, delay=0.05):
        self.get, self.key, self.shots, self.max_images = get, key, shots, max_images
        self.result_path, self.delay = result_path, delay
        self.results = read_json(result_path, {})
        self.n_images = 0

    def process(self, chunk):
        from estimate_shoulder_width import _estimate_from_perspective_image, grade_shoulder_width
        from imutil import imread_bytes
        from label_server import bearing

        mid_line = chunk["lines"][len(chunk["lines"]) // 2]
        a, b = mid_line[0], mid_line[-1]
        lat, lon = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        base = bearing(a, b)

        meta = metadata(self.get, self.key, lat, lon)
        status = meta.get("status")
        if status in FATAL_STATUS:
            raise SystemExit(f"Street View APIがエラーを返しました: {status}。キー・請求設定・API有効化を確認してください。")
        if status != "OK":
            return {"none": "no_pano"}
        pano = meta["pano_id"]
        out = {"panoId": pano, "panoDate": meta.get("date")}

        for k, off in enumerate([0, 180, 90, 270][: self.shots]):
            if self.n_images >= self.max_images:
                raise StopIteration
            heading = (base + off) % 360
            self.n_images += 1
            time.sleep(self.delay)
            try:
                data = static_image(self.get, self.key, pano, heading)
            except ApiError:
                continue
            img = imread_bytes(data)
            if img is None:
                continue
            r = _estimate_from_perspective_image(img)
            if r is not None:
                out.update({"widthM": r["widthM"], "grade": grade_shoulder_width(r["widthM"]),
                            "heading": round(heading), "vehicle": r["vehicleUsed"]})
                return out
        out["none"] = "no_vehicle"
        return out

    def run(self, chunks, labels):
        done = 0
        try:
            for c in chunks:
                if c["id"] in self.results or c["id"] in labels:
                    continue
                try:
                    self.results[c["id"]] = self.process(c)
                except ApiError as e:
                    print(f"  {c['id']}: 失敗 {e}", file=sys.stderr)
                    continue
                done += 1
                if done % 25 == 0:
                    atomic_write_json(self.result_path, self.results, indent=0)
                    ok = sum(1 for v in self.results.values() if v.get("grade"))
                    print(f"  {done}区間 処理 / 成功 {ok} / 画像 {self.n_images}枚")
        except StopIteration:
            print(f"--max-images ({self.max_images}) に達したため停止します")
        finally:
            atomic_write_json(self.result_path, self.results, indent=0)
        ok = sum(1 for v in self.results.values() if v.get("grade"))
        print(f"完了: 今回 {done}区間 / 累計成功 {ok} / 画像 {self.n_images}枚 → {self.result_path}")


def pick_chunks(city, bbox, classes, limit):
    sel = [c for c in city.queue(bbox or city.default_bbox) if c["highway"] in classes]
    return sel[:limit] if limit else sel


def apply_results(city, results, graph_path, labels):
    """結果をグラフに反映。手動ラベル済みの辺は上書きしない(区間分けは label_server と共通)。"""
    import label_via_streetview as lvs
    with open(graph_path, encoding="utf-8") as f:
        graph = json.load(f)
    by_id = {e["id"]: e for e in graph["edges"]}
    n = 0
    for c in city.chunks:
        r = results.get(c["id"])
        if not r or not r.get("grade"):
            continue
        for eid in c["edgeIds"]:
            if eid in labels:
                continue
            by_id[eid]["grades"]["shoulderWidth"] = r["grade"]
            by_id[eid]["total"] = lvs.composite(by_id[eid]["grades"].values())
            n += 1
    atomic_write_json(graph_path, graph, separators=(",", ":"))
    return n


def selftest():
    """APIを呼ばず、手元のMapillary全天球写真から透視画像を作ってAPIの代わりに返す。"""
    import cv2
    import equirect
    from imutil import imread
    from label_server import City

    photo_dir = os.path.join(DATA_DIR, "photos", "金沢市")
    pano_files = []
    for f in sorted(os.listdir(photo_dir)):
        if f.endswith(".jpg"):
            im = imread(os.path.join(photo_dir, f))
            if im is not None and equirect.is_equirectangular(im):
                pano_files.append(im)
            if len(pano_files) >= 3:
                break

    calls = {"meta": 0, "img": 0}

    def fake_get(url):
        if "/metadata" in url:
            calls["meta"] += 1
            return json.dumps({"status": "OK", "pano_id": "TEST", "date": "2024-01"}).encode()
        calls["img"] += 1
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        heading = float(q["heading"][0])
        im = pano_files[calls["img"] % len(pano_files)]
        persp = equirect.to_perspective(im, yaw_deg=heading, out_w=640, out_h=400)
        ok, buf = cv2.imencode(".jpg", persp)
        return buf.tobytes()

    city = City("金沢市")
    chunks = pick_chunks(city, None, DEFAULT_CLASSES.split(","), 6)
    with tempfile.TemporaryDirectory() as tmp:
        rpath = os.path.join(tmp, "r.json")
        run = Runner(fake_get, "SELFTEST", shots=2, max_images=50, result_path=rpath, delay=0)
        run.run(chunks, {})
        print(json.dumps(list(run.results.items())[:3], ensure_ascii=False))
        assert len(run.results) == len(chunks), "全区間が結果に入っていない"
        assert calls["meta"] == len(chunks)
        print(f"selftest OK: メタデータ{calls['meta']}回 / 画像{calls['img']}枚(モック)")


def main():
    ap = argparse.ArgumentParser(description="Street View Static API(公式)で路肩幅を推定。画像は保存しない")
    ap.add_argument("--city", default="金沢市")
    ap.add_argument("--bbox", help="south,west,north,east (省略時は都市内で道路が最も密な範囲)")
    ap.add_argument("--classes", default=DEFAULT_CLASSES)
    ap.add_argument("--limit", type=int, help="先頭からN区間だけ")
    ap.add_argument("--shots", type=int, default=2, choices=[1, 2, 3, 4], help="1区間あたり最大何枚試すか")
    ap.add_argument("--max-images", type=int, default=3000, help="これを超える枚数は取得せず停止(課金の安全弁)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true", help="保存済みの結果をグラフJSONに反映")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    from label_server import City
    city = City(args.city)
    result_path = os.path.join(RESULT_DIR, f"{args.city}.json")
    labels = read_json(os.path.join(LABELS_DIR, f"{args.city}.json"), {})

    if args.apply:
        results = read_json(result_path, {})
        if not results:
            sys.exit(f"{result_path} がありません。先に取得を実行してください。")
        n = apply_results(city, results, os.path.join(GRAPH_DIR, f"{args.city}.json"), labels)
        print(f"反映: {n}辺 → data/graph/{args.city}.json")
        return

    bbox = [float(x) for x in args.bbox.split(",")] if args.bbox else None
    chunks = pick_chunks(city, bbox, set(args.classes.split(",")), args.limit)
    results = read_json(result_path, {})
    todo = [c for c in chunks if c["id"] not in results and c["id"] not in labels]
    worst = len(todo) * args.shots
    print(f"対象 {len(chunks)}区間 / 未処理 {len(todo)}区間 / 最大 {worst}枚 "
          f"(上限 {args.max_images}枚で停止) / 画像の概算費用(最大) ${min(worst, args.max_images) * PRICE_PER_1000_USD / 1000:.0f} "
          f"※無料枠・最新料金は公式で確認")
    if args.dry_run or not todo:
        return

    key = load_key()
    if not key:
        sys.exit("APIキーがありません。環境変数 GOOGLE_MAPS_API_KEY か tools/.streetview_key に置いてください。")
    Runner(http_get, key, args.shots, args.max_images, result_path).run(todo, labels)


if __name__ == "__main__":
    main()
