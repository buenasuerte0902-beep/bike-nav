// route.js — A*探索。「最短(fastest)」と「走りやすさ優先(comfort)」の2モード。
import { bearingDiff, haversine } from "./graph.js";

export const GRADE_SCORE = { S: 5, A: 4, B: 3, C: 2, D: 1 };
const PENALTY = { S: 1, A: 1.3, B: 1.7, C: 2.3, D: 3.2 };
const AXES = ["bikeLane", "shoulderWidth", "traffic", "signals", "slope"];
export const MODE_LABEL = { comfort: "走りやすさ優先", fastest: "最短" };

// 所要時間の目安: 平坦は17km/h、上りは勾配1%ごとに1.4km/h落とし(下限6)、下りは1%ごとに0.8km/h上げる(上限26)
const BASE_SPEED_KMH = 17;
export function speedKmh(dirSlopePct) {
  if (dirSlopePct >= 0) return Math.max(6, BASE_SPEED_KMH - 1.4 * dirSlopePct);
  return Math.min(26, BASE_SPEED_KMH - 0.8 * dirSlopePct);
}

const TURN_MIN_DEG = 35; // これ以上曲がる交差点だけを案内対象にする

// 上り坂だけ追加コストを乗せる(下りは速く走れるので基本ペナルティ以上には課さない)。
// 勾配%が大きいほど比例して重くする、15%超はそれ以上増やさない(上限)。
const UPHILL_COST_PER_PCT = 0.12; // 1%につき距離コストのこの割合を加算
const UPHILL_PCT_CAP = 15;

// 交差点での曲がり角度に応じた追加コスト(距離換算のメートル数)。
// これが無いと「走りやすさ優先」が細切れの良路面を繋いでジグザグになりやすい。
function turnPenaltyM(angleDeg) {
  if (angleDeg < 25) return 0;      // ほぼ直進
  if (angleDeg < 60) return 15;
  if (angleDeg < 120) return 45;
  return 100;                        // 鋭角・ほぼ逆走
}

function directionalSlopePct(edge, forward) {
  if (edge.slopePct == null) return 0;
  return forward ? edge.slopePct : -edge.slopePct;
}

function edgeCost(edge, mode, forward) {
  let cost;
  if (mode === "fastest") {
    cost = edge.lengthM;
  } else {
    const penalty = PENALTY[edge.total] ?? 2;
    cost = edge.lengthM * penalty;
  }
  const uphillPct = Math.max(0, Math.min(UPHILL_PCT_CAP, directionalSlopePct(edge, forward)));
  cost += edge.lengthM * uphillPct * UPHILL_COST_PER_PCT;
  return cost;
}

/** prevEdgeの到着方位 → nextEdgeの出発方位、の曲がり角度(0-180度) */
function turnAngle(prevEdge, prevForward, nextEdge, nextForward) {
  if (!prevEdge) return 0; // 出発直後は曲がり角として数えない
  const arrive = prevForward ? prevEdge._bearingIn : (prevEdge._bearingOut + 180) % 360;
  const depart = nextForward ? nextEdge._bearingOut : (nextEdge._bearingIn + 180) % 360;
  return bearingDiff(arrive, depart);
}

/** 二分ヒープによる単純な優先度付きキュー */
class MinHeap {
  constructor() {
    this.items = [];
  }
  get size() {
    return this.items.length;
  }
  push(priority, value) {
    this.items.push([priority, value]);
    let i = this.items.length - 1;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (this.items[p][0] <= this.items[i][0]) break;
      [this.items[p], this.items[i]] = [this.items[i], this.items[p]];
      i = p;
    }
  }
  pop() {
    const top = this.items[0];
    const last = this.items.pop();
    if (this.items.length) {
      this.items[0] = last;
      let i = 0;
      for (;;) {
        const l = i * 2 + 1;
        const r = i * 2 + 2;
        let smallest = i;
        if (l < this.items.length && this.items[l][0] < this.items[smallest][0]) smallest = l;
        if (r < this.items.length && this.items[r][0] < this.items[smallest][0]) smallest = r;
        if (smallest === i) break;
        [this.items[smallest], this.items[i]] = [this.items[i], this.items[smallest]];
        i = smallest;
      }
    }
    return top;
  }
}

/**
 * 道路網上の経路探索。from/to は snapToRoad() の結果(両端ノードの候補を持つ)。
 * 起点・終点とも両端どちらのノードからでも入れるようにして、総コスト最小の組を選ぶ。
 * 見つからなければ null。
 */
export function findRoute(graph, from, to, mode = "comfort") {
  if (from.edge === to.edge) return directRoute(from, to, mode);

  const nodes = graph.nodes;
  const h = (id) => haversine(nodes[id][0], nodes[id][1], to.lat, to.lon);
  const goalExtra = new Map(to.candidates.map((c) => [c.id, c.extraM]));

  const gScore = new Map();
  const prevEdge = new Map();
  const prevForward = new Map();
  const prevNode = new Map();
  const closed = new Set();
  const open = new MinHeap();
  for (const c of from.candidates) {
    gScore.set(c.id, c.extraM);
    open.push(c.extraM + h(c.id), c.id);
  }

  let best = Infinity;
  let bestGoal = null;
  while (open.size) {
    const [f, curId] = open.pop();
    if (f >= best) break;
    if (closed.has(curId)) continue;
    closed.add(curId);

    if (goalExtra.has(curId)) {
      const total = gScore.get(curId) + goalExtra.get(curId);
      if (total < best) {
        best = total;
        bestGoal = curId;
      }
    }

    const incomingEdge = prevEdge.get(curId) ?? null;
    const incomingForward = prevForward.get(curId);
    for (const { edge, to: nextId, forward } of graph.adj.get(curId) || []) {
      if (closed.has(nextId)) continue;
      const angle = turnAngle(incomingEdge, incomingForward, edge, forward);
      const cost = gScore.get(curId) + edgeCost(edge, mode, forward) + turnPenaltyM(angle);
      if (cost < (gScore.get(nextId) ?? Infinity)) {
        gScore.set(nextId, cost);
        prevEdge.set(nextId, edge);
        prevForward.set(nextId, forward);
        prevNode.set(nextId, curId);
        open.push(cost + h(nextId), nextId);
      }
    }
  }
  if (bestGoal == null) return null;

  const steps = [];
  let cur = bestGoal;
  while (prevEdge.has(cur)) {
    steps.push({ edge: prevEdge.get(cur), forward: prevForward.get(cur) });
    cur = prevNode.get(cur);
  }
  steps.reverse();
  return summarize(graph, steps, cur, bestGoal, from, to, mode);
}

/** 出発地と目的地が同じ道の上にある場合は、遠回りせず直接つなぐ */
function directRoute(from, to, mode) {
  const edge = from.edge;
  const lengthM = haversine(from.lat, from.lon, to.lat, to.lon);
  const polyline = [[from.lat, from.lon], [to.lat, to.lon]];
  const totalAvg = GRADE_SCORE[edge.total] ?? null;
  const axisAvg = {};
  for (const axis of AXES) axisAvg[axis] = GRADE_SCORE[edge.grades?.[axis]] ?? null;
  return {
    mode,
    steps: [],
    edges: [edge],
    edgeKey: `direct:${edge.id}`,
    lengthM,
    durationS: lengthM / (BASE_SPEED_KMH / 3.6),
    ascentM: 0,
    descentM: 0,
    axisAvg,
    totalAvg,
    totalGrade: scoreToGrade(totalAvg),
    polyline,
    runs: [{ points: polyline, grade: edge.total ?? null }],
    turns: [],
  };
}

function summarize(graph, steps, startId, endId, from, to, mode) {
  const edges = steps.map((s) => s.edge);
  const orient = (s) => (s.forward ? s.edge.points : [...s.edge.points].reverse());
  const sameXY = (a, b) => a[0] === b[0] && a[1] === b[1];

  // 経路全体の点列。出発地→(接続線)→道路→(接続線)→目的地。runs は地図描画用のランク別区間。
  const startPt = graph.nodes[startId];
  const endPt = graph.nodes[endId];
  const polyline = [[from.lat, from.lon]];
  const runs = [];
  const stepEndIdx = [];
  const pushPts = (pts) => {
    for (const p of pts) {
      const q = [p[0], p[1]];
      if (!sameXY(polyline[polyline.length - 1], q)) polyline.push(q);
    }
  };

  runs.push({ points: [[from.lat, from.lon], [startPt[0], startPt[1]]], grade: null, connector: true });
  let run = null;
  for (const s of steps) {
    const pts = orient(s);
    pushPts(pts);
    stepEndIdx.push(polyline.length - 1);
    const grade = s.edge.total ?? null;
    if (!run || run.grade !== grade) {
      run = { points: [], grade };
      runs.push(run);
    }
    for (const p of pts) {
      const q = [p[0], p[1]];
      if (!run.points.length || !sameXY(run.points[run.points.length - 1], q)) run.points.push(q);
    }
  }
  if (!steps.length) pushPts([startPt]);
  runs.push({ points: [[endPt[0], endPt[1]], [to.lat, to.lon]], grade: null, connector: true });
  if (!sameXY(polyline[polyline.length - 1], [to.lat, to.lon])) polyline.push([to.lat, to.lon]);

  // 曲がり角(交差点ごとの符号付き角度。正=右)
  const turns = [];
  for (let i = 0; i < steps.length - 1; i++) {
    const a = steps[i];
    const b = steps[i + 1];
    const arrive = a.forward ? a.edge._bearingIn : (a.edge._bearingOut + 180) % 360;
    const depart = b.forward ? b.edge._bearingOut : (b.edge._bearingIn + 180) % 360;
    if (arrive == null || depart == null) continue;
    const signed = ((depart - arrive + 540) % 360) - 180;
    if (Math.abs(signed) >= TURN_MIN_DEG) turns.push({ idx: stepEndIdx[i], signed });
  }

  // 距離・時間・標高(接続線は平坦扱い)
  const startConn = haversine(from.lat, from.lon, startPt[0], startPt[1]);
  const endConn = haversine(endPt[0], endPt[1], to.lat, to.lon);
  let lengthM = startConn + endConn;
  let durationS = (startConn + endConn) / (BASE_SPEED_KMH / 3.6);
  let ascentM = 0;
  let descentM = 0;
  for (const s of steps) {
    const pct = directionalSlopePct(s.edge, s.forward);
    lengthM += s.edge.lengthM;
    durationS += s.edge.lengthM / (speedKmh(pct) / 3.6);
    const rise = (s.edge.lengthM * pct) / 100;
    if (rise > 0) ascentM += rise;
    else descentM -= rise;
  }

  // ランクの平均は距離で重み付け(短い区間が長い区間と同じ重みにならないように)
  const weightedAvg = (pick) => {
    let sum = 0;
    let w = 0;
    for (const e of edges) {
      const sc = GRADE_SCORE[pick(e)];
      if (sc) {
        sum += sc * e.lengthM;
        w += e.lengthM;
      }
    }
    return w ? sum / w : null;
  };
  const axisAvg = {};
  for (const axis of AXES) axisAvg[axis] = weightedAvg((e) => e.grades?.[axis]);
  const totalAvg = weightedAvg((e) => e.total);

  return {
    mode,
    steps,
    edges,
    edgeKey: edges.map((e) => e.id).join("|"),
    lengthM,
    durationS,
    ascentM,
    descentM,
    axisAvg,
    totalAvg,
    totalGrade: scoreToGrade(totalAvg),
    polyline,
    runs,
    turns,
  };
}

export function scoreToGrade(avg) {
  if (avg == null) return null;
  if (avg >= 4.5) return "S";
  if (avg >= 3.5) return "A";
  if (avg >= 2.5) return "B";
  if (avg >= 1.5) return "C";
  return "D";
}
