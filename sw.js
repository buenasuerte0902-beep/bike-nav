// sw.js — オフライン対応。アプリ本体は precache、地図タイルは runtime cache、
// 道路グラフ(data/graph/)は初回アクセス時に自動キャッシュ（タイルはオフライン非対応）。
const VERSION = "bike-nav-v5";
const SHELL = `${VERSION}-shell`;
const TILES = `${VERSION}-tiles`;
const GRAPH = `${VERSION}-graph`;

const APP_SHELL = [
  "index.html",
  "manifest.webmanifest",
  "css/app.css",
  "js/graph.js",
  "js/route.js",
  "js/geocode.js",
  "js/map.js",
  "js/app.js",
  "js/contribute.js",
  "js/gpx.js",
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
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const { request } = e;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  // 地図タイル: cache-first + 上限
  if (/tile\.openstreetmap\.org|tile\..*\/\d+\/\d+\/\d+\.png/.test(url.href)) {
    e.respondWith(
      caches.open(TILES).then(async (cache) => {
        const hit = await cache.match(request);
        if (hit) return hit;
        try {
          const res = await fetch(request);
          cache.put(request, res.clone());
          trim(cache, 400);
          return res;
        } catch {
          return hit || Response.error();
        }
      })
    );
    return;
  }

  // 道路グラフ: 一度取得したら自動でオフラインキャッシュに保存
  if (url.origin === location.origin && url.pathname.includes("/data/graph/")) {
    e.respondWith(
      (async () => {
        const hit = await caches.match(request);
        if (hit) return hit;
        try {
          const res = await fetch(request);
          if (res.ok) {
            const cache = await caches.open(GRAPH);
            cache.put(request, res.clone());
          }
          return res;
        } catch {
          return hit || Response.error();
        }
      })()
    );
    return;
  }

  // 外部API（Nominatim等）: network-first、失敗時のみキャッシュ
  if (url.origin !== location.origin) {
    e.respondWith(fetch(request).catch(() => caches.match(request)));
    return;
  }

  // アプリ本体: cache-first、更新はバックグラウンド
  e.respondWith(
    caches.match(request).then((hit) => {
      const net = fetch(request)
        .then((res) => {
          caches.open(SHELL).then((c) => c.put(request, res.clone()));
          return res;
        })
        .catch(() => hit);
      return hit || net;
    })
  );
});

async function trim(cache, max) {
  const keys = await cache.keys();
  if (keys.length > max) for (let i = 0; i < keys.length - max; i++) cache.delete(keys[i]);
}
