#!/usr/bin/env python3
"""
指定した市区町村の道路網を OSM(Overpass API) から取得し、
自転車の「走りやすさ」ルーティング用グラフを構築して書き出す。

やること:
  1. Overpass で対象エリアの走行可能な道路(highway=*)と信号ノードを取得
  2. 交差点(=複数wayが共有するノード、またはwayの端点)だけをグラフの頂点にし、
     頂点間を1つの「edge」とする（形状点はedgeのジオメトリとして保持）
  3. 500mを超えるedgeは約500mごとに分割（写真収集(fetch_mapillary_images.py)の
     単位や標高サンプリングの粒度とも揃える）
  4. 本線から孤立した小さな断片（公園の遊歩道など）を除去し、最大の連結成分だけを残す
  5. edgeごとにタグ＋標高からS〜Dランクを4軸(自転車帯/交通量近似/信号/勾配)算出
     （路肩幅・路面の滑らかさの2軸は Phase4(ML) 用に null で予約）
  6. data/graph/<市区町村名>.json に書き出す

使い方:
  python tools/build_graph.py --city 金沢市
  python tools/build_graph.py --city 金沢市 --no-elevation   # 標高APIを叩かず速く試す
  python tools/build_graph.py --city 金沢市 --force          # キャッシュ無視で再生成

標準ライブラリのみ / Python 3.9+
"""
import argparse
import collections
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.normpath(os.path.join(HERE, "..", "data"))
GRAPH_DIR = os.path.join(DATA_DIR, "graph")
ELEV_CACHE_PATH = os.path.join(GRAPH_DIR, "_elevation_cache.json")

EARTH_R = 6371008.8
SEGMENT_TARGET_M = 500     # edgeをこの長さ目安で分割する
SEGMENT_MAX_M = 650        # これを超えるedgeは分割対象
SIGNAL_NEAR_M = 25         # edgeがこの距離内にある信号ノードを「そのedgeの信号」とみなす
ELEV_BATCH = 90            # Open-Elevation 1リクエストあたりの座標数
ELEV_ROUND = 4             # 標高キャッシュのキー丸め桁(小数4桁 ≒ 11m)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# 自転車が走る対象道路。motorway/trunk(自動車専用)は除外
HIGHWAY_ALLOW = (
    "primary|secondary|tertiary|unclassified|residential|living_street|"
    "cycleway|path|track|service"
)


# ---------- 幾何ヘルパー (curve-assist の tools/extract_curves.py と同じ計算式) ----------
def _rad(d):
    return d * math.pi / 180.0


def distance(a, b):
    la1, la2 = _rad(a[0]), _rad(b[0])
    dla = _rad(b[0] - a[0])
    dlo = _rad(b[1] - a[1])
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(h)))


def path_length(pts):
    return sum(distance(pts[i - 1], pts[i]) for i in range(1, len(pts)))


def retry_call(fn, label, max_tries=6):
    """例外時に指数バックオフで再試行。KeyboardInterrupt は透過。"""
    backoff = 0.0
    for attempt in range(1, max_tries + 1):
        try:
            return fn()
        except KeyboardInterrupt:
            raise
        except Exception as ex:  # noqa: BLE001
            if attempt == max_tries:
                raise
            backoff = min(backoff * 2 if backoff else 10.0, 120.0)
            print(f"    {label} エラー({attempt}/{max_tries}): {ex} — {backoff:.0f}s 待機",
                  file=sys.stderr, flush=True)
            time.sleep(backoff)


def overpass(query, timeout=180):
    last = None
    for url in OVERPASS_ENDPOINTS:
        try:
            data = ("data=" + urllib.parse.quote(query)).encode()
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "bike-nav-graphbuilder/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as ex:  # noqa: BLE001
            last = ex
            print(f"  Overpass {url} 失敗: {ex}", file=sys.stderr)
    raise last


# ---------- OSMデータ取得 ----------
def fetch_ways_and_nodes(city):
    q = f"""
    [out:json][timeout:180];
    area["name"="{city}"]["boundary"="administrative"]->.a;
    way(area.a)["highway"~"^({HIGHWAY_ALLOW})$"];
    (._;>;);
    out body;
    """
    return retry_call(lambda: overpass(q), "ways+nodes")


def fetch_signals(city):
    q = f"""
    [out:json][timeout:120];
    area["name"="{city}"]["boundary"="administrative"]->.a;
    node(area.a)["highway"="traffic_signals"];
    out body;
    """
    return retry_call(lambda: overpass(q), "signals")


# ---------- 標高 (Open-Elevation, バッチ問い合わせ + キャッシュ) ----------
def load_elev_cache():
    if os.path.exists(ELEV_CACHE_PATH):
        with open(ELEV_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_elev_cache(cache):
    os.makedirs(GRAPH_DIR, exist_ok=True)
    with open(ELEV_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def elev_key(lat, lon):
    return f"{round(lat, ELEV_ROUND)},{round(lon, ELEV_ROUND)}"


def fetch_elevations(points, cache):
    """points: [(lat,lon), ...] 重複・キャッシュ済みは飛ばしてバッチ取得し cache を更新する。"""
    need = []
    seen = set()
    for lat, lon in points:
        k = elev_key(lat, lon)
        if k in cache or k in seen:
            continue
        seen.add(k)
        need.append((k, lat, lon))
    for i in range(0, len(need), ELEV_BATCH):
        chunk = need[i:i + ELEV_BATCH]
        body = json.dumps({"locations": [{"latitude": la, "longitude": lo} for _, la, lo in chunk]}).encode()
        req = urllib.request.Request(
            "https://api.open-elevation.com/api/v1/lookup",
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "bike-nav-graphbuilder/1.0"},
        )

        def call():
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))

        try:
            res = retry_call(call, f"elevation[{i}:{i+len(chunk)}]", max_tries=4)
            for (k, _, _), item in zip(chunk, res.get("results", [])):
                cache[k] = item.get("elevation")
        except Exception as ex:  # noqa: BLE001
            print(f"  標高取得を諦めて null にします: {ex}", file=sys.stderr)
            for k, _, _ in chunk:
                cache[k] = None
        time.sleep(1.0)


def elevation_of(lat, lon, cache):
    return cache.get(elev_key(lat, lon))


# ---------- グラフ構築 ----------
def build_edges(elements):
    nodes = {}
    ways = []
    for el in elements:
        if el["type"] == "node":
            nodes[el["id"]] = (el["lat"], el["lon"])
        elif el["type"] == "way":
            ways.append({"id": el["id"], "tags": el.get("tags", {}), "nds": el["nodes"]})

    refcount = {}
    for w in ways:
        for nid in w["nds"]:
            refcount[nid] = refcount.get(nid, 0) + 1

    def is_vertex(nid, way):
        return refcount.get(nid, 0) > 1 or nid == way["nds"][0] or nid == way["nds"][-1]

    raw_edges = []
    for w in ways:
        nds = w["nds"]
        if len(nds) < 2:
            continue
        chain = [nds[0]]
        for nid in nds[1:]:
            chain.append(nid)
            if is_vertex(nid, w) and nid != chain[0]:
                raw_edges.append({"wayId": w["id"], "tags": w["tags"], "nodeIds": chain})
                chain = [nid]

    return nodes, raw_edges


def subdivide(nodes, raw_edges):
    """500mを大きく超えるedgeを、既存ノードを保った上でsynthetic頂点で分割する。"""
    out_edges = []
    syn_nodes = {}

    def pt(nid):
        return syn_nodes.get(nid) or nodes[nid]

    for re_ in raw_edges:
        ids = re_["nodeIds"]
        pts = [pt(n) for n in ids]
        total = path_length(pts)
        if total <= SEGMENT_MAX_M or len(pts) < 2:
            out_edges.append({**re_, "points": pts, "from": ids[0], "to": ids[-1], "lengthM": total})
            continue

        n_pieces = max(2, round(total / SEGMENT_TARGET_M))
        piece_len = total / n_pieces
        cur_id = ids[0]
        cur_pts = [pts[0]]
        acc = 0.0
        piece_idx = 0
        i = 1
        while i < len(pts):
            seg = distance(pts[i - 1], pts[i])
            acc += seg
            cur_pts.append(pts[i])
            is_last_point = i == len(pts) - 1
            if acc >= piece_len and not is_last_point:
                piece_idx += 1
                syn_id = f"syn:{re_['wayId']}:{piece_idx}"
                syn_nodes[syn_id] = pts[i]
                out_edges.append({
                    **re_, "points": cur_pts, "from": cur_id, "to": syn_id, "lengthM": path_length(cur_pts),
                })
                cur_id = syn_id
                cur_pts = [pts[i]]
                acc = 0.0
            i += 1
        if len(cur_pts) > 1:
            out_edges.append({
                **re_, "points": cur_pts, "from": cur_id, "to": ids[-1], "lengthM": path_length(cur_pts),
            })

    all_nodes = dict(nodes)
    all_nodes.update(syn_nodes)
    return all_nodes, out_edges


def largest_component(edges):
    """公園の遊歩道など本線から孤立した小さな断片を除く（そこに目的地/出発地が
    スナップされると経路が全く見つからなくなるため）。"""
    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for e in edges:
        parent.setdefault(e["from"], e["from"])
        parent.setdefault(e["to"], e["to"])
    for e in edges:
        union(e["from"], e["to"])

    sizes = collections.Counter(find(nid) for nid in parent)
    biggest_root = sizes.most_common(1)[0][0]
    kept = [e for e in edges if find(e["from"]) == biggest_root]
    dropped = len(edges) - len(kept)
    if dropped:
        print(f"  孤立した断片を除外: edge {dropped}件 ({len(sizes)}個の断片のうち最大成分のみ採用)")
    return kept


def build_signal_index(signal_elements):
    cell = {}
    CELL_DEG = 0.003  # ~330m四方
    for el in signal_elements:
        if el["type"] != "node":
            continue
        lat, lon = el["lat"], el["lon"]
        key = (round(lat / CELL_DEG), round(lon / CELL_DEG))
        cell.setdefault(key, []).append((lat, lon))
    return cell, CELL_DEG


def count_nearby_signals(points, index, cell_deg):
    seen_cells = set()
    for lat, lon in points:
        for dlat in (-1, 0, 1):
            for dlon in (-1, 0, 1):
                seen_cells.add((round(lat / cell_deg) + dlat, round(lon / cell_deg) + dlon))
    count = 0
    for key in seen_cells:
        for slat, slon in index.get(key, []):
            if any(distance((slat, slon), p) <= SIGNAL_NEAR_M for p in points):
                count += 1
    return count


# ---------- ランク付け ----------
GRADE_SCORE = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}


def grade_bike_lane(tags):
    cycleway_vals = [tags.get(k, "") for k in ("cycleway", "cycleway:both", "cycleway:right", "cycleway:left")]
    if tags.get("highway") in ("cycleway", "path", "track"):
        return "S"
    if any(v in ("track", "opposite_track") for v in cycleway_vals):
        return "S"
    if any(v in ("lane", "opposite_lane", "share_busway") for v in cycleway_vals):
        return "B"
    if any(v == "shared_lane" for v in cycleway_vals):
        return "C"
    if tags.get("highway") in ("residential", "living_street", "service"):
        return "C"
    return "D"


def grade_traffic(tags):
    hw = tags.get("highway", "")
    try:
        lanes = int(str(tags.get("lanes", "")).split(";")[0])
    except (ValueError, TypeError):
        lanes = None
    try:
        maxspeed = int(str(tags.get("maxspeed", "")).split(";")[0])
    except (ValueError, TypeError):
        maxspeed = None

    if hw in ("cycleway", "path", "track", "living_street"):
        return "S"
    if hw in ("residential", "service"):
        return "A"
    if hw == "tertiary" and (lanes or 2) <= 2:
        return "B"
    if hw == "secondary" or (lanes or 0) == 3:
        return "C"
    if hw == "primary" or (lanes or 0) >= 4 or (maxspeed or 0) >= 50:
        return "D"
    return "C"


def grade_signals(count):
    return "S" if count == 0 else "B" if count == 1 else "C" if count == 2 else "D"


def grade_slope(pct):
    if pct is None:
        return None
    pct = abs(pct)
    if pct < 1.5:
        return "S"
    if pct < 3:
        return "A"
    if pct < 5:
        return "B"
    if pct < 8:
        return "C"
    return "D"


def composite(grades):
    vals = [GRADE_SCORE[g] for g in grades if g]
    if not vals:
        return None
    avg = sum(vals) / len(vals)
    for g, s in sorted(GRADE_SCORE.items(), key=lambda kv: -kv[1]):
        if avg >= s - 0.5:
            return g
    return "D"


def main():
    ap = argparse.ArgumentParser(description="市区町村の自転車ルーティング用グラフを構築")
    ap.add_argument("--city", default="金沢市")
    ap.add_argument("--no-elevation", action="store_true", help="標高APIを叩かない(勾配は評価しない)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    os.makedirs(GRAPH_DIR, exist_ok=True)
    out_path = os.path.join(GRAPH_DIR, f"{args.city}.json")
    if os.path.exists(out_path) and not args.force:
        sys.exit(f"{out_path} は既に存在します（上書きするなら --force）")

    print(f"[1/6] {args.city} の道路データを取得中 …")
    osm = fetch_ways_and_nodes(args.city)
    print(f"  elements: {len(osm['elements'])}")

    print("[2/6] 信号ノードを取得中 …")
    sig = fetch_signals(args.city)
    sig_index, cell_deg = build_signal_index(sig["elements"])
    print(f"  signals: {len(sig['elements'])}")

    print("[3/6] 交差点を頂点にしてedgeへ分解中 …")
    nodes, raw_edges = build_edges(osm["elements"])
    print(f"  raw edges: {len(raw_edges)}")

    print("[4/6] 500mごとにedgeを分割中 …")
    all_nodes, edges = subdivide(nodes, raw_edges)
    edges = largest_component(edges)
    print(f"  final edges: {len(edges)}")

    elev_cache = {}
    if not args.no_elevation:
        print("[5/6] 標高を取得中（既存キャッシュは再利用） …")
        elev_cache = load_elev_cache()
        endpoints = []
        for e in edges:
            endpoints.append(all_nodes[e["from"]])
            endpoints.append(all_nodes[e["to"]])
        fetch_elevations(endpoints, elev_cache)
        save_elev_cache(elev_cache)
    else:
        print("[5/6] --no-elevation のため標高はスキップ")

    print("[6/6] ランク付けして書き出し中 …")
    out_edges = []
    for i, e in enumerate(edges):
        bike = grade_bike_lane(e["tags"])
        traffic = grade_traffic(e["tags"])
        sig_count = count_nearby_signals(e["points"], sig_index, cell_deg)
        signals_g = grade_signals(sig_count)
        slope_pct = None
        if not args.no_elevation:
            z1 = elevation_of(*all_nodes[e["from"]], elev_cache)
            z2 = elevation_of(*all_nodes[e["to"]], elev_cache)
            if z1 is not None and z2 is not None and e["lengthM"] > 1:
                slope_pct = (z2 - z1) / e["lengthM"] * 100
        slope_g = grade_slope(slope_pct)
        grades = {
            "bikeLane": bike,
            "traffic": traffic,
            "signals": signals_g,
            "slope": slope_g,
            "shoulderWidth": None,   # Phase4(ML) 用に予約
            "smoothness": None,      # Phase4(ML) 用に予約
        }
        out_edges.append({
            "id": f"e{i}",
            "from": str(e["from"]),
            "to": str(e["to"]),
            "points": [[round(lat, 6), round(lon, 6)] for lat, lon in e["points"]],
            "lengthM": round(e["lengthM"], 1),
            "wayId": e["wayId"],
            "tags": {k: e["tags"][k] for k in ("highway", "cycleway", "surface", "maxspeed", "lanes") if k in e["tags"]},
            "grades": grades,
            "total": composite([bike, traffic, signals_g, slope_g]),
            # 符号付き勾配(%, from→to方向)。grades.slopeは向きを無視した「起伏の激しさ」表示用、
            # こちらはルーティング側で上り/下りを区別するために使う(js/route.js)
            "slopePct": round(slope_pct, 2) if slope_pct is not None else None,
        })

    used_node_ids = {str(e["from"]) for e in edges} | {str(e["to"]) for e in edges}
    out_nodes = {
        str(nid): [round(lat, 6), round(lon, 6)]
        for nid, (lat, lon) in all_nodes.items()
        if str(nid) in used_node_ids
    }

    payload = {
        "city": args.city,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "nodes": out_nodes,
        "edges": out_edges,
    }
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, out_path)  # 書き込み中の中断でファイルが壊れないよう一時ファイル経由にする
    size_mb = os.path.getsize(out_path) / 1e6
    print(f"完了: {out_path} ({len(out_nodes)} nodes / {len(out_edges)} edges, {size_mb:.2f}MB)")


if __name__ == "__main__":
    main()
