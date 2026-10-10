// sw.js — オフライン対応。アプリ本体は precache、地図タイルは runtime cache、
// 道路グラフ(data/graph/)は初回アクセス時に自動キャッシュ（起動のたびに裏で更新確認）。
const VERSION = "bike-nav-v9";
const SHELL = `${VERSION}-shell`;
// タイルと道路グラフは容量が大きいのでバージョンを上げても消さない
const TILES = "bike-nav-tiles";
const GRAPH = "bike-nav-graph";

const APP_SHELL = [
  "./",
  "index.html",
  "manifest.webmanifest",
  "css/app.css",
  "js/graph.js",
  "js/osmgraph.js",
  "js/route.js",
  "js/nav.js",
  "js/geocode.js",
  "js/map.js",
  "js/app.js",
  "js/gpx.js",
  "js/version.js",
  "vendor/leaflet/leaflet.js",
  "vendor/leaflet/leaflet.css",
  "vendor/leaflet/images/marker-icon.png",
  "vendor/leaflet/images/marker-icon-2x.png",
  "vendor/leaflet/images/marker-shadow.png",
  "icons/icon-192.png",
  "icons/icon-512.png",
  "icons/maskable-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(SHELL).then((c) => c.addAll(APP_SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  const keep = new Set([SHELL, TILES, GRAPH]);
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => !keep.has(k)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const { request } = e;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  // 地図タイル・標高タイル: cache-first + 上限
  if (/tile\.openstreetmap\.org|tile\..*\/\d+\/\d+\/\d+\.png|cyberjapandata\.gsi\.go\.jp\/xyz\/dem_png\//.test(url.href)) {
    e.respondWith(
      caches.open(TILES).then(async (cache) => {
        const hit = await cache.match(request);
        if (hit) return hit;
        try {
          const res = await fetch(request);
          if (res.ok) {
            cache.put(request, res.clone());
            trim(cache, 400);
          }
          return res;
        } catch {
          return Response.error();
        }
      })
    );
    return;
  }

  // 道路グラフ: キャッシュを即返し、裏で更新(次回起動から新データ)。無ければ取得して保存
  if (url.origin === location.origin && /\/data\/(graph|signals)\//.test(url.pathname)) {
    e.respondWith(
      (async () => {
        const cache = await caches.open(GRAPH);
        const hit = await cache.match(request);
        const refresh = fetch(request)
          .then((res) => {
            if (res.ok) cache.put(request, res.clone());
            return res;
          })
          .catch(() => null);
        if (hit) {
          e.waitUntil(refresh);
          return hit;
        }
        return (await refresh) || Response.error();
      })()
    );
    return;
  }

  // 外部API（Nominatim等）: network-first、失敗時のみキャッシュ
  if (url.origin !== location.origin) {
    e.respondWith(fetch(request).catch(() => caches.match(request).then((r) => r || Response.error())));
    return;
  }

  // アプリ本体: cache-first、更新はバックグラウンド。画面遷移は index.html に落とす
  e.respondWith(
    (async () => {
      const cache = await caches.open(SHELL);
      const hit =
        (await cache.match(request, { ignoreSearch: true })) ||
        (request.mode === "navigate" ? await cache.match("index.html") : undefined);
      const net = fetch(request)
        .then((res) => {
          if (res.ok) cache.put(request, res.clone());
          return res;
        })
        .catch(() => hit || Response.error());
      if (hit) {
        e.waitUntil(net);
        return hit;
      }
      return net;
    })()
  );
});

async function trim(cache, max) {
  const keys = await cache.keys();
  if (keys.length > max) for (let i = 0; i < keys.length - max; i++) cache.delete(keys[i]);
}
