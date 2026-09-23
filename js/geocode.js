// geocode.js — Nominatim(OSM)によるジオコーディング。検索ボタン押下時のみ呼ぶ(自動連打しない)。
const ENDPOINT = "https://nominatim.openstreetmap.org/search";

export async function searchPlace(query, { viewbox } = {}) {
  const params = new URLSearchParams({
    q: query,
    format: "jsonv2",
    limit: "5",
    "accept-language": "ja",
  });
  if (viewbox) {
    params.set("viewbox", viewbox.join(","));
    params.set("bounded", "0");
  }
  const res = await fetch(`${ENDPOINT}?${params}`);
  if (!res.ok) throw new Error("検索に失敗しました");
  const list = await res.json();
  return list.map((it) => ({
    label: it.display_name,
    lat: parseFloat(it.lat),
    lon: parseFloat(it.lon),
  }));
}
