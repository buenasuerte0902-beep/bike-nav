// nav.js — ナビゲーション用の計算。ルート上の現在位置(進捗)、曲がり角の文言、距離・時間の表示整形。
import { haversine } from "./graph.js";

const M_PER_DEG = 111320;
const WINDOW_M = 1500; // 直前の位置からこの距離だけ先までを探す(周回ルートで始点と終点を取り違えない)

/** points: [[lat, lon, ...], ...]、turns: route.js の turns([{idx, signed}]) */
export function buildNav(points, turns = []) {
  const cum = [0];
  for (let i = 1; i < points.length; i++) {
    cum.push(cum[i - 1] + haversine(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1]));
  }
  return {
    points,
    cum,
    totalM: cum[cum.length - 1],
    turns: turns.map((t) => ({ ...t, alongM: cum[t.idx] })),
  };
}

/**
 * 現在地をルート上に射影する。hintIdx(前回の位置)付近だけを探すので、
 * 周回ルートで開始直後に終点へ飛んで「到着」と誤判定することがない。
 * 大きく外れたときだけ全体から探し直す(復帰用)。
 */
export function locate(nav, lat, lon, hintIdx = 0) {
  const { points, cum } = nav;
  const lastSeg = points.length - 2;
  const cosLat = Math.cos((lat * Math.PI) / 180);

  const scan = (from, to) => {
    let best = { d: Infinity, along: 0, idx: from };
    for (let i = from; i <= to; i++) {
      const ax = (points[i][1] - lon) * cosLat * M_PER_DEG;
      const ay = (points[i][0] - lat) * M_PER_DEG;
      const dx = (points[i + 1][1] - lon) * cosLat * M_PER_DEG - ax;
      const dy = (points[i + 1][0] - lat) * M_PER_DEG - ay;
      const len2 = dx * dx + dy * dy;
      let t = len2 > 0 ? -(ax * dx + ay * dy) / len2 : 0;
      t = Math.max(0, Math.min(1, t));
      const d = Math.hypot(ax + t * dx, ay + t * dy);
      if (d < best.d) best = { d, along: cum[i] + t * (cum[i + 1] - cum[i]), idx: i };
    }
    return best;
  };

  const lo = Math.max(0, hintIdx - 5);
  let hi = lo;
  while (hi < lastSeg && cum[hi] - cum[lo] < WINDOW_M) hi++;
  let best = scan(lo, Math.min(hi, lastSeg));
  if (best.d > 100) {
    const global = scan(0, lastSeg);
    if (global.d < best.d - 30) best = global;
  }
  return {
    idx: best.idx,
    alongM: best.along,
    remainingM: Math.max(0, nav.totalM - best.along),
    offRouteM: best.d,
  };
}

/** 曲がり角の文言と矢印の回転角(度)。signed: 正=右折 */
export function describeTurn(signed) {
  const a = Math.abs(signed);
  const side = signed > 0 ? "右" : "左";
  const sign = signed > 0 ? 1 : -1;
  if (a < 60) return { label: `斜め${side}方向`, rotate: sign * 45 };
  if (a < 135) return { label: `${side}折`, rotate: sign * 90 };
  return { label: `急な${side}折`, rotate: sign * 135 };
}

export function formatDistance(m) {
  if (m < 1000) return `${Math.max(10, Math.round(m / 10) * 10)} m`;
  return `${(m / 1000).toFixed(1)} km`;
}

export function formatDuration(sec) {
  const min = Math.max(1, Math.round(sec / 60));
  if (min < 60) return `${min}分`;
  const h = Math.floor(min / 60);
  const r = min % 60;
  return r ? `${h}時間${r}分` : `${h}時間`;
}

export function formatClock(date) {
  return `${date.getHours()}:${String(date.getMinutes()).padStart(2, "0")}`;
}
