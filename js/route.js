// route.js — A*探索。「最短(fastest)」と「走りやすさ優先(comfort)」の2モード。
import { bearingDiff, haversine } from "./graph.js";

export const GRADE_SCORE = { S: 5, A: 4, B: 3, C: 2, D: 1 };
const PENALTY = { S: 1, A: 1.3, B: 1.7, C: 2.3, D: 3.2 };
const AXES = ["bikeLane", "traffic", "signals", "slope"];

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

/** graph上でfromId→toIdの経路を探す。見つからなければnull */
export function findRoute(graph, fromId, toId, mode = "comfort") {
  const nodes = graph.nodes;
  const h = (id) => haversine(...nodes[id], ...nodes[toId]);

  const gScore = new Map([[fromId, 0]]);
  const prevEdge = new Map();
  const prevForward = new Map();
  const prevNode = new Map();
  const closed = new Set();

  const open = new MinHeap();
  open.push(h(fromId), fromId);

  while (open.size) {
    const [, curId] = open.pop();
    if (closed.has(curId)) continue;
    if (curId === toId) break;
    closed.add(curId);

    const incomingEdge = prevEdge.get(curId) ?? null;
    const incomingForward = prevForward.get(curId);

    for (const { edge, to, forward } of graph.adj.get(curId) || []) {
      if (closed.has(to)) continue;
      const angle = turnAngle(incomingEdge, incomingForward, edge, forward);
      const cost = gScore.get(curId) + edgeCost(edge, mode, forward) + turnPenaltyM(angle);
      if (cost < (gScore.get(to) ?? Infinity)) {
        gScore.set(to, cost);
        prevEdge.set(to, edge);
        prevForward.set(to, forward);
        prevNode.set(to, curId);
        open.push(cost + h(to), to);
      }
    }
  }

  if (!gScore.has(toId)) return null;

  const edges = [];
  let cur = toId;
  while (cur !== fromId) {
    const edge = prevEdge.get(cur);
    if (!edge) return null;
    edges.push(edge);
    cur = prevNode.get(cur);
  }
  edges.reverse();
  return summarize(edges);
}

function summarize(edges) {
  const lengthM = edges.reduce((s, e) => s + e.lengthM, 0);
  const axisAvg = {};
  for (const axis of AXES) {
    const scores = edges.map((e) => GRADE_SCORE[e.grades?.[axis]]).filter(Boolean);
    axisAvg[axis] = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
  }
  const totalScores = edges.map((e) => GRADE_SCORE[e.total]).filter(Boolean);
  const totalAvg = totalScores.length ? totalScores.reduce((a, b) => a + b, 0) / totalScores.length : null;
  return { edges, lengthM, axisAvg, totalAvg, totalGrade: scoreToGrade(totalAvg) };
}

export function scoreToGrade(avg) {
  if (avg == null) return null;
  if (avg >= 4.5) return "S";
  if (avg >= 3.5) return "A";
  if (avg >= 2.5) return "B";
  if (avg >= 1.5) return "C";
  return "D";
}
