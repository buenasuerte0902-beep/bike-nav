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

function buildAdjacency(data) {
  const adj = new Map(); // nodeId -> [{edge, to, forward}]
  for (const id of Object.keys(data.nodes)) adj.set(id, []);
  for (const e of data.edges) {
    // 交差点での曲がり角度を計算するため、edgeの出入り口の方位を進行方向ごとに1度だけ求めておく
    if (e.points.length >= 2) {
      e._bearingOut = bearing(e.points[0], e.points[1]);
      e._bearingIn = bearing(e.points[e.points.length - 2], e.points[e.points.length - 1]);
    }
    if (!adj.has(e.from)) adj.set(e.from, []);
    if (!adj.has(e.to)) adj.set(e.to, []);
    adj.get(e.from).push({ edge: e, to: e.to, forward: true });
    adj.get(e.to).push({ edge: e, to: e.from, forward: false });
  }
  return { city: data.city, nodes: data.nodes, edges: data.edges, adj };
}

/**
 * 与えた緯度経度に最も近い、道路網(edge)上の地点を求め、経路探索の起点/終点として
 * 使えるノードIDを返す。
 *
 * 単純に「最も近いノード(交差点)」を探すと、本当に近い通り沿いの地点よりも、
 * たまたま近くにある無関係な行き止まり(私道の突き当り等)のノードを拾ってしまい、
 * 「行き止まりに案内されて途中で切れる」不具合の原因になる。
 * そのため、まず地図上で最も近いedge(道の線そのもの)を探し、そのedgeの両端点の
 * うち近い方のノードを採用する。
 */
export function nearestNode(graph, lat, lon) {
  const toXY = (la, lo) => {
    const R = 6371008.8;
    const rad = Math.PI / 180;
    return [lo * rad * Math.cos(lat * rad) * R, la * rad * R];
  };
  const [px, py] = toXY(lat, lon);

  let bestD = Infinity;
  let bestEdge = null;
  for (const edge of graph.edges) {
    const pts = edge.points;
    for (let i = 0; i < pts.length - 1; i++) {
      const [ax, ay] = toXY(pts[i][0], pts[i][1]);
      const [bx, by] = toXY(pts[i + 1][0], pts[i + 1][1]);
      const dx = bx - ax;
      const dy = by - ay;
      const len2 = dx * dx + dy * dy;
      let t = len2 > 0 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
      t = Math.max(0, Math.min(1, t));
      const d = Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
      if (d < bestD) {
        bestD = d;
        bestEdge = edge;
      }
    }
  }
  if (!bestEdge) return { id: null, distanceM: Infinity };

  const distFrom = haversine(lat, lon, ...graph.nodes[bestEdge.from]);
  const distTo = haversine(lat, lon, ...graph.nodes[bestEdge.to]);
  return distFrom <= distTo
    ? { id: bestEdge.from, distanceM: distFrom }
    : { id: bestEdge.to, distanceM: distTo };
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
