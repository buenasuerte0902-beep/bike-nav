// map.js — Leaflet地図。ルートをランク(S〜D)で色分け表示し、候補ルートは灰色で並べて表示する。
const GRADE_COLOR = { S: "#1e8e3e", A: "#7cb342", B: "#fbbc04", C: "#f29900", D: "#d93025" };
const UNKNOWN_COLOR = "#9aa0a6";
const IMPORTED_COLOR = "#8e24aa";

const PIN_SVG =
  '<svg viewBox="0 0 24 24" width="36" height="36"><path fill="#ea4335" stroke="#b31412" stroke-width=".6" d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z"/><circle cx="12" cy="9" r="2.6" fill="#7a0c0a"/></svg>';

export class MapView {
  constructor(elId, center) {
    this.map = L.map(elId, { zoomControl: false, attributionControl: false });
    L.control.zoom({ position: "topright" }).addTo(this.map);
    this.map.setView(center, 14);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      crossOrigin: true,
    }).addTo(this.map);
    L.control.attribution({ prefix: false }).addAttribution("© OpenStreetMap").addTo(this.map);

    this.routeLayer = L.layerGroup().addTo(this.map);
    this.markerLayer = L.layerGroup().addTo(this.map);
    this.signalLayer = L.layerGroup().addTo(this.map);
    this.meMarker = null;
    this.meAccuracy = null;
    this._originMarker = null;
    this._destMarker = null;
  }

  onMapClick(handler) {
    this.map.on("click", (e) => handler(e.latlng.lat, e.latlng.lng));
  }

  /** ユーザーが地図をドラッグしたとき(ナビ中の追従解除に使う) */
  onUserPan(handler) {
    this.map.on("dragstart", handler);
  }

  setOrigin(lat, lon) {
    this.clearOrigin();
    this._originMarker = L.marker([lat, lon], {
      icon: L.divIcon({ className: "", html: '<div class="origin-dot"></div>', iconSize: [22, 22], iconAnchor: [11, 11] }),
      interactive: false,
      keyboard: false,
    }).addTo(this.markerLayer);
  }
  clearOrigin() {
    this._originMarker?.remove();
    this._originMarker = null;
  }

  setDestination(lat, lon) {
    this.clearDestination();
    this._destMarker = L.marker([lat, lon], {
      icon: L.divIcon({ className: "", html: `<div class="dest-pin">${PIN_SVG}</div>`, iconSize: [36, 36], iconAnchor: [18, 34] }),
      interactive: false,
      keyboard: false,
    }).addTo(this.markerLayer);
  }
  clearDestination() {
    this._destMarker?.remove();
    this._destMarker = null;
  }

  /** 信号機マークを描く。名前データがあるものはマークの上に名前を出す */
  showSignals(signals) {
    this.signalLayer.clearLayers();
    for (const s of signals) {
      const named = !!s.name;
      const html = `<div class="sig-mark${named ? " named" : ""}">${
        named ? `<span class="sig-name">${escapeHtml(s.name)}</span>` : ""
      }<span class="sig-icon"><i></i><i></i><i></i></span></div>`;
      L.marker([s.lat, s.lon], {
        icon: L.divIcon({ className: "", html, iconSize: [18, 30], iconAnchor: [9, 15] }),
        interactive: false,
        keyboard: false,
        zIndexOffset: named ? 300 : 200,
      }).addTo(this.signalLayer);
    }
  }
  clearSignals() {
    this.signalLayer.clearLayers();
  }

  clearRoute() {
    this.routeLayer.clearLayers();
  }

  /**
   * 候補ルートを描画する。選択中のルートはランク色(白縁つき)、それ以外は灰色で
   * 下に描き、タップで onSelect(index) を呼ぶ。
   */
  showRoutes(routes, selected, onSelect) {
    this.clearRoute();
    const opts = { bubblingMouseEvents: false, lineCap: "round", lineJoin: "round" };
    routes.forEach((r, i) => {
      if (i === selected) return;
      const casing = L.polyline(r.polyline, { ...opts, color: "#fff", weight: 10, opacity: 1 }).addTo(this.routeLayer);
      const line = L.polyline(r.polyline, { ...opts, color: "#8ab4f8", weight: 6, opacity: 1 }).addTo(this.routeLayer);
      const hit = L.polyline(r.polyline, { ...opts, color: "#000", weight: 24, opacity: 0 }).addTo(this.routeLayer);
      for (const l of [casing, line, hit]) l.on("click", () => onSelect?.(i));
    });
    const r = routes[selected];
    if (!r) return;
    L.polyline(r.polyline, { ...opts, color: "#fff", weight: 11, opacity: 1 }).addTo(this.routeLayer);
    for (const run of r.runs) {
      if (run.connector) {
        L.polyline(run.points, { ...opts, color: UNKNOWN_COLOR, weight: 4, dashArray: "2 8" }).addTo(this.routeLayer);
      } else {
        L.polyline(run.points, { ...opts, color: GRADE_COLOR[run.grade] || UNKNOWN_COLOR, weight: 7, opacity: 1 })
          .bindTooltip(`この区間のランク: ${run.grade ?? "-"}`, { sticky: true })
          .addTo(this.routeLayer);
      }
    }
  }

  /** GPXインポートしたルート(ランクデータが無いので単色) */
  showImported(points) {
    this.clearRoute();
    const latlngs = points.map((p) => [p[0], p[1]]);
    const opts = { bubblingMouseEvents: false, lineCap: "round", lineJoin: "round" };
    L.polyline(latlngs, { ...opts, color: "#fff", weight: 10 }).addTo(this.routeLayer);
    L.polyline(latlngs, { ...opts, color: IMPORTED_COLOR, weight: 6 }).addTo(this.routeLayer);
  }

  /** points が画面に収まるようにする。bottom はシートに隠れる分(px) */
  fitPoints(points, bottom = 0, left = 0) {
    if (!points.length) return;
    this.map.stop();
    this.map.fitBounds(L.latLngBounds(points.map((p) => [p[0], p[1]])), {
      paddingTopLeft: [left + 40, 90],
      paddingBottomRight: [40, bottom + 30],
      maxZoom: 17,
      animate: false,
    });
  }

  /** 現在地(青いドット + 精度の円) */
  setMe(lat, lon, accuracy) {
    if (!this.meMarker) {
      this.meMarker = L.marker([lat, lon], {
        icon: L.divIcon({ className: "", html: '<div class="me-dot"></div>', iconSize: [22, 22], iconAnchor: [11, 11] }),
        interactive: false,
        keyboard: false,
        zIndexOffset: 1000,
      }).addTo(this.markerLayer);
      this.meAccuracy = L.circle([lat, lon], {
        radius: accuracy || 0,
        color: "#4285f4",
        weight: 1,
        fillColor: "#4285f4",
        fillOpacity: 0.12,
        interactive: false,
      }).addTo(this.markerLayer);
    } else {
      this.meMarker.setLatLng([lat, lon]);
      this.meAccuracy.setLatLng([lat, lon]).setRadius(accuracy || 0);
    }
  }

  panTo(lat, lon, zoom, animate = true) {
    this.map.stop();
    this.map.setView([lat, lon], zoom ?? this.map.getZoom(), { animate });
  }
}

function escapeHtml(t) {
  return String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
