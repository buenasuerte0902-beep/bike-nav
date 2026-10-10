// osmgraph.js — 事前構築したグラフが無い地域用。出発地〜目的地の周辺だけ OSM(Overpass) から道路を取り、
// 国土地理院の標高タイルで勾配を付けて、tools/build_graph.py と同じ形式・同じランク基準のグラフをその場で作る。
// (路肩幅・路面の滑らかさ・交通量の実測は事前処理した都市にしか無いので、ここでは null / タグからの近似)
import { haversine } from "./graph.js";

const OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"];
const HIGHWAY_ALLOW = "primary|secondary|tertiary|unclassified|residential|living_street|cycleway|path|track|service";
const SEGMENT_TARGET_M = 500;
const SEGMENT_MAX_M = 650;
const SIGNAL_NEAR_M = 25;
const SIGNAL_ROUTE_M = 30; // ナビ表示用: ルートからこの距離内の信号を出す
const DEM_URL = (z, x, y) => `https://cyberjapandata.gsi.go.jp/xyz/dem_png/${z}/${x}/${y}.png`;
const DEM_MAX_TILES = 90;

export const MAX_STRAIGHT_KM = 30;

/** 出発地→目的地の線分を囲む帯(四角形)。幅は距離に応じて広げ、遠回りの候補も入るようにする */
export function corridor(a, b) {
  const d = haversine(a.lat, a.lon, b.lat, b.lon);
  const margin = Math.min(4000, Math.max(1500, d * 0.25));
  const lat0 = (a.lat + b.lat) / 2;
  const mLat = 111320;
  const mLon = 111320 * Math.cos((lat0 * Math.PI) / 180);
  const ax = (a.lon - b.lon) * mLon, ay = (a.lat - b.lat) * mLat;
  const len = Math.hypot(ax, ay) || 1;
  const ux = -ax / len, uy = -ay / len; // a→b 方向
  const nx = -uy, ny = ux;              // 直交方向
  const p = (pt, s, t) => [pt.lat + (uy * s + ny * t) / mLat, pt.lon + (ux * s + nx * t) / mLon];
  return [p(a, -margin, -margin), p(b, margin, -margin), p(b, margin, margin), p(a, -margin, margin)];
}

export function pointInPoly(lat, lon, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [yi, xi] = poly[i], [yj, xj] = poly[j];
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

async function overpass(query, signal) {
  let last = null;
  for (const url of OVERPASS) {
    try {
      const timeout = AbortSignal.timeout?.(100000);
      const res = await fetch(url, {
        method: "POST",
        body: new URLSearchParams({ data: query }),
        signal: timeout && AbortSignal.any ? AbortSignal.any([signal, timeout].filter(Boolean)) : signal,
      });
      if (res.ok) return await res.json();
      if (res.status === 429 || res.status === 504) last = "道路データのサーバーが混雑しています。少し待ってから再度お試しください";
    } catch (err) {
      if (signal?.aborted) throw err; // ユーザー操作による中断。タイムアウトは次のサーバーへ
    }
  }
  throw new Error(last || "道路データを取得できませんでした。通信状況を確認してください");
}

/**
 * ルート(points: [[lat,lon],...])沿いの信号機をOSMから取得する。
 * 戻り値: [{lat, lon, name|null}]。名前は name タグ(無ければ name:ja)。
 */
export async function fetchRouteSignals(points, signal) {
  if (!points.length) return [];
  let s = Infinity, w = Infinity, n = -Infinity, e = -Infinity;
  for (const [la, lo] of points) {
    s = Math.min(s, la); n = Math.max(n, la);
    w = Math.min(w, lo); e = Math.max(e, lo);
  }
  const pad = 0.0004;
  s -= pad; w -= pad; n += pad; e += pad;
  // 全国分の事前取得データ(data/signals/)があればそれを使い、無ければ Overpass に問い合わせる
  let cands = await localSignals(s, w, n, e, signal);
  if (!cands) {
    const q = `[out:json][timeout:60];
node["highway"="traffic_signals"](${s},${w},${n},${e});
out body qt;`;
    const osm = await overpass(q, signal);
    cands = osm.elements
      .filter((el) => el.type === "node")
      .map((el) => ({ lat: el.lat, lon: el.lon, name: el.tags?.name || el.tags?.["name:ja"] || null }));
  }
  // 間引いたルート点との距離で絞る(ルートから離れた信号は除外)
  const step = Math.max(1, Math.floor(points.length / 4000));
  const sample = points.filter((_, i) => i % step === 0 || i === points.length - 1);
  const cell = 0.002;
  const grid = new Map();
  for (const p of sample) {
    const k = `${Math.round(p[0] / cell)},${Math.round(p[1] / cell)}`;
    if (!grid.has(k)) grid.set(k, []);
    grid.get(k).push(p);
  }
  return cands.filter((c) => {
    const cx = Math.round(c.lat / cell), cy = Math.round(c.lon / cell);
    for (let a = -1; a <= 1; a++)
      for (let b = -1; b <= 1; b++)
        if ((grid.get(`${cx + a},${cy + b}`) || []).some((p) => haversine(c.lat, c.lon, p[0], p[1]) <= SIGNAL_ROUTE_M)) return true;
    return false;
  });
}

// 全国の信号機データ: data/signals/index.json (tools/fetch_signals.py が生成)。1度×1度のタイル単位で読む。
let signalIndex; // Promise<Set<string>|null>
async function localSignals(s, w, n, e, signal) {
  signalIndex ??= fetch("data/signals/index.json")
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => (j ? new Set(j.tiles) : null))
    .catch(() => null);
  const have = await signalIndex;
  if (!have) return null;
  const out = [];
  for (let la = Math.floor(s); la <= Math.floor(n); la++) {
    for (let lo = Math.floor(w); lo <= Math.floor(e); lo++) {
      if (!have.has(`${la}_${lo}`)) continue; // 信号の無いタイルは省略されている
      const res = await fetch(`data/signals/${la}_${lo}.json`, { signal });
      if (!res.ok) return null; // 取得できなければ Overpass へ
      for (const [lat, lon, name] of await res.json())
        if (lat >= s && lat <= n && lon >= w && lon <= e) out.push({ lat, lon, name: name || null });
    }
  }
  return out;
}

// ---------- 標高(国土地理院 標高タイル dem_png) ----------
function lonToX(lon, z) {
  return ((lon + 180) / 360) * 2 ** z;
}
function latToY(lat, z) {
  const r = (lat * Math.PI) / 180;
  return ((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 2 ** z;
}

async function loadDemTile(z, x, y, signal) {
  try {
    const res = await fetch(DEM_URL(z, x, y), { signal });
    if (!res.ok) return null; // 海など、タイルが無い場所
    const bmp = await createImageBitmap(await res.blob());
    const c = new OffscreenCanvas(256, 256);
    const ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(bmp, 0, 0);
    return ctx.getImageData(0, 0, 256, 256).data;
  } catch (err) {
    if (err.name === "AbortError") throw err;
    return null;
  }
}

/** points: [[lat,lon],...] → 標高(m)の配列(取れない点は null) */
async function elevations(points, signal) {
  let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
  for (const [la, lo] of points) {
    minLat = Math.min(minLat, la); maxLat = Math.max(maxLat, la);
    minLon = Math.min(minLon, lo); maxLon = Math.max(maxLon, lo);
  }
  let z = 14; // dem_png の最大ズーム(約10mメッシュ)
  const count = (z) =>
    (Math.floor(lonToX(maxLon, z)) - Math.floor(lonToX(minLon, z)) + 1) *
    (Math.floor(latToY(minLat, z)) - Math.floor(latToY(maxLat, z)) + 1);
  while (z > 10 && count(z) > DEM_MAX_TILES) z--;

  const tiles = new Map();
  const keys = new Set(points.map(([la, lo]) => `${Math.floor(lonToX(lo, z))}/${Math.floor(latToY(la, z))}`));
  const list = [...keys];
  for (let i = 0; i < list.length; i += 8) {
    await Promise.all(
      list.slice(i, i + 8).map(async (k) => {
        const [x, y] = k.split("/").map(Number);
        tiles.set(k, await loadDemTile(z, x, y, signal));
      })
    );
  }
  return points.map(([la, lo]) => {
    const fx = lonToX(lo, z), fy = latToY(la, z);
    const tx = Math.floor(fx), ty = Math.floor(fy);
    const data = tiles.get(`${tx}/${ty}`);
    if (!data) return null;
    const px = Math.min(255, Math.floor((fx - tx) * 256));
    const py = Math.min(255, Math.floor((fy - ty) * 256));
    const i = (py * 256 + px) * 4;
    const v = data[i] * 65536 + data[i + 1] * 256 + data[i + 2];
    if (v === 8388608) return null; // 無効値
    return (v < 8388608 ? v : v - 16777216) * 0.01;
  });
}

// ---------- ランク付け(tools/build_graph.py と同じ基準) ----------
const GRADE_SCORE = { S: 5, A: 4, B: 3, C: 2, D: 1 };

function gradeBikeLane(t) {
  const cw = ["cycleway", "cycleway:both", "cycleway:right", "cycleway:left"].map((k) => t[k] || "");
  if (["cycleway", "path", "track"].includes(t.highway)) return "S";
  if (cw.some((v) => v === "track" || v === "opposite_track")) return "S";
  if (cw.some((v) => ["lane", "opposite_lane", "share_busway"].includes(v))) return "B";
  if (cw.some((v) => v === "shared_lane")) return "C";
  if (["residential", "living_street", "service"].includes(t.highway)) return "C";
  return "D";
}

function gradeTraffic(t) {
  const hw = t.highway || "";
  const lanes = parseInt(String(t.lanes || "").split(";")[0], 10) || null;
  const maxspeed = parseInt(String(t.maxspeed || "").split(";")[0], 10) || null;
  if (["cycleway", "path", "track", "living_street"].includes(hw)) return "S";
  if (hw === "residential" || hw === "service") return "A";
  if (hw === "tertiary" && (lanes || 2) <= 2) return "B";
  if (hw === "secondary" || lanes === 3) return "C";
  if (hw === "primary" || (lanes || 0) >= 4 || (maxspeed || 0) >= 50) return "D";
  return "C";
}

const gradeSignals = (n) => (n === 0 ? "S" : n === 1 ? "B" : n === 2 ? "C" : "D");

function gradeSlope(pct) {
  if (pct == null) return null;
  const a = Math.abs(pct);
  return a < 1.5 ? "S" : a < 3 ? "A" : a < 5 ? "B" : a < 8 ? "C" : "D";
}

function composite(grades) {
  const vals = grades.filter(Boolean).map((g) => GRADE_SCORE[g]);
  if (!vals.length) return null;
  const avg = vals.reduce((s, v) => s + v, 0) / vals.length;
  for (const [g, s] of Object.entries(GRADE_SCORE)) if (avg >= s - 0.5) return g;
  return "D";
}

// ---------- グラフ構築 ----------
const pathLength = (pts) => {
  let s = 0;
  for (let i = 1; i < pts.length; i++) s += haversine(pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]);
  return s;
};

function splitWays(ways) {
  const ref = new Map();
  for (const w of ways) for (const id of w.nodes) ref.set(id, (ref.get(id) || 0) + 1);
  const raw = [];
  for (const w of ways) {
    const ids = w.nodes, pts = w.geometry.map((g) => [g.lat, g.lon]);
    if (ids.length < 2) continue;
    let start = 0;
    for (let i = 1; i < ids.length; i++) {
      const vertex = ref.get(ids[i]) > 1 || i === ids.length - 1;
      if (vertex && ids[i] !== ids[start]) {
        raw.push({ wayId: w.id, tags: w.tags || {}, from: String(ids[start]), to: String(ids[i]), points: pts.slice(start, i + 1) });
        start = i;
      }
    }
  }
  return raw;
}

function subdivide(raw, nodes) {
  const out = [];
  for (const e of raw) {
    const total = pathLength(e.points);
    if (total <= SEGMENT_MAX_M) {
      out.push({ ...e, lengthM: total });
      continue;
    }
    const piece = total / Math.max(2, Math.round(total / SEGMENT_TARGET_M));
    let curId = e.from, cur = [e.points[0]], acc = 0, k = 0;
    for (let i = 1; i < e.points.length; i++) {
      acc += haversine(e.points[i - 1][0], e.points[i - 1][1], e.points[i][0], e.points[i][1]);
      cur.push(e.points[i]);
      if (acc >= piece && i < e.points.length - 1) {
        const syn = `syn:${e.wayId}:${e.from}:${++k}`;
        nodes[syn] = e.points[i];
        out.push({ ...e, from: curId, to: syn, points: cur, lengthM: pathLength(cur) });
        curId = syn; cur = [e.points[i]]; acc = 0;
      }
    }
    if (cur.length > 1) out.push({ ...e, from: curId, to: e.to, points: cur, lengthM: pathLength(cur) });
  }
  return out;
}

function largestComponent(edges) {
  const parent = new Map();
  const find = (x) => {
    while (parent.get(x) !== x) {
      parent.set(x, parent.get(parent.get(x)));
      x = parent.get(x);
    }
    return x;
  };
  for (const e of edges) {
    if (!parent.has(e.from)) parent.set(e.from, e.from);
    if (!parent.has(e.to)) parent.set(e.to, e.to);
  }
  for (const e of edges) {
    const a = find(e.from), b = find(e.to);
    if (a !== b) parent.set(a, b);
  }
  const size = new Map();
  for (const id of parent.keys()) size.set(find(id), (size.get(find(id)) || 0) + 1);
  let root = null, best = -1;
  for (const [r, s] of size) if (s > best) { best = s; root = r; }
  return edges.filter((e) => find(e.from) === root);
}

function signalCounter(signals) {
  const CELL = 0.003;
  const grid = new Map();
  for (const s of signals) {
    const k = `${Math.round(s.lat / CELL)},${Math.round(s.lon / CELL)}`;
    if (!grid.has(k)) grid.set(k, []);
    grid.get(k).push([s.lat, s.lon]);
  }
  return (points) => {
    const cells = new Set();
    for (const [la, lo] of points)
      for (let a = -1; a <= 1; a++)
        for (let b = -1; b <= 1; b++) cells.add(`${Math.round(la / CELL) + a},${Math.round(lo / CELL) + b}`);
    let n = 0;
    for (const k of cells)
      for (const [sl, so] of grid.get(k) || [])
        if (points.some((p) => haversine(sl, so, p[0], p[1]) <= SIGNAL_NEAR_M)) n++;
    return n;
  };
}

/**
 * poly([[lat,lon],...]) の範囲の道路グラフを作る。onProgress(文言) で進み具合を通知。
 * 戻り値は data/graph/<city>.json と同じ形({city, nodes, edges}) + area(poly)。
 */
export async function buildAreaGraph(poly, { signal, onProgress = () => {} } = {}) {
  const polyStr = poly.map(([la, lo]) => `${la.toFixed(6)} ${lo.toFixed(6)}`).join(" ");
  onProgress("周辺の道路データを取得中…");
  const q = `[out:json][timeout:90][maxsize:536870912];
way["highway"~"^(${HIGHWAY_ALLOW})$"](poly:"${polyStr}");
out body geom qt;
node["highway"="traffic_signals"](poly:"${polyStr}");
out skel qt;`;
  const osm = await overpass(q, signal);
  const ways = osm.elements.filter((e) => e.type === "way" && e.geometry);
  const signals = osm.elements.filter((e) => e.type === "node");
  if (!ways.length) throw new Error("この周辺には道路データがありません");

  onProgress("道路網を組み立て中…");
  const nodes = {};
  for (const w of ways) w.nodes.forEach((id, i) => (nodes[id] = [w.geometry[i].lat, w.geometry[i].lon]));
  let edges = largestComponent(subdivide(splitWays(ways), nodes));

  onProgress("標高を取得中…");
  const used = [...new Set(edges.flatMap((e) => [e.from, e.to]))];
  const zs = await elevations(used.map((id) => nodes[id]), signal);
  const elev = new Map(used.map((id, i) => [id, zs[i]]));

  const countSignals = signalCounter(signals);
  const outNodes = {};
  for (const id of used) outNodes[id] = [+nodes[id][0].toFixed(6), +nodes[id][1].toFixed(6)];
  edges = edges.map((e, i) => {
    const z1 = elev.get(e.from), z2 = elev.get(e.to);
    const slopePct = z1 != null && z2 != null && e.lengthM > 1 ? ((z2 - z1) / e.lengthM) * 100 : null;
    const grades = {
      bikeLane: gradeBikeLane(e.tags),
      traffic: gradeTraffic(e.tags),
      signals: gradeSignals(countSignals(e.points)),
      slope: gradeSlope(slopePct),
      shoulderWidth: null,
      smoothness: null,
    };
    const tags = {};
    for (const k of ["highway", "cycleway", "surface", "maxspeed", "lanes"]) if (k in e.tags) tags[k] = e.tags[k];
    return {
      id: `o${i}`,
      from: e.from,
      to: e.to,
      points: e.points.map(([la, lo]) => [+la.toFixed(6), +lo.toFixed(6)]),
      lengthM: Math.round(e.lengthM * 10) / 10,
      wayId: e.wayId,
      tags,
      grades,
      total: composite([grades.bikeLane, grades.traffic, grades.signals, grades.slope]),
      slopePct: slopePct == null ? null : Math.round(slopePct * 100) / 100,
    };
  });
  return { city: null, area: poly, nodes: outNodes, edges };
}
