// app.js — 画面の結線。検索・現在地・ルート計算・下部シート表示。
import { loadGraph, nearestNode, bounds } from "./graph.js";
import { findRoute } from "./route.js";
import { searchPlace } from "./geocode.js";
import { MapView } from "./map.js";

const CITY = "金沢市";
const DEFAULT_CENTER = [36.5613, 136.6562];
const AXIS_LABEL = { bikeLane: "自転車帯", traffic: "交通量", signals: "信号", slope: "起伏" };

const state = {
  mode: "comfort", // "comfort" | "fastest"
  origin: null, // {lat, lon}
  destination: null,
  graph: null,
};

const mapView = new MapView("map", DEFAULT_CENTER);
const els = {
  destInput: document.getElementById("destInput"),
  searchResults: document.getElementById("searchResults"),
  modeToggle: document.getElementById("modeToggle"),
  locateBtn: document.getElementById("locateBtn"),
  sheet: document.getElementById("sheet"),
  closeSheet: document.getElementById("closeSheet"),
  totalGrade: document.getElementById("totalGrade"),
  sheetDist: document.getElementById("sheetDist"),
  axisBreakdown: document.getElementById("axisBreakdown"),
  toast: document.getElementById("toast"),
};

init();

async function init() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }

  try {
    state.graph = await loadGraph(CITY);
    const b = bounds(state.graph);
    state.viewbox = [b.west, b.north, b.east, b.south];
  } catch (err) {
    toast(`${CITY}の道路データがありません。tools/build_graph.py --city ${CITY} を実行してください。`, 6000);
  }

  els.modeToggle.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-mode]");
    if (!btn) return;
    state.mode = btn.dataset.mode;
    for (const b of els.modeToggle.querySelectorAll("button")) b.classList.toggle("active", b === btn);
    if (state.origin && state.destination) computeRoute();
  });

  els.locateBtn.addEventListener("click", useCurrentLocation);
  els.closeSheet.addEventListener("click", () => (els.sheet.hidden = true));
  mapView.onMapClick((lat, lon) => {
    state.origin = { lat, lon };
    mapView.setOrigin(lat, lon);
    toast("出発地を設定しました（地図タップ）");
    if (state.destination) computeRoute();
  });

  let searchTimer = null;
  els.destInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const q = els.destInput.value.trim();
    if (q.length < 2) {
      els.searchResults.hidden = true;
      return;
    }
    searchTimer = setTimeout(() => runSearch(q), 400);
  });

  document.addEventListener("click", (e) => {
    if (!els.searchResults.contains(e.target) && e.target !== els.destInput) {
      els.searchResults.hidden = true;
    }
  });
}

async function runSearch(query) {
  try {
    const results = await searchPlace(query, { viewbox: state.viewbox });
    if (!results.length) {
      els.searchResults.innerHTML = `<div class="result">見つかりませんでした</div>`;
      els.searchResults.hidden = false;
      return;
    }
    els.searchResults.innerHTML = results
      .map((r, i) => `<div class="result" data-i="${i}">${escapeHtml(r.label)}</div>`)
      .join("");
    els.searchResults.hidden = false;
    els.searchResults.querySelectorAll(".result[data-i]").forEach((el) => {
      el.addEventListener("click", () => {
        const r = results[Number(el.dataset.i)];
        selectDestination(r.lat, r.lon, r.label);
      });
    });
  } catch {
    toast("検索に失敗しました");
  }
}

function selectDestination(lat, lon, label) {
  state.destination = { lat, lon };
  mapView.setDestination(lat, lon);
  els.destInput.value = label;
  els.searchResults.hidden = true;
  if (!state.origin) {
    toast("出発地が未設定です。現在地ボタンか、地図タップで指定してください");
  } else {
    computeRoute();
  }
}

function useCurrentLocation() {
  if (!("geolocation" in navigator)) {
    toast("この端末では現在地を取得できません");
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const { latitude: lat, longitude: lon } = pos.coords;
      state.origin = { lat, lon };
      mapView.setOrigin(lat, lon);
      mapView.updateMe(lat, lon);
      mapView.panTo(lat, lon, 15);
      if (state.destination) computeRoute();
    },
    () => toast("現在地を取得できませんでした"),
    { enableHighAccuracy: true, timeout: 8000 }
  );
}

function computeRoute() {
  if (!state.graph) {
    toast("道路データが読み込まれていません");
    return;
  }
  const from = nearestNode(state.graph, state.origin.lat, state.origin.lon);
  const to = nearestNode(state.graph, state.destination.lat, state.destination.lon);
  if (!from.id || !to.id) {
    toast("近くに道路データが見つかりません");
    return;
  }
  const result = findRoute(state.graph, from.id, to.id, state.mode);
  if (!result) {
    toast("経路が見つかりませんでした");
    return;
  }
  mapView.setRoute(result.edges);
  showSummary(result);
}

function showSummary(result) {
  els.totalGrade.textContent = result.totalGrade ?? "-";
  els.totalGrade.className = `grade-badge ${result.totalGrade ?? ""}`;
  els.sheetDist.textContent = `${(result.lengthM / 1000).toFixed(1)} km`;
  els.axisBreakdown.innerHTML = Object.entries(AXIS_LABEL)
    .map(([key, label]) => {
      const grade = scoreToGrade(result.axisAvg[key]);
      return `<div class="axis-item">${label}<span class="grade-badge ${grade ?? ""}">${grade ?? "-"}</span></div>`;
    })
    .join("");
  els.sheet.hidden = false;
}

function scoreToGrade(avg) {
  if (avg == null) return null;
  if (avg >= 4.5) return "S";
  if (avg >= 3.5) return "A";
  if (avg >= 2.5) return "B";
  if (avg >= 1.5) return "C";
  return "D";
}

function toast(msg, ms = 3000) {
  els.toast.textContent = msg;
  els.toast.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (els.toast.hidden = true), ms);
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
