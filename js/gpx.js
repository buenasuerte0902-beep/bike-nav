// gpx.js — GPXファイル(Strava/Ride with GPS等からエクスポートした自分のルート)の
// 読み込みと、そのルートに沿ったナビゲーション補助(残り距離・逸脱検知)。
import { haversine } from "./graph.js";

const MAX_POINTS = 2000; // 長時間ライドのGPXは点数が多いので表示・計算負荷対策で間引く

/** GPXファイルの文字列内容から { name, points: [[lat, lon, ele|null], ...] } を取り出す */
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
  const name = (doc.querySelector("trk > name") || doc.querySelector("metadata > name") || doc.querySelector("rte > name"))?.textContent.trim();
  return { name: name || null, points: decimate(points, MAX_POINTS) };
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

/** 獲得標高。GPSの高度ノイズで水増しされないよう、3m以上の上昇だけを数える */
export function elevationGainM(points) {
  const THRESHOLD_M = 3;
  let gain = 0;
  let ref = null;
  for (const p of points) {
    const ele = p[2];
    if (ele == null) continue;
    if (ref == null) ref = ele;
    else if (ele > ref + THRESHOLD_M) {
      gain += ele - ref;
      ref = ele;
    } else if (ele < ref) ref = ele;
  }
  return gain;
}
