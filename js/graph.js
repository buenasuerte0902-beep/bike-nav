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

/** 与えた緯度経度に最も近いグラフのノードIDを返す */
export function nearestNode(graph, lat, lon) {
  let best = null;
  let bestD = Infinity;
  for (const id in graph.nodes) {
    const [nlat, nlon] = graph.nodes[id];
    const d = haversine(lat, lon, nlat, nlon);
    if (d < bestD) {
      bestD = d;
      best = id;
    }
  }
  return { id: best, distanceM: bestD };
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
