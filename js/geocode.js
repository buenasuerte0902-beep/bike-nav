// geocode.js — Nominatim(OSM)によるジオコーディング。利用規約上、入力中の自動検索(オートコンプリート)は
// 禁止のため、ユーザーが検索を確定したとき(Enter/検索ボタン)だけ呼ぶこと。
const ENDPOINT = "https://nominatim.openstreetmap.org/search";

export async function searchPlace(query, { viewbox, signal } = {}) {
  const params = new URLSearchParams({
    q: query,
    format: "jsonv2",
    limit: "6",
    countrycodes: "jp",
    "accept-language": "ja",
  });
  if (viewbox) {
    params.set("viewbox", viewbox.join(","));
    params.set("bounded", "0");
  }
  const res = await fetch(`${ENDPOINT}?${params}`, { signal });
  if (!res.ok) throw new Error("検索に失敗しました");
  const list = await res.json();
  return list.map((it) => {
    const parts = it.display_name.split(", ").filter((p) => p !== "日本" && !/^\d{3}-\d{4}$/.test(p));
    const name = it.name || parts[0];
    return {
      name,
      sub: parts.filter((p) => p !== name).slice(0, 4).join(" "),
      lat: parseFloat(it.lat),
      lon: parseFloat(it.lon),
    };
  });
}
