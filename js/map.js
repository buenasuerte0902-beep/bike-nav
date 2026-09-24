// map.js — Leaflet地図。ルートをランク(S〜D)で色分け表示する。
const GRADE_COLOR = { S: "#1e8e3e", A: "#7cb342", B: "#fbbc04", C: "#f29900", D: "#d93025" };

export class MapView {
  constructor(elId, center) {
    this.map = L.map(elId, { zoomControl: false, attributionControl: false });
    L.control.zoom({ position: "bottomleft" }).addTo(this.map);
    this.map.setView(center, 14);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      crossOrigin: true,
    }).addTo(this.map);
    L.control.attribution({ prefix: false }).addAttribution("© OpenStreetMap").addTo(this.map);

    this.routeLayer = L.layerGroup().addTo(this.map);
    this.markerLayer = L.layerGroup().addTo(this.map);
    this.meMarker = null;
  }

  /** 地図長押し/クリックで座標を受け取る（現在地が使えない環境向けの手動指定） */
  onMapClick(handler) {
    this.map.on("click", (e) => handler(e.latlng.lat, e.latlng.lng));
  }

  setOrigin(lat, lon) {
    this._setMarker("origin", lat, lon, "#1a73e8", "出発地");
  }
  setDestination(lat, lon) {
    this._setMarker("dest", lat, lon, "#d93025", "目的地");
  }
  _setMarker(key, lat, lon, color, label) {
    this[`_m_${key}`]?.remove();
    this[`_m_${key}`] = L.circleMarker([lat, lon], {
      radius: 9,
      color: "#fff",
      weight: 2,
      fillColor: color,
      fillOpacity: 1,
    })
      .bindTooltip(label, { permanent: false })
      .addTo(this.markerLayer);
  }

  clearRoute() {
    this.routeLayer.clearLayers();
  }

  /** route.js の findRoute() が返す edges 配列をランク色で描画 */
  setRoute(edges) {
    this.clearRoute();
    for (const e of edges) {
      const color = GRADE_COLOR[e.total] || "#9aa0a6";
      L.polyline(
        e.points.map((p) => [p[0], p[1]]),
        { color, weight: 6, opacity: 0.9, className: "route-glow" }
      )
        .bindTooltip(this._edgeTooltip(e), { sticky: true })
        .addTo(this.routeLayer);
    }
    const allPts = edges.flatMap((e) => e.points);
    if (allPts.length) this.map.fitBounds(L.latLngBounds(allPts), { padding: [60, 200] });
  }

  /** GPXインポートしたルートを描画(ランクデータが無いので単色) */
  setImportedRoute(points) {
    this.clearRoute();
    this._importedLine = L.polyline(
      points.map((p) => [p[0], p[1]]),
      { color: "#8e24aa", weight: 5, opacity: 0.85 }
    ).addTo(this.routeLayer);
    this.map.fitBounds(this._importedLine.getBounds(), { padding: [60, 200] });
  }

  _edgeTooltip(e) {
    const g = e.grades || {};
    return (
      `総合: ${e.total ?? "-"}<br>` +
      `自転車帯 ${g.bikeLane ?? "-"} / 交通量 ${g.traffic ?? "-"} / ` +
      `信号 ${g.signals ?? "-"} / 勾配 ${g.slope ?? "-"}`
    );
  }

  updateMe(lat, lon) {
    if (!this.meMarker) {
      this.meMarker = L.circleMarker([lat, lon], {
        radius: 8,
        color: "#fff",
        weight: 2,
        fillColor: "#4285f4",
        fillOpacity: 1,
      }).addTo(this.markerLayer);
    } else {
      this.meMarker.setLatLng([lat, lon]);
    }
  }

  panTo(lat, lon, zoom) {
    this.map.setView([lat, lon], zoom ?? this.map.getZoom(), { animate: true });
  }
}
