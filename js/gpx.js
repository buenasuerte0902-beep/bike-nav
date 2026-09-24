// gpx.js — GPXファイル(Strava/Ride with GPS等からエクスポートした自分のルート)の
// 読み込みと、そのルートに沿ったナビゲーション補助(残り距離・逸脱検知)。
import { haversine } from "./graph.js";

const MAX_POINTS = 2000; // 長時間ライドのGPXは点数が多いので表示・計算負荷対策で間引く

/** GPXファイルの文字列内容から [[lat, lon, ele|null], ...] の点列を取り出す */
export function parseGpx(text) {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  if (doc.querySelector("parsererror")) throw new Error("GPXの解析に失敗しました");

  const trkpts = [...doc.querySelectorAll("trkpt")];
  const src = trkpts.length ? trkpts : [...doc.querySelectorAll("rtept")];
  if (!src.length) throw new Error("GPXにルートの座標が見つかりません");

  const points = src
    .map((el) => {
      const lat = parseFloat(el.getAttribute("lat"));
      const lon = parseFloat(el.getAttribute("lon"));
      const eleEl = el.querySelector("ele");
      const ele = eleEl ? parseFloat(eleEl.textContent) : null;
      return [lat, lon, Number.isFinite(ele) ? ele : null];
    })
    .filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]));

  if (points.length < 2) throw new Error("GPXの座標点が足りません");
  return decimate(points, MAX_POINTS);
}

function decimate(points, maxPoints) {
  if (points.length <= maxPoints) return points;
  const step = points.length / maxPoints;
  const out = [];
  for (let i = 0; i < maxPoints; i++) out.push(points[Math.floor(i * step)]);
  out.push(points[points.length - 1]);
  return out;
}

export function totalDistanceM(points) {
  let d = 0;
  for (let i = 1; i < points.length; i++) {
    d += haversine(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1]);
  }
  return d;
}

export function elevationGainM(points) {
  let gain = 0;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1][2];
    const b = points[i][2];
    if (a != null && b != null && b > a) gain += b - a;
  }
  return gain;
}

/**
 * 現在地(lat, lon)からルート上で最も近い点を探し、そこから終点までの残り距離と、
 * ルートからの逸脱距離を返す(ナビ中の進捗表示・オフルート検知用)。
 */
export function progressAlongRoute(points, lat, lon) {
  let bestIdx = 0;
  let bestD = Infinity;
  for (let i = 0; i < points.length; i++) {
    const d = haversine(lat, lon, points[i][0], points[i][1]);
    if (d < bestD) {
      bestD = d;
      bestIdx = i;
    }
  }
  let remainingM = 0;
  for (let i = bestIdx; i < points.length - 1; i++) {
    remainingM += haversine(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1]);
  }
  return { remainingM, offRouteM: bestD };
}
