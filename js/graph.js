// graph.js — 道路グラフ(data/graph/<city>.json)の読み込みと隣接リスト構築
let cached = null;

export function haversine(lat1, lon1, lat2, lon2) {
  const R = 6371008.8;
  const rad = (d) => (d * Math.PI) / 180;
  const dLat = rad(lat2 - lat1);
  const dLon = rad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** p1→p2 の進行方位(度, 0-360) */
export function bearing([lat1, lon1], [lat2, lon2]) {
  const rad = (d) => (d * Math.PI) / 180;
  const y = Math.sin(rad(lon2 - lon1)) * Math.cos(rad(lat2));
  const x = Math.cos(rad(lat1)) * Math.sin(rad(lat2)) - Math.sin(rad(lat1)) * Math.cos(rad(lat2)) * Math.cos(rad(lon2 - lon1));
  const deg = (Math.atan2(y, x) * 180) / Math.PI;
  return ((deg % 360) + 360) % 360;
}

/** 2つの方位(度)の差を 0〜180 の絶対角度で返す */
export function bearingDiff(a, b) {
  const d = Math.abs(a - b) % 360;
  return d > 180 ? 360 - d : d;
}

export async function loadGraph(city) {
  if (cached && cached.city === city) return cached;
  const res = await fetch(`data/graph/${encodeURIComponent(city)}.json`, { cache: "force-cache" });
  if (!res.ok) throw new Error(`グラフの読み込みに失敗しました: ${city}`);
  const data = await res.json();
  cached = buildAdjacency(data);
  return cached;
}

/** 事前構築済みの都市一覧 data/graph/index.json ([{city, bbox:[s,w,n,e]}]) */
export async function loadCityIndex() {
  try {
    const res = await fetch("data/graph/index.json");
    return res.ok ? await res.json() : [];
  } catch {
    return [];
  }
}

export function buildAdjacency(data) {
  const adj = new Map(); // nodeId -> [{edge, to, forward}]
  for (const id of Object.keys(data.nodes)) adj.set(id, []);
  for (const e of data.edges) {
    // 交差点での曲がり角度を計算するため、edgeの出入り口の方位を進行方向ごとに1度だけ求めておく
    if (e.points.length >= 2) {
      e._bearingOut = bearing(e.points[0], e.points[1]);
      e._bearingIn = bearing(e.points[e.points.length - 2], e.points[e.points.length - 1]);
    }
    // 最寄りedge探索を高速化するための外接矩形 [minLat, minLon, maxLat, maxLon]
    let minLat = Infinity, minLon = Infinity, maxLat = -Infinity, maxLon = -Infinity;
    for (const p of e.points) {
      if (p[0] < minLat) minLat = p[0];
      if (p[0] > maxLat) maxLat = p[0];
      if (p[1] < minLon) minLon = p[1];
      if (p[1] > maxLon) maxLon = p[1];
    }
    e._bbox = [minLat, minLon, maxLat, maxLon];
    if (!adj.has(e.from)) adj.set(e.from, []);
    if (!adj.has(e.to)) adj.set(e.to, []);
    adj.get(e.from).push({ edge: e, to: e.to, forward: true });
    adj.get(e.to).push({ edge: e, to: e.from, forward: false });
  }
  return { city: data.city, area: data.area || null, nodes: data.nodes, edges: data.edges, adj };
}

/**
 * 与えた緯度経度を、最も近い道(edge)の上に射影し、経路探索の起点/終点候補を返す。
 *
 * 「最寄りの交差点ノード」を直接探すと、無関係な行き止まりの突き当りを拾って
 * 「行き止まりに案内される」不具合の原因になる。そのため、まず最寄りedgeを求め、
 * そのedgeの両端点を候補(candidates)として返す。どちらを使うかは経路探索側が
 * 総コストで選ぶ(候補ごとの extraM は、地点からそのノードまでの直線距離)。
 */
export function snapToRoad(graph, lat, lon) {
  const M = 111320; // 緯度1度あたりのメートル
  const cosLat = Math.cos((lat * Math.PI) / 180);

  let bestD = Infinity;
  let bestEdge = null;
  for (const edge of graph.edges) {
    const bb = edge._bbox;
    const dLat = lat < bb[0] ? bb[0] - lat : lat > bb[2] ? lat - bb[2] : 0;
    const dLon = lon < bb[1] ? bb[1] - lon : lon > bb[3] ? lon - bb[3] : 0;
    if (Math.hypot(dLat * M, dLon * cosLat * M) >= bestD) continue;

    const pts = edge.points;
    for (let i = 0; i < pts.length - 1; i++) {
      // 地点を原点とするローカル座標(m)
      const ax = (pts[i][1] - lon) * cosLat * M;
      const ay = (pts[i][0] - lat) * M;
      const dx = (pts[i + 1][1] - lon) * cosLat * M - ax;
      const dy = (pts[i + 1][0] - lat) * M - ay;
      const len2 = dx * dx + dy * dy;
      let t = len2 > 0 ? -(ax * dx + ay * dy) / len2 : 0;
      t = Math.max(0, Math.min(1, t));
      const d = Math.hypot(ax + t * dx, ay + t * dy);
      if (d < bestD) {
        bestD = d;
        bestEdge = edge;
      }
    }
  }
  if (!bestEdge) return null;

  const candidate = (id) => ({ id, extraM: haversine(lat, lon, graph.nodes[id][0], graph.nodes[id][1]) });
  return {
    lat,
    lon,
    edge: bestEdge,
    distanceM: bestD,
    candidates: [candidate(bestEdge.from), candidate(bestEdge.to)],
  };
}

export function bounds(graph) {
  let south = 90, north = -90, west = 180, east = -180;
  for (const id in graph.nodes) {
    const [lat, lon] = graph.nodes[id];
    south = Math.min(south, lat);
    north = Math.max(north, lat);
    west = Math.min(west, lon);
    east = Math.max(east, lon);
  }
  return { south, north, west, east };
}
