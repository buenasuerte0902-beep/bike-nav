// label.js — 手動ラベリングUI。判定は1区間ごとにサーバー(tools/label_server.py)へ即保存する。
const $ = (id) => document.getElementById(id);
const GRADE_COLOR = { S: "#43d17a", A: "#9ccc3c", B: "#fbbc04", C: "#f29900", D: "#ff5a4d" };
const GRADES = ["S", "A", "B", "C", "D"];
const GUIDE = {
  sh: ["1.5m以上<br>余裕で並走", "1.0〜1.5m", "0.5〜1.0m", "0.2〜0.5m", "ほぼ無し<br>白線のみ"],
  sm: ["新しく平滑", "良好", "小さなひび<br>補修跡", "凹凸・轍<br>目立つ", "荒れ・段差<br>砂利"],
};
const HW = {
  primary: "主要道路", secondary: "準主要道路", tertiary: "一般道(tertiary)", unclassified: "一般道",
  residential: "住宅街の道", living_street: "生活道路", cycleway: "自転車道", service: "私道・構内路",
  path: "小径", track: "農道など",
};

const map = L.map("map", { preferCanvas: true, zoomControl: true, maxZoom: 20 });
const aerial = L.tileLayer("https://maps.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg", {
  maxNativeZoom: 18, maxZoom: 20, attribution: "地理院タイル(航空写真)",
});
const std = L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png", {
  maxNativeZoom: 18, maxZoom: 20, attribution: "地理院タイル",
});
let baseIsAerial = true;
aerial.addTo(map);
const renderer = L.canvas({ padding: 0.5 });
const curLayer = L.layerGroup().addTo(map);

const state = {
  city: null, bbox: null, chunks: [], polys: [], idx: -1, history: [],
  pend: { sh: undefined, sm: undefined }, photoIdx: 0, saving: false,
};

function toast(msg, ms = 2500) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), ms);
}

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.status);
  return data;
}

// ---------- 判定ボタン ----------
function buildPad() {
  for (const [axis, rowId] of [["sh", "rowSh"], ["sm", "rowSm"]]) {
    const row = $(rowId);
    row.innerHTML = "";
    GRADES.forEach((g, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.g = g;
      b.dataset.axis = axis;
      b.innerHTML = `<span class="g">${g}</span><span class="d">${GUIDE[axis][i]}</span>`;
      b.addEventListener("click", () => pick(g, axis));
      row.appendChild(b);
    });
  }
}

function renderPending() {
  for (const axis of ["sh", "sm"]) {
    const row = $(axis === "sh" ? "rowSh" : "rowSm");
    for (const b of row.children) b.classList.toggle("sel", b.dataset.g === state.pend[axis]);
  }
  const p = state.pend;
  const show = (v) => (v === undefined ? "…" : v === null ? "不明" : v);
  $("pending").textContent = p.sh === undefined && p.sm === undefined ? "" : `路肩 ${show(p.sh)} / 路面 ${show(p.sm)}`;
}

/** g: "S".."D" または null(わからない)。axis 省略時は未入力の軸を 路肩→路面 の順に埋める */
function pick(g, axis) {
  if (state.idx < 0 || state.saving) return;
  axis = axis || (state.pend.sh === undefined ? "sh" : "sm");
  state.pend[axis] = g;
  renderPending();
  if (state.pend.sh !== undefined && state.pend.sm !== undefined) commit();
}

async function commit() {
  const c = state.chunks[state.idx];
  const sh = state.pend.sh ?? null;
  const sm = state.pend.sm ?? null;
  if (sh === null && sm === null) return advance();
  state.saving = true;
  try {
    await api("/api/label", { city: state.city, chunk: c.id, shoulderWidth: sh, smoothness: sm });
    c.label = { shoulderWidth: sh ?? c.label?.shoulderWidth ?? null, smoothness: sm ?? c.label?.smoothness ?? null };
    paintPoly(state.idx);
    advance();
  } catch (e) {
    toast("保存に失敗: " + e.message, 5000);
  } finally {
    state.saving = false;
  }
}

async function clearCurrent() {
  const c = state.chunks[state.idx];
  if (!c?.label) return;
  try {
    await api("/api/label", { city: state.city, chunk: c.id, clear: true });
    c.label = null;
    paintPoly(state.idx);
    resetPending();
    $("len").textContent = `${c.lengthM}m`;
    toast("判定を消しました");
  } catch (e) {
    toast("失敗: " + e.message, 5000);
  }
}

// ---------- 地図 ----------
function polyStyle(c) {
  if (c.label) {
    const g = c.label.shoulderWidth || c.label.smoothness;
    return { color: GRADE_COLOR[g] || "#9aa4ad", weight: 4, opacity: 0.95 };
  }
  return { color: "#ffffff", weight: 3, opacity: 0.55 };
}
function paintPoly(i) {
  state.polys[i]?.setStyle(polyStyle(state.chunks[i]));
  updateProgress();
}

function drawChunks() {
  for (const p of state.polys) p.remove();
  curLayer.clearLayers();
  state.polys = state.chunks.map((c, i) => {
    const pl = L.polyline(c.lines, { ...polyStyle(c), renderer, bubblingMouseEvents: false, lineCap: "round" }).addTo(map);
    pl.on("click", () => goTo(i, true));
    return pl;
  });
}

function showCurrent(fit) {
  curLayer.clearLayers();
  const c = state.chunks[state.idx];
  if (!c) return;
  L.polyline(c.lines, { color: "#000", weight: 12, opacity: 0.6, interactive: false, renderer }).addTo(curLayer);
  const top = L.polyline(c.lines, { color: "#00e5ff", weight: 6, opacity: 1, interactive: false, renderer }).addTo(curLayer);
  top.bringToFront();
  if (fit) {
    const b = top.getBounds();
    const z = Math.min(map.getBoundsZoom(b.pad(0.4)), 18);
    map.setView(b.getCenter(), Math.max(z, 17), { animate: false });
  }
}

function setBase(aer) {
  baseIsAerial = aer;
  if (aer) { map.removeLayer(std); aerial.addTo(map); } else { map.removeLayer(aerial); std.addTo(map); }
}

// ---------- 写真ビューア(全天球は透視投影に変換して表示) ----------
const pano = { img: null, off: null, yaw: 0, pitch: -8, fov: 100, raf: 0 };

function renderPano() {
  pano.raf = 0;
  const cv = $("pano");
  const ctx = cv.getContext("2d");
  const w = cv.width, h = cv.height;
  const src = pano.off;
  if (!src) return;
  const out = ctx.createImageData(w, h);
  const sw = src.width, sh = src.height, sd = src.data, od = out.data;
  const f = w / 2 / Math.tan((pano.fov * Math.PI) / 360);
  const yaw = (pano.yaw * Math.PI) / 180, pitch = (pano.pitch * Math.PI) / 180;
  const cp = Math.cos(pitch), sp = Math.sin(pitch), cy = Math.cos(yaw), sy = Math.sin(yaw);
  let o = 0;
  for (let j = 0; j < h; j++) {
    const y = j - h / 2;
    for (let i = 0; i < w; i++) {
      const x = i - w / 2;
      const n = Math.hypot(x, y, f);
      const xn = x / n, yn = y / n, zn = f / n;
      const y2 = yn * cp - zn * sp, z2 = yn * sp + zn * cp;
      const x3 = xn * cy + z2 * sy, z3 = -xn * sy + z2 * cy;
      const lon = Math.atan2(x3, z3), lat = Math.asin(Math.max(-1, Math.min(1, -y2)));
      let sx = Math.floor((lon / (2 * Math.PI) + 0.5) * sw) % sw;
      const sY = Math.min(sh - 1, Math.max(0, Math.floor((0.5 - lat / Math.PI) * sh)));
      if (sx < 0) sx += sw;
      const k = (sY * sw + sx) * 4;
      od[o++] = sd[k]; od[o++] = sd[k + 1]; od[o++] = sd[k + 2]; od[o++] = 255;
    }
  }
  ctx.putImageData(out, 0, 0);
}
function schedulePano() {
  if (!pano.raf) pano.raf = requestAnimationFrame(renderPano);
}

function initPanoInput() {
  const cv = $("pano");
  let drag = null;
  cv.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY }; cv.setPointerCapture(e.pointerId); });
  cv.addEventListener("pointerup", () => (drag = null));
  cv.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const k = pano.fov / cv.clientWidth;
    pano.yaw -= (e.clientX - drag.x) * k;
    pano.pitch = Math.max(-80, Math.min(80, pano.pitch + (e.clientY - drag.y) * k));
    drag = { x: e.clientX, y: e.clientY };
    schedulePano();
  });
  cv.addEventListener("wheel", (e) => {
    e.preventDefault();
    pano.fov = Math.max(40, Math.min(120, pano.fov + e.deltaY * 0.05));
    schedulePano();
  }, { passive: false });
}

function showPhoto(i) {
  const c = state.chunks[state.idx];
  const photos = c?.photos || [];
  state.photoIdx = Math.max(0, Math.min(i, photos.length - 1));
  const p = photos[state.photoIdx];
  $("pano").hidden = $("photo").hidden = true;
  $("noPhoto").hidden = !!p;
  $("thumbs").innerHTML = "";
  $("photoCap").textContent = "";
  if (!p) return;

  photos.forEach((q, n) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = `写真${n + 1} (${Math.round(q.distanceM)}m)`;
    b.classList.toggle("on", n === state.photoIdx);
    b.addEventListener("click", () => showPhoto(n));
    $("thumbs").appendChild(b);
  });
  const year = p.capturedAt ? new Date(p.capturedAt).getFullYear() : "?";
  $("photoCap").textContent = `この区間の端から約${Math.round(p.distanceM)}m ・ ${year}年撮影 ・ Mapillary(CC BY-SA)`;

  const url = `/photo/${encodeURIComponent(state.city)}/${p.edgeId}.jpg`;
  const img = new Image();
  const token = (showPhoto._t = (showPhoto._t || 0) + 1);
  img.onload = () => {
    if (token !== showPhoto._t) return;
    if (Math.abs(img.width / img.height - 2) <= 0.15) {
      const W = Math.min(2048, img.width), H = Math.round((W * img.height) / img.width);
      const c2 = document.createElement("canvas");
      c2.width = W; c2.height = H;
      const cx = c2.getContext("2d");
      cx.drawImage(img, 0, 0, W, H);
      pano.off = cx.getImageData(0, 0, W, H);
      pano.yaw = p.compass == null ? 0 : (((p.bearing - p.compass) % 360) + 540) % 360 - 180;
      pano.pitch = -8;
      pano.fov = 100;
      $("pano").hidden = false;
      renderPano();
    } else {
      $("photo").src = url;
      $("photo").hidden = false;
    }
  };
  img.onerror = () => { $("noPhoto").hidden = false; };
  img.src = url;
}

// ---------- 区間の移動 ----------
function resetPending() {
  state.pend = { sh: undefined, sm: undefined };
  renderPending();
}

function updateProgress() {
  const total = state.chunks.length;
  const done = state.chunks.reduce((n, c) => n + (c.label ? 1 : 0), 0);
  $("progress").textContent = `${state.city} ・ この範囲 ${total}区間中 判定済み ${done} / 残り ${total - done}` +
    (state.idx >= 0 ? ` ・ 表示中 #${state.idx + 1}` : "");
}

function goTo(i, fromClick = false, quiet = false) {
  if (i < 0 || i >= state.chunks.length) return;
  if (!fromClick && state.idx >= 0) state.history.push(state.idx);
  state.idx = i;
  const c = state.chunks[i];
  $("hw").textContent = HW[c.highway] || c.highway || "-";
  $("len").textContent = `${c.lengthM}m` + (c.label ? " ・ 判定済み(押すと上書き)" : "");
  $("guess").textContent = c.guess ? `航空写真による自動推定(参考): 路肩 ${c.guess}` : "自動推定なし";
  resetPending();
  showCurrent(true);
  showPhoto(0);
  updateProgress();
  if ($("svSync").checked && !quiet) openStreetView();
}

function advance() {
  const n = state.chunks.length;
  for (let k = 1; k <= n; k++) {
    const i = (state.idx + k) % n;
    if (!state.chunks[i].label) return goTo(i);
  }
  toast("この範囲の区間はすべて判定済みです。別の範囲か都市に移りましょう", 5000);
  resetPending();
}

function back() {
  const i = state.history.pop();
  if (i !== undefined) {
    state.idx = i;
    goTo(i, true);
  }
}

// ---------- 読み込み ----------
async function loadQueue(city, bbox) {
  const qs = new URLSearchParams({ city });
  if (bbox) qs.set("bbox", bbox.join(","));
  $("progress").textContent = "読み込み中…";
  const data = await api("/api/queue?" + qs);
  state.city = data.city;
  state.bbox = data.bbox;
  state.chunks = data.chunks;
  state.history = [];
  state.idx = -1;
  drawChunks();
  if (!bbox && data.bbox) map.fitBounds([[data.bbox[0], data.bbox[1]], [data.bbox[2], data.bbox[3]]], { animate: false });
  const first = state.chunks.findIndex((c) => !c.label);
  if (first >= 0) goTo(first, true, true);
  else { updateProgress(); toast("この範囲は判定済みか、道路がありません"); }
}

async function merge() {
  if (!confirm("判定をグラフ(data/graph)に書き込みます。よろしいですか?")) return;
  try {
    const r = await api("/api/merge", { city: state.city });
    toast(r.message || "反映しました", 6000);
  } catch (e) {
    toast("反映に失敗: " + e.message, 6000);
  }
}

// ---------- 入力 ----------
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "SELECT" || e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key;
  if (k >= "1" && k <= "5") pick(GRADES[+k - 1]);
  else if (k === "-" || k === "0") pick(null);
  else if (k === "Enter") { if (state.pend.sh !== undefined || state.pend.sm !== undefined) commit(); }
  else if (k === " ") { e.preventDefault(); advance(); }
  else if (k === "Backspace") { e.preventDefault(); back(); }
  else if (k === "Delete") clearCurrent();
  else if (k === "Escape") resetPending();
  else if (k === "ArrowRight") showPhoto(state.photoIdx + 1);
  else if (k === "ArrowLeft") showPhoto(state.photoIdx - 1);
  else if (k === "v" || k === "V") { pano.yaw += 180; schedulePano(); }
  else if (k === "g" || k === "G") openStreetView();
  else if (k === "m" || k === "M") setBase(!baseIsAerial);
});

function openStreetView() {
  const c = state.chunks[state.idx];
  if (!c) return;
  const [lat, lon] = c.center;
  const w = window.open(`https://www.google.com/maps?q=&layer=c&cbll=${lat.toFixed(6)},${lon.toFixed(6)}`, "streetview-window");
  if (w) w.opener = null;
  else toast("Street View のウィンドウがブロックされました。ポップアップを許可してください", 4000);
}

async function init() {
  buildPad();
  initPanoInput();
  $("layerBtn").onclick = () => setBase(!baseIsAerial);
  $("svBtn").onclick = openStreetView;
  $("skipBtn").onclick = advance;
  $("backBtn").onclick = back;
  $("clearBtn").onclick = clearCurrent;
  $("mergeBtn").onclick = merge;
  try { $("svSync").checked = localStorage.getItem("labelSvSync") === "1"; } catch {}
  $("svSync").onchange = () => {
    try { localStorage.setItem("labelSvSync", $("svSync").checked ? "1" : "0"); } catch {}
    if ($("svSync").checked) openStreetView();
  };
  $("requeue").onclick = () => {
    const b = map.getBounds();
    loadQueue(state.city, [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()]).catch((e) => toast("失敗: " + e.message, 5000));
  };
  const cities = await api("/api/cities");
  const sel = $("citySel");
  sel.innerHTML = cities.map((c) => `<option value="${c.name}">${c.name} (判定${c.labeledEdges}辺)</option>`).join("");
  sel.onchange = () => loadQueue(sel.value).catch((e) => toast("失敗: " + e.message, 5000));
  if (!cities.length) { $("progress").textContent = "data/graph/ に都市のグラフがありません"; return; }
  const last = localStorage.getItem("labelCity");
  if (last && cities.some((c) => c.name === last)) sel.value = last;
  sel.addEventListener("change", () => localStorage.setItem("labelCity", sel.value));
  await loadQueue(sel.value);
}

init().catch((e) => { $("progress").textContent = "起動に失敗: " + e.message; });
