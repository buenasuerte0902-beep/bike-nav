// app.js — 画面の結線。検索・出発地/目的地・候補ルート・ナビ(案内)・GPXインポート。
import { loadGraph, snapToRoad, bounds } from "./graph.js";
import { findRoute, scoreToGrade, MODE_LABEL } from "./route.js";
import { searchPlace } from "./geocode.js";
import { MapView } from "./map.js";
import { parseGpx, totalDistanceM, elevationGainM } from "./gpx.js";
import { buildNav, locate, describeTurn, formatDistance, formatDuration, formatClock } from "./nav.js";

const CITY = "金沢市";
const DEFAULT_CENTER = [36.5613, 136.6562];
const RECENT_KEY = "bikeNavRecent";
const OUT_OF_AREA_M = 1500; // 道路データからこれ以上離れた地点はエリア外とみなす
const OFF_ROUTE_M = 70;
const PANEL_W = 404; // デスクトップ表示の左パネル幅(css/app.css と揃える)
const GPX_SPEED_KMH = 16; // GPXルートの到着予想に使う平均速度
const AXIS_LABEL = { bikeLane: "自転車帯", shoulderWidth: "路肩幅", traffic: "交通量", signals: "信号", slope: "起伏" };

const $ = (id) => document.getElementById(id);
const els = Object.fromEntries(
  [
    "destInput", "searchResults", "searchBar", "clearBtn", "menuBtn", "locateBtn",
    "sheet", "closeSheet", "placeName", "placeSub", "originGps", "originPick", "routeMsg",
    "routeCards", "routeDetail", "axisBreakdown", "startNavBtn",
    "gpxBar", "gpxName", "gpxStats", "gpxStart", "closeGpx", "gpxFileInput",
    "navBanner", "navArrow", "navMain", "navSub", "navBar", "navTime", "navMeta", "endNavBtn",
    "toast", "scrim", "drawer", "importGpxItem", "legendItem", "aboutItem",
    "modal", "modalTitle", "modalBody", "modalClose",
  ].map((id) => [id, $(id)])
);

const state = {
  graph: null,
  viewbox: null,
  origin: null, // {lat, lon, kind: "gps" | "map"}
  destination: null, // {lat, lon, name, sub}
  routes: [],
  selected: 0,
  computing: false,
  routeError: null,
  reqId: 0,
  pickingOrigin: false,
  me: null, // {lat, lon, accuracy, t}
  locWatchId: null,
  centerOnFix: false,
  originOnFix: false,
  gpx: null, // {name, points, distM, gainM}
  nav: null, // {kind, obj, route, idx, follow, zoomed, arrived, offCount, lastAction}
  wakeLock: null,
  searchAbort: null,
};

const mapView = new MapView("map", DEFAULT_CENTER);

init();

async function init() {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  bindUi();
  observeSheetHeight();

  // 位置情報の許可が済んでいれば、起動時に現在地へ移動する(未許可のときは勝手に確認しない)
  try {
    const perm = await navigator.permissions?.query({ name: "geolocation" });
    if (perm?.state === "granted") {
      state.centerOnFix = true;
      startLocWatch();
    }
  } catch {}

  toast("道路データを読み込み中…", 60000);
  try {
    state.graph = await loadGraph(CITY);
    const b = bounds(state.graph);
    state.viewbox = [b.west, b.north, b.east, b.south];
    hideToast();
    if (state.destination && state.origin) computeRoutes();
  } catch {
    toast(`${CITY}の道路データを読み込めませんでした。通信状況を確認してください`, 8000);
  }
}

function bindUi() {
  // 検索(規約上、入力中の自動検索はせず、Enter/検索ボタンで確定したときだけ検索する)
  $("searchBar").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = els.destInput.value.trim();
    if (!q) return;
    els.destInput.blur();
    runSearch(q);
  });
  els.destInput.addEventListener("input", () => {
    els.clearBtn.hidden = !els.destInput.value;
    if (!els.destInput.value) showRecents();
  });
  els.destInput.addEventListener("focus", () => {
    if (!els.destInput.value) showRecents();
  });
  els.clearBtn.addEventListener("click", () => {
    els.destInput.value = "";
    els.clearBtn.hidden = true;
    els.destInput.focus();
  });
  els.searchResults.addEventListener("click", (e) => {
    const item = e.target.closest("[data-pick]");
    if (!item) return;
    const r = JSON.parse(item.dataset.pick);
    selectPlace(r);
  });
  document.addEventListener("click", (e) => {
    if (!$("searchBar").contains(e.target)) els.searchResults.hidden = true;
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    els.searchResults.hidden = true;
    closeDrawer();
    els.modal.hidden = true;
  });

  // 地図タップ: 目的地を置く(出発地の指定中は出発地を置く)
  mapView.onMapClick((lat, lon) => {
    els.searchResults.hidden = true;
    if (state.nav || state.gpx) return;
    if (state.pickingOrigin) {
      state.pickingOrigin = false;
      setOrigin({ lat, lon, kind: "map" });
      return;
    }
    setDestination({ lat, lon, name: "選択した地点", sub: `${lat.toFixed(5)}, ${lon.toFixed(5)}` });
  });
  mapView.onUserPan(() => {
    if (state.nav?.follow) {
      state.nav.follow = false;
      els.locateBtn.classList.remove("following");
    }
  });

  els.locateBtn.addEventListener("click", onLocateClick);

  // 下部シート
  els.closeSheet.addEventListener("click", clearDestination);
  els.originGps.addEventListener("click", useGpsOrigin);
  els.originPick.addEventListener("click", () => {
    state.pickingOrigin = !state.pickingOrigin;
    if (state.pickingOrigin) toast("出発地にしたい場所を地図でタップしてください", 4000);
    renderSheet();
  });
  els.routeCards.addEventListener("click", (e) => {
    const card = e.target.closest("[data-i]");
    if (card) selectRoute(Number(card.dataset.i));
  });
  els.startNavBtn.addEventListener("click", () => {
    const route = state.routes[state.selected];
    if (route) startNav("app", buildNav(route.polyline, route.turns), route);
  });
  els.endNavBtn.addEventListener("click", endNav);

  // ドロワー・GPX
  els.menuBtn.addEventListener("click", openDrawer);
  els.scrim.addEventListener("click", closeDrawer);
  els.importGpxItem.addEventListener("click", () => {
    closeDrawer();
    els.gpxFileInput.click();
  });
  els.gpxFileInput.addEventListener("change", () => {
    const file = els.gpxFileInput.files[0];
    els.gpxFileInput.value = "";
    if (file) loadGpxFile(file);
  });
  els.gpxStart.addEventListener("click", () => {
    if (state.gpx) startNav("gpx", buildNav(state.gpx.points), null);
  });
  els.closeGpx.addEventListener("click", closeGpx);
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    const file = e.dataTransfer?.files?.[0];
    if (file && /\.gpx$/i.test(file.name)) loadGpxFile(file);
  });
  els.legendItem.addEventListener("click", () => {
    closeDrawer();
    openModal("ランクの見方", LEGEND_HTML);
  });
  els.aboutItem.addEventListener("click", () => {
    closeDrawer();
    openModal("データについて", ABOUT_HTML);
  });
  els.modalClose.addEventListener("click", () => (els.modal.hidden = true));
  els.modal.addEventListener("click", (e) => {
    if (e.target === els.modal) els.modal.hidden = true;
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.nav) requestWakeLock();
  });
}

// ---------- 検索 ----------

async function runSearch(query) {
  state.searchAbort?.abort();
  const ctrl = new AbortController();
  state.searchAbort = ctrl;
  showListNote("検索中…");
  try {
    const results = await searchPlace(query, { viewbox: state.viewbox, signal: ctrl.signal });
    if (!results.length) return showListNote("見つかりませんでした。別のキーワードをお試しください");
    renderPlaceList(results, "i-pin");
  } catch (err) {
    if (err.name !== "AbortError") showListNote("検索に失敗しました。通信状況を確認してください");
  }
}

function renderPlaceList(list, icon) {
  els.searchResults.innerHTML = list
    .map(
      (r) =>
        `<button type="button" class="result" data-pick='${escapeAttr(JSON.stringify(r))}'>` +
        `<svg class="ic"><use href="#${icon}"/></svg>` +
        `<span class="result-text"><div class="result-name">${escapeHtml(r.name)}</div>` +
        `<div class="result-sub">${escapeHtml(r.sub || "")}</div></span></button>`
    )
    .join("");
  els.searchResults.hidden = false;
}

function showListNote(text) {
  els.searchResults.innerHTML = `<div class="list-note">${escapeHtml(text)}</div>`;
  els.searchResults.hidden = false;
}

function showRecents() {
  const list = loadRecents();
  if (!list.length) {
    els.searchResults.hidden = true;
    return;
  }
  renderPlaceList(list, "i-history");
}

function loadRecents() {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveRecent(place) {
  try {
    const list = loadRecents().filter((r) => r.name !== place.name || r.sub !== place.sub);
    list.unshift(place);
    localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 6)));
  } catch {}
}

function selectPlace(place) {
  saveRecent(place);
  els.searchResults.hidden = true;
  els.destInput.value = place.name;
  els.clearBtn.hidden = false;
  setDestination(place, true);
}

// ---------- 出発地・目的地・ルート ----------

async function setDestination(dest, fly = false) {
  closeGpx();
  state.destination = dest;
  mapView.setDestination(dest.lat, dest.lon);
  if (fly) mapView.panTo(dest.lat, dest.lon, Math.max(mapView.map.getZoom(), 15), false);
  state.routes = [];
  state.routeError = null;
  state.reqId++;
  renderSheet();

  if (state.origin?.kind === "gps") {
    await useGpsOrigin();
  } else if (state.origin) {
    computeRoutes();
  } else {
    await useGpsOrigin();
  }
}

function clearDestination() {
  state.destination = null;
  state.routes = [];
  state.routeError = null;
  state.pickingOrigin = false;
  state.reqId++;
  mapView.clearDestination();
  mapView.clearRoute();
  els.destInput.value = "";
  els.clearBtn.hidden = true;
  renderSheet();
}

function setOrigin(origin) {
  state.origin = origin;
  if (origin.kind === "map") mapView.setOrigin(origin.lat, origin.lon);
  else mapView.clearOrigin();
  renderSheet();
  if (state.destination) computeRoutes();
}

async function useGpsOrigin() {
  state.pickingOrigin = false;
  startLocWatch();
  const fresh = state.me && Date.now() - state.me.t < 30000;
  const pos = fresh ? state.me : await getPosition();
  if (!pos) {
    // 取得に失敗。すでに出発地があればそのまま、無ければ地図での指定を促す
    renderSheet();
    return;
  }
  setOrigin({ lat: pos.lat, lon: pos.lon, kind: "gps" });
}

function getPosition() {
  if (!("geolocation" in navigator)) {
    toast("この端末では現在地を取得できません");
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        onPosition(pos);
        resolve(state.me);
      },
      (err) => {
        toast(err.code === 1 ? "位置情報の利用が許可されていません" : "現在地を取得できませんでした");
        resolve(null);
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 5000 }
    );
  });
}

const nextFrame = () => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)));

async function computeRoutes() {
  if (!state.destination || !state.origin) return;
  if (!state.graph) {
    toast("道路データを読み込み中です。完了後に自動でルートを探します", 4000);
    return;
  }
  const id = ++state.reqId;
  state.computing = true;
  state.routeError = null;
  renderSheet();
  await nextFrame();
  if (id !== state.reqId) return;

  const from = snapToRoad(state.graph, state.origin.lat, state.origin.lon);
  const to = snapToRoad(state.graph, state.destination.lat, state.destination.lon);
  let routes = [];
  let error = null;
  if (!from || !to) error = "近くに道路データが見つかりません";
  else if (from.distanceM > OUT_OF_AREA_M || to.distanceM > OUT_OF_AREA_M)
    error = `${CITY}の道路データの範囲外です。${CITY}内の場所を指定してください`;
  else {
    for (const mode of ["comfort", "fastest"]) {
      const r = findRoute(state.graph, from, to, mode);
      if (r) routes.push(r);
    }
    if (routes.length === 2 && routes[0].edgeKey === routes[1].edgeKey) routes = [routes[0]];
    if (!routes.length) error = "経路が見つかりませんでした";
  }

  state.computing = false;
  state.routes = routes;
  state.selected = 0;
  state.routeError = error;
  renderSheet();
  drawRoutes(true);
}

function selectRoute(i) {
  if (i === state.selected || !state.routes[i]) return;
  state.selected = i;
  renderSheet();
  drawRoutes(false);
}

function drawRoutes(fit) {
  if (!state.routes.length) {
    mapView.clearRoute();
    return;
  }
  mapView.showRoutes(state.routes, state.selected, selectRoute);
  if (fit) {
    const pts = state.routes.flatMap((r) => r.polyline);
    mapView.fitPoints(pts, isWide() ? 0 : els.sheet.offsetHeight, isWide() ? PANEL_W : 0);
  }
}

function renderSheet() {
  const dest = state.destination;
  if (state.nav || !dest) {
    els.sheet.hidden = true;
    return;
  }
  els.sheet.hidden = false;
  els.placeName.textContent = dest.name;
  els.placeSub.textContent = dest.sub || "";
  els.originGps.classList.toggle("on", state.origin?.kind === "gps");
  els.originPick.classList.toggle("on", state.pickingOrigin || state.origin?.kind === "map");
  els.originPick.textContent = state.pickingOrigin ? "地図をタップ…" : state.origin?.kind === "map" ? "地図で指定済み" : "地図で指定";

  const msg = els.routeMsg;
  msg.classList.remove("busy");
  msg.hidden = false;
  if (state.computing) {
    msg.textContent = "ルートを検索中…";
    msg.classList.add("busy");
  } else if (state.routeError) {
    msg.textContent = state.routeError;
  } else if (!state.origin) {
    msg.textContent = "出発地が未設定です。「現在地」を押すか、地図をタップして指定してください";
  } else if (!state.routes.length) {
    msg.textContent = "ルートを検索中…";
    msg.classList.add("busy");
  } else {
    msg.hidden = true;
  }

  const has = state.routes.length > 0 && !state.computing;
  els.routeDetail.hidden = !has;
  els.startNavBtn.hidden = !has;
  els.routeCards.innerHTML = has ? state.routes.map(routeCardHtml).join("") : "";
  if (has) renderAxes(state.routes[state.selected]);
}

function routeCardHtml(r, i) {
  const grade = r.totalGrade ?? "";
  const label =
    state.routes.length === 1 && r.mode === "comfort" ? "おすすめ(最短と同じ道)" : MODE_LABEL[r.mode];
  const climb = r.ascentM >= 5 ? ` ・ 上り ${Math.round(r.ascentM)} m` : "";
  return (
    `<button type="button" class="route-card${i === state.selected ? " selected" : ""}" data-i="${i}">` +
    `<span class="grade-badge ${grade}">${grade || "-"}</span>` +
    `<span class="route-line"><span class="route-time">${formatDuration(r.durationS)}</span>` +
    `<span class="route-dist">${(r.lengthM / 1000).toFixed(1)} km</span></span>` +
    `<span class="route-sub">${label}${climb}</span></button>`
  );
}

function renderAxes(route) {
  els.axisBreakdown.innerHTML = Object.entries(AXIS_LABEL)
    .map(([key, label]) => {
      const g = scoreToGrade(route.axisAvg[key]);
      return `<div class="axis-item">${label}<span class="grade-badge ${g ?? ""}">${g ?? "-"}</span></div>`;
    })
    .join("");
}

// ---------- 現在地 ----------

function startLocWatch() {
  if (state.locWatchId != null || !("geolocation" in navigator)) return;
  state.locWatchId = navigator.geolocation.watchPosition(onPosition, onPositionError, {
    enableHighAccuracy: true,
    maximumAge: 2000,
  });
}

function stopLocWatch() {
  if (state.locWatchId == null) return;
  navigator.geolocation.clearWatch(state.locWatchId);
  state.locWatchId = null;
}

function onPositionError(err) {
  if (err.code === 1) {
    stopLocWatch();
    toast("位置情報の利用が許可されていません");
  } else if (state.nav) {
    toast("現在地を取得できません", 2500);
  }
}

function onPosition(pos) {
  const { latitude: lat, longitude: lon, accuracy } = pos.coords;
  state.me = { lat, lon, accuracy, t: Date.now() };
  mapView.setMe(lat, lon, accuracy);
  els.locateBtn.classList.add("active");

  if (state.centerOnFix) {
    state.centerOnFix = false;
    if (!state.destination && !state.gpx) mapView.panTo(lat, lon, 15);
  }
  if (state.originOnFix) {
    state.originOnFix = false;
    if (!state.origin || state.origin.kind === "gps") setOrigin({ lat, lon, kind: "gps" });
  }
  if (state.nav) updateNav(lat, lon, accuracy);
}

function onLocateClick() {
  if (state.nav) {
    state.nav.follow = true;
    els.locateBtn.classList.add("following");
    if (state.me) mapView.panTo(state.me.lat, state.me.lon);
    return;
  }
  startLocWatch();
  if (state.me) {
    mapView.panTo(state.me.lat, state.me.lon, Math.max(mapView.map.getZoom(), 15));
    if (state.destination && (!state.origin || state.origin.kind === "gps")) useGpsOrigin();
  } else {
    state.centerOnFix = true;
    state.originOnFix = !!state.destination;
    toast("現在地を取得中…", 2500);
  }
}

// ---------- ナビ(案内) ----------

function startNav(kind, obj, route) {
  if (!("geolocation" in navigator)) {
    toast("この端末では現在地を取得できません");
    return;
  }
  state.nav = { kind, obj, route, idx: 0, follow: true, zoomed: false, arrived: false, offCount: 0, lastAction: 0 };
  document.body.classList.add("navigating");
  els.sheet.hidden = true;
  els.gpxBar.hidden = true;
  els.navBanner.hidden = false;
  els.navBar.hidden = false;
  els.locateBtn.classList.add("following");
  if (kind === "app") mapView.showRoutes([route], 0);
  els.navMain.textContent = "現在地を確認中…";
  els.navSub.textContent = "";
  els.navTime.textContent = "-";
  els.navMeta.textContent = "";
  els.navArrow.style.transform = "rotate(0deg)";
  startLocWatch();
  requestWakeLock();
  if (state.me && Date.now() - state.me.t < 30000) updateNav(state.me.lat, state.me.lon, state.me.accuracy);
}

function updateNav(lat, lon, accuracy) {
  const n = state.nav;
  if (!n || n.arrived) return;
  const p = locate(n.obj, lat, lon, n.idx);
  n.idx = p.idx;

  if (n.follow) {
    mapView.panTo(lat, lon, n.zoomed ? undefined : 17);
    n.zoomed = true;
  }
  if (p.remainingM < 25 && p.offRouteM < 60) return arrive();

  const next = n.obj.turns.find((t) => t.alongM - p.alongM > 8);
  if (next) {
    const d = describeTurn(next.signed);
    els.navMain.textContent = formatDistance(next.alongM - p.alongM);
    els.navSub.textContent = d.label;
    els.navArrow.style.transform = `rotate(${d.rotate}deg)`;
  } else {
    els.navMain.textContent = formatDistance(p.remainingM);
    els.navSub.textContent = n.kind === "app" ? "目的地まで直進" : "ルートに沿って進む";
    els.navArrow.style.transform = "rotate(0deg)";
  }

  const remainS =
    n.kind === "app" && n.route
      ? (n.route.durationS * p.remainingM) / Math.max(1, n.obj.totalM)
      : p.remainingM / ((GPX_SPEED_KMH * 1000) / 3600);
  els.navTime.textContent = formatDuration(remainS);
  els.navMeta.textContent = `${(p.remainingM / 1000).toFixed(1)} km ・ ${formatClock(new Date(Date.now() + remainS * 1000))}着`;

  if (p.offRouteM > OFF_ROUTE_M && accuracy < 100) {
    n.offCount++;
    if (n.offCount >= 3) onOffRoute(n, lat, lon);
  } else {
    n.offCount = 0;
  }
}

function onOffRoute(n, lat, lon) {
  const now = Date.now();
  if (now - n.lastAction < 12000) return;
  n.lastAction = now;
  if (n.kind !== "app" || !state.graph) {
    toast("ルートから外れています");
    return;
  }
  const from = snapToRoad(state.graph, lat, lon);
  const to = snapToRoad(state.graph, state.destination.lat, state.destination.lon);
  const route = from && to ? findRoute(state.graph, from, to, n.route.mode) : null;
  if (!route) {
    toast("ルートから外れています");
    return;
  }
  n.route = route;
  n.obj = buildNav(route.polyline, route.turns);
  n.idx = 0;
  n.offCount = 0;
  mapView.showRoutes([route], 0);
  toast("ルートを再検索しました");
}

function arrive() {
  const n = state.nav;
  n.arrived = true;
  els.navMain.textContent = "到着";
  els.navSub.textContent = "目的地に到着しました";
  els.navArrow.style.transform = "rotate(0deg)";
  els.navTime.textContent = "到着";
  els.navMeta.textContent = "";
  toast("目的地に到着しました", 4000);
}

function endNav() {
  state.nav = null;
  document.body.classList.remove("navigating");
  els.navBanner.hidden = true;
  els.navBar.hidden = true;
  els.locateBtn.classList.remove("following");
  releaseWakeLock();
  stopLocWatch();
  if (state.gpx) {
    els.gpxBar.hidden = false;
    mapView.showImported(state.gpx.points);
  } else {
    renderSheet();
    drawRoutes(false);
  }
}

async function requestWakeLock() {
  try {
    if ("wakeLock" in navigator && !state.wakeLock) {
      state.wakeLock = await navigator.wakeLock.request("screen");
      state.wakeLock.addEventListener("release", () => (state.wakeLock = null));
    }
  } catch {}
}

function releaseWakeLock() {
  try {
    state.wakeLock?.release().catch(() => {});
  } catch {}
  state.wakeLock = null;
}

// ---------- GPX ----------

async function loadGpxFile(file) {
  try {
    const { name, points } = parseGpx(await file.text());
    if (state.nav) endNav();
    clearDestination();
    state.gpx = {
      name: name || file.name.replace(/\.gpx$/i, ""),
      points,
      distM: totalDistanceM(points),
      gainM: elevationGainM(points),
    };
    mapView.showImported(points);
    mapView.fitPoints(points, isWide() ? 0 : 150, isWide() ? PANEL_W : 0);
    els.gpxName.textContent = state.gpx.name;
    els.gpxStats.textContent = `${(state.gpx.distM / 1000).toFixed(1)} km ・ 獲得標高 ${Math.round(state.gpx.gainM)} m`;
    els.gpxBar.hidden = false;
  } catch (err) {
    toast(err.message || "GPXの読み込みに失敗しました");
  }
}

function closeGpx() {
  if (!state.gpx) return;
  if (state.nav?.kind === "gpx") endNav();
  state.gpx = null;
  els.gpxBar.hidden = true;
  mapView.clearRoute();
}

// ---------- 共通UI ----------

function openDrawer() {
  els.scrim.hidden = false;
  els.drawer.hidden = false;
}
function closeDrawer() {
  els.scrim.hidden = true;
  els.drawer.hidden = true;
}
function openModal(title, html) {
  els.modalTitle.textContent = title;
  els.modalBody.innerHTML = html;
  els.modal.hidden = false;
}

// 下部シートの高さに合わせて、現在地ボタンなどを押し上げる
function observeSheetHeight() {
  const panels = [els.sheet, els.gpxBar, els.navBar];
  const update = () => {
    const h = Math.max(0, ...panels.filter((p) => !p.hidden).map((p) => p.offsetHeight));
    document.documentElement.style.setProperty("--sheet-h", `${h}px`);
  };
  if ("ResizeObserver" in window) {
    const ro = new ResizeObserver(update);
    panels.forEach((p) => ro.observe(p));
  }
  window.addEventListener("resize", update);
}

const isWide = () => window.matchMedia("(min-width: 720px)").matches;

function toast(msg, ms = 3000) {
  els.toast.textContent = msg;
  els.toast.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (els.toast.hidden = true), ms);
}
function hideToast() {
  clearTimeout(toast._t);
  els.toast.hidden = true;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const escapeAttr = escapeHtml;

const LEGEND_HTML = `
  <p>区間ごとの走りやすさを5段階で表します。ルートの線の色もこのランクです。</p>
  ${[["S", "非常に快適"], ["A", "快適"], ["B", "ふつう"], ["C", "やや注意"], ["D", "注意が必要"]]
    .map(([g, t]) => `<div class="grade-row"><span class="grade-badge ${g}">${g}</span>${t}</div>`)
    .join("")}
  <p>評価項目: 自転車帯(自転車通行帯の有無)・路肩幅・交通量・信号の多さ・起伏(勾配)。データが無い項目は「-」で、総合ランクの計算に含めません。</p>
  <p>所要時間は平地17km/hを基準に、上りは遅く・下りは速く見積もった目安です。</p>`;

const ABOUT_HTML = `
  <p>走りやすさ優先の自転車ナビです。現在は${CITY}のみ対応しています。</p>
  <ul>
    <li>地図・道路: © OpenStreetMap contributors</li>
    <li>交通量: 国土交通省 道路交通センサス</li>
    <li>路肩幅の推定: 国土地理院の空中写真、Mapillaryの街路写真をオフラインで解析</li>
    <li>標高: 国土地理院</li>
  </ul>
  <p>ランクは推定を含みます。実際の道路状況・交通ルールを優先し、安全に走行してください。</p>`;
