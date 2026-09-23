"""
緯度経度から道路グラフの最寄りedgeを特定する。
tools/apply_traffic_census.py で使った「メートル近似平面に投影→粗いグリッドで
候補を絞り込み→shapelyの正確な距離」というパターンをedgeマッチング用に切り出したもの。
"""
import json
import math
import os
from collections import defaultdict
from functools import lru_cache

from shapely.geometry import LineString, Point

HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH_DIR = os.path.normpath(os.path.join(HERE, "..", "data", "graph"))
GRID_CELL_M = 200
MAX_MATCH_M = 200  # これより遠い投稿は「対象道路が無い」として拒否


def _projector(ref_lat):
    m_per_lat = 111320.0
    m_per_lon = 111320.0 * math.cos(math.radians(ref_lat))

    def to_xy(lon, lat):
        return (lon * m_per_lon, lat * m_per_lat)

    return to_xy


class EdgeIndex:
    def __init__(self, graph):
        all_lats = [lat for lat, lon in graph["nodes"].values()]
        self.to_xy = _projector(sum(all_lats) / len(all_lats))
        self.bounds = (
            min(lon for lat, lon in graph["nodes"].values()),
            min(all_lats),
            max(lon for lat, lon in graph["nodes"].values()),
            max(all_lats),
        )
        self.edges = []
        self.grid = defaultdict(list)
        for e in graph["edges"]:
            xy = [self.to_xy(lon, lat) for lat, lon in e["points"]]
            if len(xy) < 2:
                continue
            line = LineString(xy)
            idx = len(self.edges)
            self.edges.append({"id": e["id"], "line": line})
            minx, miny, maxx, maxy = line.bounds
            for gx in range(int(minx // GRID_CELL_M), int(maxx // GRID_CELL_M) + 1):
                for gy in range(int(miny // GRID_CELL_M), int(maxy // GRID_CELL_M) + 1):
                    self.grid[(gx, gy)].append(idx)

    def in_bounds(self, lat, lon, pad=0.02):
        west, south, east, north = self.bounds
        return (west - pad) <= lon <= (east + pad) and (south - pad) <= lat <= (north + pad)

    def nearest_edge(self, lat, lon):
        x, y = self.to_xy(lon, lat)
        gx, gy = int(x // GRID_CELL_M), int(y // GRID_CELL_M)
        candidates = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                candidates.update(self.grid.get((gx + dx, gy + dy), []))
        if not candidates:
            return None, None
        pt = Point(x, y)
        best_d, best_id = None, None
        for i in candidates:
            d = self.edges[i]["line"].distance(pt)
            if best_d is None or d < best_d:
                best_d, best_id = d, self.edges[i]["id"]
        return best_id, best_d


@lru_cache(maxsize=8)
def load_index(city):
    path = os.path.join(GRAPH_DIR, f"{city}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        graph = json.load(f)
    return EdgeIndex(graph)


def match(city, lat, lon):
    """(city, lat, lon) -> (edgeId, distanceM) 。マッチ無しなら (None, None)。"""
    index = load_index(city)
    if index is None or not index.in_bounds(lat, lon):
        return None, None
    edge_id, dist = index.nearest_edge(lat, lon)
    if edge_id is None or dist > MAX_MATCH_M:
        return None, None
    return edge_id, dist
