const map = L.map("map", { preferCanvas: true }).setView([13.2, 101.2], 6);
// One canvas for every canvas-drawn layer (ThaiWater stations, GISTDA cells). Leaflet does not
// pass clicks down to a lower canvas, so stacked canvases left the lower layers' markers
// without popups. Draw order inside this canvas is managed by raiseStationLayers().
map.createPane("stations").style.zIndex = 450;
const stationRenderer = L.canvas({ padding: 0.3, pane: "stations" });
// Base maps sit at zIndex 0 so radar (zIndex 1) and traffic (2) always draw above them.
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
const BASEMAPS = {
  map: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, zIndex: 0, attribution: "© OpenStreetMap contributors",
  }),
  satellite: L.layerGroup([
    L.tileLayer(`${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`, {
      maxZoom: 19, maxNativeZoom: 18, zIndex: 0,
      attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
    }),
    // Place and boundary labels, since imagery alone has no names.
    L.tileLayer(`${ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`, {
      maxZoom: 19, maxNativeZoom: 18, zIndex: 0, attribution: "Labels © Esri",
    }),
  ]),
};
const trafficLayer = L.tileLayer("/api/v1/traffic/tiles/{z}/{x}/{y}.png", {
  maxZoom: 19, maxNativeZoom: 18, zIndex: 2, opacity: 0.85,
  attribution: 'Traffic © <a href="https://www.tomtom.com/" target="_blank" rel="noreferrer">TomTom</a>',
});
const storage = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } },
};
let activeBasemap = BASEMAPS[storage.get("basemap")] ? storage.get("basemap") : "map";
BASEMAPS[activeBasemap].addTo(map);

const MARKER_MODE_KEY = "marker-mode";
const state = {
  radarFrames: [], radarHost: "", radarLayer: null, radarVisibleLayer: null,
  damLayer: L.layerGroup(), floodPointLayer: L.layerGroup(), timer: null,
  damCount: 0, floodAllFeatures: [],
  radarTransitionId: 0, floodRequestId: 0, inspectRequestId: 0,
  areaForecastRequestId: 0, areaForecastController: null,
  markerByKey: new Map(), pendingFocusKey: null,
  // DPM river gauges that are also ThaiWater stations (backend links them as twin_id).
  dpmByTwin: new Map(), twDrawnIds: new Set(), twMarkerById: new Map(), pendingTwinFocus: null,
  // Default view: hide normal/unknown stations so the map isn't ~6,000 dots. "fp:<key>" and
  // "tw:<layer>:<id>" entries stay visible regardless (a station whose popup is open, or one
  // just focused from the priority list), even if its severity would otherwise be hidden.
  riskOnly: storage.get(MARKER_MODE_KEY) !== "all",
  forceVisible: new Set(),
};
const statusEl = document.querySelector("#connection");
const radarRange = document.querySelector("#radar-range");
const radarTime = document.querySelector("#radar-time");
const pointData = document.querySelector("#point-data");
const layerCounts = document.querySelector("#layer-counts");
const damCountEl = document.querySelector("#dam-count");
const alertCountEl = document.querySelector("#alert-count");
const radarSummaryEl = document.querySelector("#radar-summary");
const freshnessEl = document.querySelector("#data-freshness");
const refreshButton = document.querySelector("#refresh-all");
const sidebarButton = document.querySelector("#sidebar-toggle");
const toastEl = document.querySelector("#toast");
const priorityListEl = document.querySelector("#priority-list");
const priorityMetaEl = document.querySelector("#priority-meta");
const priorityObservedEl = document.querySelector("#priority-observed");
const mapAlertSummaryEl = document.querySelector("#map-alert-summary");
const priorityKpiEl = document.querySelector(".priority-kpi");
const alertCountLabelEl = document.querySelector("#alert-count-label");
const priorityScopeEl = document.querySelector("#priority-scope");
const localNewsSectionEl = document.querySelector("#local-news-section");
const localNewsProvinceEl = document.querySelector("#local-news-province");
const localNewsListEl = document.querySelector("#local-news-list");
const localSocialSectionEl = document.querySelector("#local-social-section");
const localSocialProvinceEl = document.querySelector("#local-social-province");
const localSocialListEl = document.querySelector("#local-social-list");
const damListEl = document.querySelector("#dam-list");
const damMetaEl = document.querySelector("#dam-meta");
const damObservedEl = document.querySelector("#dam-observed");
const damMoreButton = document.querySelector("#dam-more");
const mapRadarStatusEl = document.querySelector("#map-radar-status");
const mapLayerPanelEl = document.querySelector("#map-layer-panel");
const mapLayerToggleEl = document.querySelector("#map-layer-toggle");
const radarControlsEl = document.querySelector("#radar-controls");
const selectedSectionEl = document.querySelector("#selected-title").closest("section");
const prioritySectionEl = document.querySelector("#priority-title").closest("section");
const areaForecastFormEl = document.querySelector("#area-forecast-form");
const areaProvinceEl = document.querySelector("#area-province");
const areaDistrictEl = document.querySelector("#area-district");
const areaSubdistrictEl = document.querySelector("#area-subdistrict");
const areaForecastHoursEl = document.querySelector("#area-forecast-hours");
const areaForecastSubmitEl = document.querySelector("#area-forecast-submit");
const areaForecastResultEl = document.querySelector("#area-forecast-result");
const areaForecastStatusEl = document.querySelector("#area-forecast-status");
const severityColors = {
  normal: "#36c98f", low: "#9bd653", moderate: "#f1c84b",
  high: "#ff8a3d", critical: "#ff4d61", unknown: "#8999a6",
};
const severityLabels = {
  normal: "ปกติ", low: "เฝ้าระวังต่ำ", moderate: "เฝ้าระวัง",
  high: "สูง", critical: "วิกฤต", unknown: "ไม่มีข้อมูล",
};
// Severity marks from the SVG sprite: the shape changes with the level (ring -> triangle ->
// filled triangle -> octagon), so it reads without colour. Never reuse these for data types.
const SEVERITY_KEYS = new Set(["normal", "low", "moderate", "high", "critical", "unknown"]);
const severityIcon = (severity) => `<svg class="sev" aria-hidden="true"><use href="#sev-${SEVERITY_KEYS.has(severity) ? severity : "unknown"}"/></svg>`;
const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const severityOrder = { critical: 5, high: 4, moderate: 3, low: 2, normal: 1, unknown: 0 };
const RIVER_GAUGE_NOTE = "ระดับน้ำในลำน้ำ ไม่ใช่การยืนยันน้ำท่วมพื้นที่รอบสถานี";
// ThaiWater's own warning/critical levels for BKK canals sit well below the physical bank
// (pumping-control thresholds, unconfirmed) — the map's severity compares against the bank instead.
const CANAL_GAUGE_NOTE = "ระดับตามเกณฑ์แผนที่เทียบกับตลิ่งคลอง ไม่ใช่เกณฑ์เตือน/วิกฤตของ ThaiWater ด้านบน";

// Marker rules (risk levels, zoom rules, hidden-reason counts, clustering) live in rules.js so
// they can be unit-tested without a browser.
const { isRiskVisible, classify, layerCountText, severityBreakdown, clusterPoints } = window.Rules;

// Last load outcome per source; drives the header status (P1.1 / P1.4).
state.outcomes = new Map();
function outcome(source, result) {
  const status = result.meta?.status || (result.stale ? "stale" : "cached");
  return { source, ok: true, status, stale: status === "stale" };
}
function record(result) {
  if (result) state.outcomes.set(result.source, result);
  renderHealth();
  return result;
}
function failure(source, error) {
  console.warn(`${source} unavailable`, error);
  return record({ source, ok: false, status: "error", error: error.message });
}

async function getJSON(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

// The header status is a button: short text always visible (phones included), and a per-source
// list opens on click, so the state never depends on the dot colour alone.
const statusDetailEl = document.querySelector("#status-detail");
const STATUS_ROW = { ok: ["ปกติ", "sev-normal"], stale: ["ข้อมูลสำรอง", "sev-moderate"], error: ["ล้มเหลว", "sev-critical"] };

function renderHealth() {
  const active = [...state.outcomes.values()].filter((r) => !r.skipped);
  if (!active.length) return;
  const failed = active.filter((r) => !r.ok);
  const stale = active.filter((r) => r.ok && r.stale);
  const text = window.Rules.statusText(active.length, failed.length, stale.length);
  statusEl.classList.toggle("online", text.level === "online");
  statusEl.classList.toggle("degraded", text.level === "degraded");
  statusEl.querySelector(".status-long").textContent = text.long;
  statusEl.querySelector(".status-short").textContent = text.short;
  statusEl.setAttribute("aria-label", `สถานะแหล่งข้อมูล: ${text.long} กดเพื่อดูรายละเอียด`);
  statusDetailEl.querySelector("ul").innerHTML = active.map((r) => {
    const [label, mark] = STATUS_ROW[!r.ok ? "error" : r.stale ? "stale" : "ok"];
    return `<li class="${mark}"><svg class="sev" aria-hidden="true"><use href="#${mark}"/></svg><span>${escapeHtml(r.source)}</span><b>${label}</b></li>`;
  }).join("");
  freshnessEl.textContent = failed.length === active.length ? "ตรวจสอบไม่ได้"
    : failed.length || stale.length ? "ข้อมูลไม่ครบ"
      : `ตรวจล่าสุด ${new Date().toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" })}`;
}

function setStatusDetailOpen(open) {
  statusDetailEl.hidden = !open;
  statusEl.setAttribute("aria-expanded", String(open));
}
statusEl.addEventListener("click", () => setStatusDetailOpen(statusDetailEl.hidden));
document.addEventListener("click", (event) => {
  if (!statusDetailEl.hidden && !statusDetailEl.contains(event.target) && !statusEl.contains(event.target)) setStatusDetailOpen(false);
});

let toastTimer;
function showToast(text, tone = "ok") {
  toastEl.textContent = text;
  toastEl.classList.toggle("warn", tone === "warn");
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), 2200);
}

const isPhone = () => window.innerWidth <= 820;
function setMapLayersOpen(open) {
  mapLayerPanelEl.hidden = !open;
  mapLayerToggleEl.setAttribute("aria-expanded", String(open));
  mapLayerToggleEl.setAttribute("aria-label", `${open ? "ปิด" : "เปิด"}ชั้นภาพและตัวกรองบนแผนที่`);
  mapLayerToggleEl.classList.toggle("active", open);
  document.querySelector(".map-shell").classList.toggle("map-layers-open", open);
}
mapLayerToggleEl.addEventListener("click", () => setMapLayersOpen(mapLayerPanelEl.hidden));
document.addEventListener("click", (event) => {
  if (!mapLayerPanelEl.hidden && !mapLayerPanelEl.contains(event.target) && !mapLayerToggleEl.contains(event.target)) setMapLayersOpen(false);
});
// On phones the sidebar slides over the map. Closing it after picking an item remembers that
// item, so reopening the menu lands the keyboard/screen-reader focus back where the user was;
// the scroll position is kept because the panel is only moved, never re-rendered.
function setSidebarOpen(open, focusSection = null, { returnTo = null } = {}) {
  if (open) setMapLayersOpen(false);
  const wasOpen = document.body.classList.contains("sidebar-open");
  document.body.classList.toggle("sidebar-open", open);
  sidebarButton.setAttribute("aria-expanded", String(open));
  sidebarButton.setAttribute("aria-label", open ? "ปิดเมนู" : "เปิดเมนู");
  if (!open && returnTo) state.sidebarReturnTo = returnTo;
  if (open && focusSection) focusSection.scrollIntoView({ behavior: "smooth", block: "start" });
  if (isPhone() && open && !wasOpen) {
    const target = focusSection?.querySelector("h2") || (state.sidebarReturnTo?.isConnected ? state.sidebarReturnTo : null);
    setTimeout(() => target?.focus({ preventScroll: !focusSection }), 280);
  } else if (isPhone() && !open && wasOpen && document.activeElement?.closest("#sidebar")) {
    sidebarButton.focus();
  }
  setTimeout(() => map.invalidateSize(), 260);
}
// Shortcuts at the top of the sidebar: reach the forecast, layers and legend in one action
// (two on phones: open the menu, then the shortcut). Opens a folded group if the target is in one.
function jumpToSection(headingId, focusEl = null) {
  const heading = document.getElementById(headingId);
  if (!heading) return;
  if (isPhone() && !document.body.classList.contains("sidebar-open")) setSidebarOpen(true);
  const details = heading.closest("details");
  if (details && !details.open) details.open = true;
  heading.closest("section").scrollIntoView({ behavior: "smooth", block: "start" });
  (focusEl || heading).focus({ preventScroll: true });
}
for (const button of document.querySelectorAll("[data-jump]")) {
  button.addEventListener("click", () => jumpToSection(button.dataset.jump));
}
// Folded groups (ThaiWater layers, legend) remember whether the user opened them.
for (const details of document.querySelectorAll("details[data-remember]")) {
  const key = `open-${details.dataset.remember}`;
  const stored = storage.get(key);
  if (stored) details.open = stored === "1";
  details.addEventListener("toggle", () => storage.set(key, details.open ? "1" : "0"));
}

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!statusDetailEl.hidden) { setStatusDetailOpen(false); statusEl.focus(); return; }
  if (!mapLayerPanelEl.hidden) { setMapLayersOpen(false); mapLayerToggleEl.focus(); return; }
  if (isPhone() && document.body.classList.contains("sidebar-open")) setSidebarOpen(false);
});

function showRadar(index) {
  const frame = state.radarFrames[index];
  if (!frame) return;
  const transitionId = ++state.radarTransitionId;
  const oldLayer = state.radarVisibleLayer;
  const url = `${state.radarHost}${frame.path}/256/{z}/{x}/{y}/2/1_1.png`;
  const nextLayer = L.tileLayer(url, { opacity: 0, maxNativeZoom: 7, maxZoom: 19 });
  state.radarLayer = nextLayer;
  const frameTime = new Date(frame.time * 1000);
  radarTime.textContent = frameTime.toLocaleString("th-TH", { dateStyle: "short", timeStyle: "short" });
  radarSummaryEl.textContent = frameTime.toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" });
  mapRadarStatusEl.textContent = `เรดาร์ย้อนหลัง · ${frameTime.toLocaleString("th-TH", { dateStyle: "short", timeStyle: "short", timeZone: "Asia/Bangkok" })} น. (ไทย)`;

  if (!document.querySelector("#radar-toggle").checked) {
    nextLayer.setOpacity(0.58);
    if (oldLayer && map.hasLayer(oldLayer)) map.removeLayer(oldLayer);
    state.radarVisibleLayer = nextLayer;
    return;
  }

  nextLayer.once("load", () => {
    if (transitionId !== state.radarTransitionId) {
      if (map.hasLayer(nextLayer)) map.removeLayer(nextLayer);
      return;
    }
    requestAnimationFrame(() => {
      state.radarVisibleLayer = nextLayer;
      nextLayer.setOpacity(0.58);
      if (oldLayer && map.hasLayer(oldLayer)) oldLayer.setOpacity(0);
      setTimeout(() => {
        if (oldLayer && map.hasLayer(oldLayer)) map.removeLayer(oldLayer);
      }, 500);
    });
  });
  nextLayer.addTo(map);
}

async function loadRadar() {
  try {
    const result = await getJSON("/api/v1/radar/latest");
    state.radarHost = result.data.host;
    state.radarFrames = result.data.radar?.past || [];
    if (!state.radarFrames.length) {
      radarTime.textContent = "ไม่มีภาพ";
      radarSummaryEl.textContent = "—";
      mapRadarStatusEl.textContent = "เรดาร์ยังไม่มีภาพล่าสุด";
    }
    radarRange.max = Math.max(0, state.radarFrames.length - 1);
    radarRange.value = radarRange.max;
    updateRadarControls();
    showRadar(Number(radarRange.value));
    return record(outcome("เรดาร์ฝน", result));
  } catch (error) {
    mapRadarStatusEl.textContent = "โหลดเวลาเรดาร์ไม่สำเร็จ";
    updateRadarControls();
    return failure("เรดาร์ฝน", error);
  }
}

// ---------------------------------------------------------------- GISTDA satellite flood
const FLOOD_SOURCE = "ขอบเขตน้ำท่วม (GISTDA)";
// Below this zoom only tiles + a cell count load; per-cell impact is ~1 KB/cell, so detail
// waits until the view is small enough to stay under the backend's 3,000-cell cap.
const FLOOD_DETAIL_ZOOM = 11;
const FLOOD_WINDOW_LABELS = { "1day": "1 วัน", "3days": "3 วัน", "7days": "7 วัน", "30days": "30 วัน" };
const FLOOD_CAVEAT = "ตรวจจากดาวเทียมเรดาร์ ช้ากว่าเวลาจริงประมาณครึ่งถึงหนึ่งวัน และมองไม่เห็นน้ำขังในเขตเมือง";
const floodToggle = document.querySelector("#flood-toggle");
const floodWindowSelect = document.querySelector("#flood-window");
const floodFreqToggle = document.querySelector("#flood-freq-toggle");
const floodMetaEl = document.querySelector("#flood-meta");
const satellitePriorityEl = document.querySelector("#satellite-priority");

// GISTDA tiles are 512 px in XYZ order: zoomOffset -1 keeps them at their true scale.
function gistdaTiles(layer, options) {
  return L.tileLayer(`/api/v1/gistda/tiles/${layer}/{z}/{x}/{y}.png`, {
    tileSize: 512, zoomOffset: -1, minZoom: 1, maxZoom: 19, maxNativeZoom: 18,
    attribution: "Flood © GISTDA", ...options,
  });
}
state.floodTiles = gistdaTiles(`flood-${floodWindowSelect.value}`, { zIndex: 3, opacity: 0.85 });
state.floodFreqTiles = gistdaTiles("flood-freq", { zIndex: 1, opacity: 0.6 });
state.floodCells = L.geoJSON(null, {
  renderer: stationRenderer,
  // Tiles draw the colour; these near-invisible shapes only make cells hoverable/clickable.
  style: { stroke: false, fill: true, fillColor: "#ffffff", fillOpacity: 0.01 },
  bubblingMouseEvents: false,
  onEachFeature: (feature, layer) => {
    layer.bindPopup(() => floodCellPopup(feature.properties));
    layer.on("mouseover", () => layer.setStyle({ stroke: true, color: "#ffffff", weight: 1.5 }));
    layer.on("mouseout", () => layer.setStyle({ stroke: false }));
  },
});
state.floodCellRequestId = 0;

function floodCellPopup(p) {
  return `<div class="popup-title">น้ำท่วมจากดาวเทียม</div>
    <div class="popup-grid">
      <span>พื้นที่</span><b>${escapeHtml(p.subdistrict)} ${escapeHtml(p.district)}</b>
      <span>จังหวัด</span><b>${escapeHtml(p.province)}</b>
      <span>พื้นที่น้ำท่วม</span><b>${formatNumber(p.flood_rai, 1)} ไร่</b>
      <span>ประชากร</span><b>${formatNumber(p.population, 0)} คน</b>
      <span>อาคาร</span><b>${formatNumber(p.building, 0)}</b>
      <span>โรงพยาบาล / โรงเรียน</span><b>${formatNumber(p.hospital, 0)} / ${formatNumber(p.school, 0)}</b>
      <span>ถนน</span><b>${formatNumber(p.road_km, 2)} กม.</b>
      <span>นาข้าว</span><b>${formatNumber(p.rice_rai, 1)} ไร่</b>
      <span>ภาพดาวเทียมล่าสุด</span><b>${escapeHtml(formatObserved(p.observed_at))}${p.passes > 1 ? ` · ${p.passes} รอบ` : ""}</b>
    </div><div class="popup-note">${FLOOD_CAVEAT}</div>
    <div class="popup-source">GISTDA Disaster Platform · ช่อง H3 ${escapeHtml(p.h3)}</div>`;
}

function setFloodMeta(text, offline = false) {
  floodMetaEl.textContent = text;
  floodMetaEl.classList.toggle("offline", offline);
}

function markFloodNotConfigured() {
  state.floodNotConfigured = true;
  for (const input of [floodToggle, floodFreqToggle, floodWindowSelect]) input.disabled = true;
  floodToggle.checked = false;
  floodFreqToggle.checked = false;
  for (const selector of ['label[for="flood-toggle"]', 'label[for="flood-freq-toggle"]']) {
    const control = document.querySelector(selector);
    control.classList.add("is-unavailable");
    control.title = "ต้องตั้งค่า GISTDA_API_KEY ก่อน (ดู README)";
  }
  setFloodMeta("ยังไม่ได้เชื่อมต่อ GISTDA · ไม่ใช่การยืนยันว่าไม่มีน้ำท่วม");
  for (const layer of [state.floodTiles, state.floodFreqTiles, state.floodCells]) map.removeLayer(layer);
  renderSatellitePriority(null);
}

state.satelliteRenderId = 0;
function renderSatellitePriority(data, detail = false) {
  const renderId = ++state.satelliteRenderId;
  state.lastSatellite = [data, detail];
  satellitePriorityEl.replaceChildren();
  satellitePriorityEl.hidden = !data;
  if (!data) return;
  const heading = document.createElement("div");
  heading.className = "priority-group";
  heading.innerHTML = `น้ำท่วมจากดาวเทียม (GISTDA)<span>${detail ? `${data.areas.length}${data.areas.length >= 10 ? "+" : ""} ตำบล` : ""}</span>`;
  satellitePriorityEl.append(heading);
  if (!detail || !data.areas.length) {
    const empty = document.createElement("p");
    empty.className = "priority-empty";
    empty.textContent = !detail
      ? `ซูมเข้า (ระดับ ${FLOOD_DETAIL_ZOOM} ขึ้นไป) เพื่อดูตำบลที่ได้รับผลกระทบ`
      : "ไม่พบน้ำท่วมจากดาวเทียมในพื้นที่ที่เห็น · ดาวเทียมมองไม่เห็นน้ำขังในเขตเมือง จึงไม่ยืนยันว่าไม่มีน้ำขัง";
    satellitePriorityEl.append(empty);
    return;
  }
  for (const area of data.areas.slice(0, 3)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "priority-item";
    button.style.setProperty("--marker", "#3f7fd9");
    const impact = [
      area.hospital ? `รพ. ${area.hospital}` : "", area.school ? `โรงเรียน ${area.school}` : "",
      `ประชากร ~${formatNumber(area.population, 0)}`,
    ].filter(Boolean).join(" · ");
    button.innerHTML = `<span class="priority-badge data-type" aria-hidden="true">${icon("satellite")}</span>
      <span class="priority-copy"><strong>${escapeHtml(area.subdistrict)} ${escapeHtml(area.district)}</strong>
      <small>${escapeHtml(area.province)} · ${formatNumber(area.flood_rai, 0)} ไร่ · ${impact}</small>
      <small class="tmd-rain" hidden></small></span>
      <span class="priority-level">${formatNumber(area.cells, 0)} ช่อง</span>`;
    button.setAttribute("aria-label", `${area.subdistrict} ${area.district} น้ำท่วมจากดาวเทียม ${formatNumber(area.flood_rai, 0)} ไร่ กดเพื่อดูบนแผนที่`);
    if (area.center) {
      button.addEventListener("click", () => {
        setSidebarOpen(false);
        map.flyTo([area.center[1], area.center[0]], Math.max(map.getZoom(), 13), { duration: .65 });
      });
    }
    satellitePriorityEl.append(button);
    addTambonForecast(button, area, renderId);
  }
}

// "Flooded now + more rain coming" is the strongest waterlogging signal we can show.
async function addTambonForecast(button, area, renderId) {
  if (!state.tmdConfigured) return;
  const params = new URLSearchParams({ province: area.province, district: area.district, subdistrict: area.subdistrict });
  try {
    const f = (await getJSON(`/api/v1/forecast/tambon?${params}`)).data;
    if (renderId !== state.satelliteRenderId || !f.found) return;
    const slot = button.querySelector(".tmd-rain");
    const heavy = f.rain_mm > 35;
    slot.textContent = f.rain_mm > 0
      ? `พยากรณ์ฝน 24 ชม. ${formatNumber(f.rain_mm, 1)} มม. (${f.rain_class})${heavy ? " · น้ำท่วมอยู่ + ฝนหนัก" : ""}`
      : "พยากรณ์ 24 ชม. ไม่มีฝน";
    slot.hidden = false;
    slot.classList.toggle("heavy", heavy);
    button.classList.toggle("rain-heavy", heavy);
  } catch (error) {
    console.warn("TMD tambon forecast unavailable", error);
  }
}

state.satelliteRequestId = 0;
// Province scope: sub-district summary for the whole province (no geometry), zoom-independent.
async function loadSatellitePriority() {
  const requestId = ++state.satelliteRequestId;
  if (!state.scope) { loadFlood(); return; }
  if (state.floodNotConfigured || !floodToggle.checked) { renderSatellitePriority(null); return; }
  const params = new URLSearchParams({ province: state.scope, window: floodWindowSelect.value, detail: true, areas_only: true });
  try {
    const result = await getJSON(`/api/v1/flood/current?${params}`);
    if (requestId !== state.satelliteRequestId || !state.scope) return;
    renderSatellitePriority(result.data, true);
  } catch (error) {
    if (requestId !== state.satelliteRequestId) return;
    console.warn("GISTDA province summary unavailable", error);
    renderSatellitePriority(null);
  }
}

async function loadFlood() {
  const requestId = ++state.floodCellRequestId;
  if (state.floodNotConfigured) return record({ source: FLOOD_SOURCE, skipped: true });
  if (!floodToggle.checked) {
    state.outcomes.delete(FLOOD_SOURCE);
    return null;
  }
  const floodWindow = floodWindowSelect.value;
  const detail = map.getZoom() >= FLOOD_DETAIL_ZOOM;
  const b = map.getBounds();
  const params = new URLSearchParams({
    min_lon: b.getWest(), min_lat: b.getSouth(), max_lon: b.getEast(), max_lat: b.getNorth(), window: floodWindow, detail,
  });
  try {
    const result = await getJSON(`/api/v1/flood/current?${params}`);
    if (requestId !== state.floodCellRequestId) return null;
    if (result.meta?.status === "not_configured") {
      markFloodNotConfigured();
      return record({ source: FLOOD_SOURCE, skipped: true });
    }
    const data = result.data;
    if (!map.hasLayer(state.floodTiles)) state.floodTiles.addTo(map);
    state.floodCells.clearLayers();
    if (detail) {
      state.floodCells.addData(data.features);
      state.floodCells.bringToBack();  // under the station markers that share the canvas
      if (!map.hasLayer(state.floodCells)) state.floodCells.addTo(map);
    }
    const windowLabel = FLOOD_WINDOW_LABELS[floodWindow];
    const total = formatNumber(data.total_matched, 0);
    const text = !data.total_matched
      ? `ไม่พบในจอ (${windowLabel}) · ไม่ใช่การยืนยันว่าไม่มีน้ำท่วม`
      : detail
        ? `${data.truncated ? `แสดง ${formatNumber(data.features.length, 0)}/${total}` : total} ช่องในจอ · ภาพล่าสุด ${formatObserved(data.observed_at)}`
        : `${total} ช่องในจอ (${windowLabel}) · ประมวลผล ${formatObserved(data.published_at)}`;
    setFloodMeta(result.stale ? `${text} · cache` : text);
    floodMetaEl.title = FLOOD_CAVEAT;
    if (!state.scope) renderSatellitePriority(data, detail);
    return record(outcome(FLOOD_SOURCE, result));
  } catch (error) {
    if (requestId !== state.floodCellRequestId) return null;
    setFloodMeta("โหลดไม่สำเร็จ", true);
    return failure(FLOOD_SOURCE, error);
  }
}

function formatNumber(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("th-TH", { maximumFractionDigits: digits });
}

function formatObserved(value) {
  if (!value) return "ไม่ระบุเวลา";
  const localTime = String(value).replace(" ", "T");
  const hasClock = /T\d\d:\d\d/.test(localTime);
  const dateTime = hasClock ? localTime : `${localTime}T00:00:00`;
  const parsed = new Date(`${dateTime}${/Z$|[+-]\d\d:\d\d$/.test(dateTime) ? "" : "+07:00"}`);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString("th-TH", {
    timeZone: "Asia/Bangkok", day: "numeric", month: "short",
    ...(hasClock ? { hour: "2-digit", minute: "2-digit" } : {}),
  });
}

function escapeHtml(value) {
  return String(value ?? "—").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[char]);
}

// Point-layer counts: each layer's own line says how many of the stations loaded for this view are
// drawn and why the rest are hidden (see Rules.classify). The section header sums the layers that
// are switched on; these are map markers, not the KPI's de-duplicated alert count.
const POINT_LAYER_TOGGLES = {
  "flood-points": "#flood-point-toggle", "water-level": "#tw-water-level-toggle", rain: "#tw-rain-toggle",
  canal: "#tw-canal-toggle", watergate: "#tw-watergate-toggle",
};
state.pointCounts = {};  // layer -> Rules.classify() result for the last render
function updateLayerCounts() {
  let shown = 0, hidden = 0, off = 0;
  for (const [layer, toggle] of Object.entries(POINT_LAYER_TOGGLES)) {
    if (!document.querySelector(toggle).checked) { off += 1; continue; }
    const c = state.pointCounts[layer];
    if (!c) continue;
    shown += c.shown.length;
    hidden += c.loaded - c.shown.length;
  }
  layerCounts.textContent = `หมุดสถานีในจอ: แสดง ${formatNumber(shown, 0)} · ซ่อน ${formatNumber(hidden, 0)}${off ? ` · ปิด ${off} ชั้น` : ""}`;
  layerCounts.title = "นับหมุดของชั้นสถานีที่เปิดอยู่ ในกรอบแผนที่ปัจจุบัน · รายละเอียดเหตุที่ซ่อนดูใต้ชื่อแต่ละชั้น";
  const failed = state.outcomes.get("จุดเฝ้าระวัง ปภ.")?.ok === false;
  const c = state.pointCounts["flood-points"];
  floodPointMetaEl.classList.toggle("offline", failed);
  floodPointMetaEl.textContent = !document.querySelector("#flood-point-toggle").checked ? "ปิดอยู่ · ไม่นับ"
    : failed ? "โหลดไม่สำเร็จ" : c ? layerCountText(c) : "กำลังโหลด…";
  floodPointMetaEl.title = c?.risk.length ? `ต่ำกว่าเกณฑ์: ${severityBreakdown(c.risk)}` : "";
  if (state.outcomes.get("เขื่อน")?.ok) { damCountEl.textContent = formatNumber(state.damCount, 0); damCountEl.title = ""; }
}
const floodPointMetaEl = document.querySelector("#flood-point-meta");

// Dams at or above this storage keep their % label at every zoom; the rest show it zoomed in.
const DAM_LABEL_ALWAYS = 80;
const DAM_DETAIL_ZOOM = 9;

function damPercentText(percent) {
  if (percent == null) return "—";
  return percent > 100 ? "100%+" : `${formatNumber(percent, 0)}%`;
}

function damIcon(p) {
  const percent = p.percent_storage;
  const label = percent == null ? "" : `<div class="dam-label${percent >= DAM_LABEL_ALWAYS ? "" : " minor"}${percent > 100 ? " over" : ""}">
      <span>${damPercentText(percent)}${damTrend(p).symbol}</span>
      <small>${escapeHtml(p.name)}</small>
      <small>${formatNumber(p.volume, 0)}/${formatNumber(p.storage, 0)} ล้าน ลบ.ม.</small></div>`;
  return L.divIcon({
    className: "dam-icon",
    html: `<div class="dam-pin">${icon("dam")}</div>${label}`,
    iconSize: [30, 30], iconAnchor: [15, 28], popupAnchor: [0, -27], tooltipAnchor: [16, -14],
  });
}

function floodPointIcon(severity) {
  const color = severityColors[severity] || severityColors.unknown;
  return L.divIcon({
    className: "flood-point-icon",
    html: `<div class="flood-pin" style="--marker:${color}">${severityIcon(severity)}</div>`,
    iconSize: [30, 30], iconAnchor: [15, 28], popupAnchor: [0, -27],
  });
}

function floodPointKey(feature) {
  const p = feature.properties || {};
  const [lon, lat] = feature.geometry.coordinates;
  return `${p.point_type}:${p.id || p.name}:${lon.toFixed(5)}:${lat.toFixed(5)}`;
}

// One marker per physical station: a DPM river gauge is not drawn while ThaiWater draws its twin.
// The twin rule is part of renderFloodPoints() (it is one of the counted hidden reasons), so a
// change on the ThaiWater side just re-renders the DPM pins from the data already loaded.
function syncTwinMarkers() {
  renderFloodPoints();
}

const SEVERITY_SOURCE_LABELS = { thaiwater: "ThaiWater (ใหม่กว่า)", dpm: "ปภ. / กรมชลประทาน" };

// Shown in the DPM popup: the ThaiWater reading of the same station.
function twinSection(p) {
  const t = p.twin;
  return `<div class="popup-subtitle">สถานีเดียวกันใน ThaiWater (สสน.)</div>
    <div class="popup-grid">
      <span>ชื่อสถานี</span><b>${escapeHtml(t.name)}</b>
      <span>ระดับตามเกณฑ์</span><b>${severityLabels[t.severity] || severityLabels.unknown}</b>
      <span>ระดับน้ำ</span><b>${withUnit(t.water_level_msl, "ม.รทก.")}</b>
      <span>% ความจุลำน้ำ</span><b>${withUnit(t.storage_percent, "%", 1)}</b>
      <span>เวลา</span><b>${escapeHtml(t.observed_at)}</b>
      <span>ใช้จัดลำดับเตือน</span><b>${SEVERITY_SOURCE_LABELS[p.severity_source] || "—"}</b>
    </div>`;
}

// Shown in the ThaiWater popup: the DPM/RID reading of the same station.
function dpmSection(d) {
  return `<div class="popup-subtitle">สถานีเดียวกันใน ปภ. / กรมชลประทาน</div>
    <div class="popup-grid">
      <span>ชื่อสถานี</span><b>${escapeHtml(d.name)}</b>
      <span>ระดับตามเกณฑ์</span><b>${severityLabels[d.dpm_severity] || severityLabels.unknown}</b>
      <span>ห่างจากตลิ่ง</span><b>${withUnit(d.bank_clearance_m, "ม.")}</b>
      <span>เวลา</span><b>${escapeHtml(d.dpm_observed_at)}</b>
      <span>ใช้จัดลำดับเตือน</span><b>${SEVERITY_SOURCE_LABELS[d.severity_source] || "—"}</b>
    </div>`;
}

// `trigger` is the list button: on phones the menu reopens with focus back on it.
function focusPriority(feature, trigger = null) {
  loadNewsForProvince(feature.properties.province);
  const [lon, lat] = feature.geometry.coordinates;
  const key = floodPointKey(feature);
  const toggle = document.querySelector("#flood-point-toggle");
  if (!toggle.checked) {
    toggle.checked = true;
    state.floodPointLayer.addTo(map);
  }
  const twinId = feature.properties.tw_id;
  const twToggle = document.querySelector(THAIWATER_LAYERS["water-level"].toggle);
  if (feature.properties.origin === "thaiwater" && !twToggle.checked) {
    twToggle.checked = true;  // ThaiWater-only station: its layer is the only place it is drawn
    twToggle.dispatchEvent(new Event("change"));
  }
  if (twinId && twToggle.checked) {
    // Drawn by ThaiWater (a DPM twin is hidden under it); reopen after the post-flyTo reload.
    // Stays visible even if risk-only mode would otherwise hide its severity.
    state.forceVisible.add(`tw:water-level:${twinId}`);
    state.pendingTwinFocus = twinId;
    state.twMarkerById.get(twinId)?.openPopup();
  } else {
    state.forceVisible.add(`fp:${key}`);
    state.pendingFocusKey = key;
    // Drawn inside a group (or hidden) right now: redraw so it stands on its own.
    if (state.markerByKey.has(key)) state.markerByKey.get(key).openPopup(); else renderFloodPoints();
  }
  setSidebarOpen(false, null, { returnTo: trigger });
  // The list stays as it was while we fly to the station; the next map move by the user refreshes it.
  state.keepPriorityOnce = true;
  map.flyTo([lat, lon], Math.max(map.getZoom(), 10), { duration: .65 });
}

// ---------------------------------------------------------------- priority list (by view or province)
const SCOPE_STORAGE_KEY = "priority-scope";
state.alertsRequestId = 0;
state.provinces = [];
state.scope = storage.get(SCOPE_STORAGE_KEY) || "";
state.newsRequestId = 0;
state.newsProvince = "";
state.newsLoadedAt = 0;
state.newsLoading = false;
state.socialRequestId = 0;
state.socialProvince = "";
state.socialLoadedAt = 0;
state.socialLoading = false;

async function loadSocialForProvince(province) {
  if (!province) {
    ++state.socialRequestId;
    state.socialProvince = "";
    state.socialLoading = false;
    localSocialSectionEl.hidden = true;
    return;
  }
  localSocialSectionEl.hidden = false;
  if (state.socialProvince === province && (state.socialLoading || Date.now() - state.socialLoadedAt < 5 * 60_000)) return;
  const requestId = ++state.socialRequestId;
  state.socialProvince = province;
  state.socialLoading = true;
  localSocialProvinceEl.textContent = `จ.${province}`;
  localSocialListEl.replaceChildren();
  const loading = document.createElement("p");
  loading.className = "priority-empty";
  loading.textContent = "กำลังตรวจคลิปข่าวล่าสุด…";
  localSocialListEl.append(loading);
  try {
    const result = await getJSON(`/api/v1/local-social?province=${encodeURIComponent(province)}`);
    if (requestId !== state.socialRequestId) return;
    state.socialLoading = false;
    state.socialLoadedAt = Date.now();
    localSocialListEl.replaceChildren();
    for (const item of result.data.items) {
      const url = new URL(item.url);
      if (url.protocol !== "https:" || url.hostname !== "www.youtube.com" || url.pathname !== "/watch" ||
          !/^[A-Za-z0-9_-]{11}$/.test(url.searchParams.get("v") || "")) continue;
      const link = document.createElement("a");
      link.href = url.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.className = "local-news-item";
      const title = document.createElement("strong");
      title.textContent = item.title;
      const meta = document.createElement("small");
      meta.textContent = `News NBT2HD · ${formatObserved(item.published_at)} · ระบุจังหวัดใน${item.match_field === "title" ? "ชื่อคลิป" : "คำอธิบาย"}`;
      const platform = document.createElement("small");
      platform.className = "local-social-platform";
      platform.textContent = "▶ เปิดคลิปบน YouTube";
      link.append(title, meta, platform);
      localSocialListEl.append(link);
    }
    if (!localSocialListEl.childElementCount) {
      const empty = document.createElement("p");
      empty.className = "priority-empty";
      empty.textContent = "ไม่พบคลิปที่ระบุจังหวัดนี้ในรายการอัปโหลดล่าสุด · ไม่ได้หมายความว่าไม่มีเหตุการณ์";
      localSocialListEl.append(empty);
    }
    if (result.stale) localSocialProvinceEl.textContent = `จ.${province} · ข้อมูลสำรอง`;
  } catch (error) {
    if (requestId !== state.socialRequestId) return;
    state.socialLoading = false;
    state.socialLoadedAt = Date.now();
    localSocialListEl.replaceChildren();
    const message = document.createElement("p");
    message.className = "priority-empty";
    message.textContent = "โหลดคลิปข่าวไม่สำเร็จ · ไม่ทราบว่ามีคลิปใหม่หรือไม่";
    localSocialListEl.append(message);
    console.warn("local social unavailable", error);
  }
}

async function loadNewsForProvince(province) {
  loadSocialForProvince(province);
  if (!province) {
    ++state.newsRequestId;
    state.newsProvince = "";
    state.newsLoading = false;
    localNewsSectionEl.hidden = true;
    return;
  }
  localNewsSectionEl.hidden = false;
  if (state.newsProvince === province && (state.newsLoading || Date.now() - state.newsLoadedAt < 5 * 60_000)) return;
  const requestId = ++state.newsRequestId;
  state.newsProvince = province;
  state.newsLoading = true;
  localNewsProvinceEl.textContent = `จ.${province}`;
  localNewsListEl.replaceChildren();
  const loading = document.createElement("p");
  loading.className = "priority-empty";
  loading.textContent = "กำลังตรวจข่าวล่าสุด…";
  localNewsListEl.append(loading);
  try {
    const result = await getJSON(`/api/v1/local-news?province=${encodeURIComponent(province)}`);
    if (requestId !== state.newsRequestId) return;
    state.newsLoading = false;
    localNewsListEl.replaceChildren();
    state.newsLoadedAt = Date.now();
    for (const item of result.data.items) {
      const link = document.createElement("a");
      const url = new URL(item.url);
      if (url.protocol !== "https:" || !url.hostname.endsWith(".prd.go.th")) continue;
      link.href = url.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.className = "local-news-item";
      const title = document.createElement("strong");
      title.textContent = item.title;
      const meta = document.createElement("small");
      meta.textContent = `กรมประชาสัมพันธ์ · ${formatObserved(item.published_at)} · จับคู่ระดับจังหวัด`;
      link.append(title, meta);
      localNewsListEl.append(link);
    }
    if (!localNewsListEl.childElementCount) {
      const empty = document.createElement("p");
      empty.className = "priority-empty";
      empty.textContent = "ไม่พบข่าวน้ำหรือฝนในรายการล่าสุดที่ระบุจังหวัดนี้ · ไม่ได้หมายความว่าพื้นที่ปลอดภัย";
      localNewsListEl.append(empty);
    }
    if (result.stale) localNewsProvinceEl.textContent = `จ.${province} · ข่าวสำรอง`;
  } catch (error) {
    if (requestId !== state.newsRequestId) return;
    state.newsLoading = false;
    state.newsLoadedAt = Date.now();
    localNewsListEl.replaceChildren();
    const message = document.createElement("p");
    message.className = "priority-empty";
    message.textContent = "โหลดข่าวไม่สำเร็จ · ตรวจจากแหล่งทางการโดยตรง";
    localNewsListEl.append(message);
    console.warn("local news unavailable", error);
  }
}

function scopeLabel() {
  return state.scope ? `ใน จ.${state.scope}` : "ในจอ";
}

function setKpiNote(text, tone = "") {
  priorityObservedEl.textContent = text;
  priorityObservedEl.classList.toggle("warn", tone === "warn");
  priorityObservedEl.classList.toggle("error", tone === "error");
}

function renderPriority(alerts, observedAt, stale) {
  const n = alerts.length;
  alertCountEl.textContent = formatNumber(n, 0);
  alertCountLabelEl.textContent = window.Rules.kpiLabel(state.scope);
  priorityKpiEl.classList.toggle("has-alerts", n > 0);
  priorityListEl.setAttribute("aria-busy", "false");
  priorityMetaEl.textContent = `${n} จุด${scopeLabel()}`;
  // Time and fallback state sit right under the KPI number; 0 never reads as "safe".
  setKpiNote([
    observedAt ? `ข้อมูลสถานี ${formatObserved(observedAt)}` : "ไม่ทราบเวลาข้อมูลสถานี",
    stale ? "ข้อมูลสำรอง (อาจไม่ใช่ล่าสุด)" : "",
    n ? "" : "ไม่พบจุดถึงเกณฑ์ · ไม่ยืนยันว่าปลอดภัย",
  ].filter(Boolean).join(" · "), stale ? "warn" : "");
  mapAlertSummaryEl.textContent = `${n} จุดเตือน${scopeLabel()}`;
  mapAlertSummaryEl.classList.toggle("has-alerts", n > 0);
  mapAlertSummaryEl.setAttribute("aria-label", `${n} จุดเตือน${scopeLabel()} เปิดรายการจุดเตือน`);
  priorityListEl.replaceChildren();
  if (!n) {
    const empty = document.createElement("p");
    empty.className = "priority-empty";
    empty.innerHTML = `${icon("pin-alert")}<span>ไม่พบสถานีถึงระดับเตือน${state.scope ? `ใน จ.${escapeHtml(state.scope)}` : "ในพื้นที่ที่เห็น"} · ข้อมูลนี้ไม่ยืนยันว่าพื้นที่ปลอดน้ำท่วม</span>`;
    priorityListEl.append(empty);
    return;
  }
  // A province is a bounded work list, so show more of it than the pan-driven view.
  const perGroup = state.scope ? 5 : 3;
  for (const [type, label] of [["road_flood", "น้ำขังถนน"], ["river_gauge", "ระดับน้ำแม่น้ำ"]]) {
    const group = alerts.filter((feature) => feature.properties.point_type === type);
    if (!group.length) continue;
    const heading = document.createElement("div");
    heading.className = "priority-group";
    heading.innerHTML = `${label}<span>${group.length > perGroup ? `${perGroup} จาก ` : ""}${group.length} จุด</span>`;
    priorityListEl.append(heading);
    for (const feature of group.slice(0, perGroup)) {
      const p = feature.properties;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "priority-item";
      button.style.setProperty("--marker", severityColors[p.severity]);
      button.innerHTML = `<span class="priority-badge" aria-hidden="true">${severityIcon(p.severity)}</span>
        <span class="priority-copy"><strong>${escapeHtml(p.name)}</strong>
        <small>${escapeHtml(p.province)} · ${escapeHtml(formatObserved(p.observed_at))}${p.origin === "thaiwater" ? " · ThaiWater" : ""}</small></span>
        <span class="priority-level">${severityLabels[p.severity]}</span>`;
      button.setAttribute("aria-label", `${p.name} ${label} ระดับ${severityLabels[p.severity]} กดเพื่อดูบนแผนที่`);
      button.addEventListener("click", () => focusPriority(feature, button));
      priorityListEl.append(button);
    }
  }
}

// Provinces with alerts first (most alerts on top), then the rest alphabetically.
// ---------------------------------------------------------------- TMD rain forecast by province
const forecastLineEl = document.querySelector("#forecast-line");
state.tmdConfigured = false;
state.provinceForecast = new Map();  // name -> [{date, rain_mm, rain_class}]
state.lastByProvince = null;
const RAIN_WORTH_NOTING = 10;  // mm/day: TMD "ปานกลาง" starts above 10

function forecastDayLabel(date, index) {
  if (index === 0) return "วันนี้";
  if (index === 1) return "พรุ่งนี้";
  return formatObserved(date);
}

function renderForecastLine() {
  forecastLineEl.replaceChildren();
  forecastLineEl.hidden = !state.tmdConfigured || !state.provinceForecast.size;
  if (forecastLineEl.hidden) return;
  const label = document.createElement("span");
  label.className = "forecast-label";
  if (state.scope) {
    const days = state.provinceForecast.get(state.scope) || [];
    label.textContent = "พยากรณ์ฝน (กรมอุตุฯ)";
    forecastLineEl.append(label, ...days.map((day, i) => {
      const chip = document.createElement("span");
      chip.className = `forecast-chip${day.rain_mm > 35 ? " heavy" : ""}`;
      chip.textContent = `${forecastDayLabel(day.date, i)} ${formatNumber(day.rain_mm ?? 0, 1)} มม.${day.rain_mm > RAIN_WORTH_NOTING ? ` (${day.rain_class})` : ""}`;
      return chip;
    }));
    return;
  }
  // View mode: which provinces should get attention tomorrow, one click away.
  const tomorrow = [...state.provinceForecast].map(([name, days]) => ({ name, day: days[1] }))
    .filter((p) => p.day?.rain_mm > RAIN_WORTH_NOTING).sort((a, b) => b.day.rain_mm - a.day.rain_mm).slice(0, 4);
  label.textContent = tomorrow.length ? "พยากรณ์ฝนพรุ่งนี้ (กรมอุตุฯ)" : "พยากรณ์พรุ่งนี้ (กรมอุตุฯ): ไม่มีจังหวัดที่ฝนเกิน 10 มม.";
  forecastLineEl.append(label, ...tomorrow.map((p) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `forecast-chip${p.day.rain_mm > 35 ? " heavy" : ""}`;
    chip.textContent = `${p.name} ${formatNumber(p.day.rain_mm, 0)} มม.`;
    chip.title = `ฝน${p.day.rain_class} · กดเพื่อเลือก จ.${p.name}`;
    chip.addEventListener("click", () => { priorityScopeEl.value = p.name; setScope(p.name); });
    return chip;
  }));
}

async function loadProvinceForecast() {
  try {
    const result = await getJSON("/api/v1/forecast/provinces");
    state.tmdConfigured = result.meta?.status !== "not_configured";
    state.provinceForecast = new Map((result.data.provinces || []).map((p) => [p.name, p.days]));
  } catch (error) {
    console.warn("TMD province forecast unavailable", error);
    state.provinceForecast = new Map();
  }
  renderForecastLine();
  if (state.lastByProvince && state.provinces.length) renderScopeOptions(state.lastByProvince);
  // The satellite list may have rendered before we knew TMD was configured.
  if (state.tmdConfigured && state.lastSatellite?.[0]) renderSatellitePriority(...state.lastSatellite);
}

// ---------------------------------------------------------------- TMD hourly rain by administrative area
function resetAreaSelect(select, placeholder) {
  select.innerHTML = `<option value="">${placeholder}</option>`;
  select.disabled = true;
}

function selectedAreaCode() {
  return areaSubdistrictEl.value || areaDistrictEl.value || areaProvinceEl.value;
}

function updateAreaForecastButton() {
  areaForecastSubmitEl.disabled = !selectedAreaCode();
}

function resetAreaForecastResult() {
  state.areaForecastController?.abort();
  state.areaForecastController = null;
  state.areaForecastRequestId += 1;
  areaForecastResultEl.className = "metrics empty-state area-forecast-result";
  areaForecastResultEl.innerHTML = `<span class="empty-icon" aria-hidden="true">${icon("rain")}</span>
    <strong>เลือกพื้นที่แล้วกดดูพยากรณ์</strong>
    <small>ค่าพยากรณ์ ณ จุดอ้างอิงของพื้นที่ ไม่ใช่ค่าเฉลี่ยทั้งพื้นที่</small>`;
  areaForecastStatusEl.textContent = "TMD · รายชั่วโมง";
}

async function loadAreaChoices(parentCode, select, placeholder) {
  select.disabled = true;
  select.innerHTML = '<option value="">กำลังโหลด…</option>';
  try {
    const params = parentCode ? `?parent_code=${encodeURIComponent(parentCode)}` : "";
    const result = await getJSON(`/api/v1/areas${params}`);
    select.innerHTML = `<option value="">${placeholder}</option>${result.areas.map((area) =>
      `<option value="${escapeHtml(area.code)}">${escapeHtml(area.name)}</option>`).join("")}`;
    select.disabled = false;
  } catch (error) {
    select.innerHTML = '<option value="">โหลดรายการไม่สำเร็จ</option>';
    console.warn("administrative area list unavailable", error);
  }
  updateAreaForecastButton();
}

function renderAreaForecast(result) {
  const data = result.data || {};
  if (!data.found) {
    const message = data.source_status === "not_configured"
      ? "ยังไม่ได้ตั้งค่า TMD API key"
      : data.mapping_mismatch
        ? "รหัสพื้นที่จาก TMD ไม่ตรงทะเบียน จึงไม่แสดงผล"
        : "TMD ไม่พบพยากรณ์ของพื้นที่นี้";
    areaForecastResultEl.className = "metrics empty-state area-forecast-result";
    areaForecastResultEl.innerHTML = `<strong>${escapeHtml(message)}</strong><small>ลองเลือกระดับพื้นที่อื่นหรือลองใหม่ภายหลัง</small>`;
    areaForecastStatusEl.textContent = data.mapping_mismatch ? "ตรวจสอบ mapping" : "ไม่มีข้อมูล";
    return;
  }
  const rows = data.hours || [];
  const summary = data.summary || {};
  const values = rows.map((row) => row.rain_mm).filter(Number.isFinite);
  const scale = Math.max(1, ...values);
  const bars = rows.map((row) => {
    const missing = !Number.isFinite(row.rain_mm);
    const height = missing ? 3 : Math.max(1, Math.round((row.rain_mm / scale) * 100));
    const label = `${formatObserved(row.time)} · ${missing ? "ไม่มีข้อมูล" : `${formatNumber(row.rain_mm, 1)} มม.`}`;
    return `<i class="${missing ? "missing" : ""}" style="--h:${height}%" tabindex="0" role="img" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}"></i>`;
  }).join("");
  const area = data.area || {};
  const areaName = [area.province, area.district, area.subdistrict].filter(Boolean).join(" › ");
  const point = data.reference_point || {};
  const status = result.meta?.status;
  const caveat = data.partial
    ? `ข้อมูลไม่ครบ: ได้ ${summary.hours_returned || 0}/${summary.hours_requested || 0} ชั่วโมง`
    : "ค่าจากแบบจำลอง ไม่ใช่ฝนที่ตรวจวัดจริง";
  areaForecastResultEl.className = "metrics area-forecast-result";
  areaForecastResultEl.innerHTML = `
    <strong>${escapeHtml(areaName)}</strong> <span class="tag">TMD · พยากรณ์</span>
    <div class="bars" aria-label="กราฟฝนรายชั่วโมง ${summary.hours_returned || 0} ชั่วโมง">${bars}</div>
    <div class="bars-axis"><span>${rows[0] ? thHour(rows[0].time) : "—"}</span><span>+${Math.floor((summary.hours_returned || 0) / 2)} ชม.</span><span>${rows.at(-1) ? thHour(rows.at(-1).time) : "—"}</span></div>
    <div class="area-rain-summary">
      <span>ฝนสะสมช่วงที่แสดง<b>${formatNumber(summary.total_rain_mm, 1)} มม.</b></span>
      <span>สูงสุดรายชั่วโมง<b>${formatNumber(summary.peak_rain_mm, 1)} มม.</b></span>
    </div>
    <div class="area-forecast-note">${escapeHtml(caveat)}${summary.peak_at && summary.peak_rain_mm > 0 ? ` · สูงสุด ${escapeHtml(formatObserved(summary.peak_at))}` : ""}<br>
      จุดอ้างอิง ${formatNumber(point.lat, 4)}, ${formatNumber(point.lon, 4)} · ดึงข้อมูล ${escapeHtml(formatObserved(result.meta?.fetched_at))}</div>
    ${state.scope && area.province && area.province !== state.scope ? `<div class="area-forecast-note scope-mismatch">พยากรณ์นี้เป็นของ จ.${escapeHtml(area.province)} คนละจังหวัดกับรายการจุดเตือน (จ.${escapeHtml(state.scope)})</div>` : ""}`;
  areaForecastStatusEl.textContent = status === "stale" ? "ข้อมูลสำรอง" : data.partial ? "ข้อมูลไม่ครบ" : `${summary.hours_returned} ชั่วโมง`;
}

areaProvinceEl.addEventListener("change", async () => {
  areaPrefillNoteEl.hidden = true;
  resetAreaSelect(areaDistrictEl, "เลือกจังหวัดก่อน");
  resetAreaSelect(areaSubdistrictEl, "เลือกอำเภอก่อน");
  resetAreaForecastResult();
  updateAreaForecastButton();
  if (areaProvinceEl.value) await loadAreaChoices(areaProvinceEl.value, areaDistrictEl, "ไม่เลือก (ใช้ระดับจังหวัด)");
});

areaDistrictEl.addEventListener("change", async () => {
  resetAreaSelect(areaSubdistrictEl, "เลือกอำเภอก่อน");
  resetAreaForecastResult();
  updateAreaForecastButton();
  if (areaDistrictEl.value) await loadAreaChoices(areaDistrictEl.value, areaSubdistrictEl, "ไม่เลือก (ใช้ระดับอำเภอ)");
});

areaSubdistrictEl.addEventListener("change", () => { resetAreaForecastResult(); updateAreaForecastButton(); });
areaForecastHoursEl.addEventListener("change", resetAreaForecastResult);

areaForecastFormEl.addEventListener("submit", async (event) => {
  event.preventDefault();
  const areaCode = selectedAreaCode();
  if (!areaCode) return;
  const requestId = ++state.areaForecastRequestId;
  state.areaForecastController?.abort();
  const controller = new AbortController();
  state.areaForecastController = controller;
  areaForecastSubmitEl.disabled = true;
  areaForecastStatusEl.textContent = "กำลังโหลด…";
  areaForecastResultEl.className = "metrics empty-state area-forecast-result";
  areaForecastResultEl.innerHTML = "<strong>กำลังโหลดพยากรณ์ TMD…</strong><small>ดึงเมื่อเลือกพื้นที่เพื่อประหยัดโควตา</small>";
  try {
    const params = new URLSearchParams({ area_code: areaCode, hours: areaForecastHoursEl.value });
    const result = await getJSON(`/api/v1/forecast/hourly?${params}`, { signal: controller.signal });
    if (requestId === state.areaForecastRequestId) renderAreaForecast(result);
  } catch (error) {
    if (error.name !== "AbortError" && requestId === state.areaForecastRequestId) {
      areaForecastResultEl.innerHTML = "<strong>โหลดพยากรณ์ไม่สำเร็จ</strong><small>โควตาอาจไม่พอ หรือ TMD อาจขัดข้อง ลองใหม่ภายหลัง</small>";
      areaForecastStatusEl.textContent = "โหลดไม่สำเร็จ";
      console.warn("TMD hourly area forecast unavailable", error);
    }
  } finally {
    if (requestId === state.areaForecastRequestId) updateAreaForecastButton();
  }
});

function renderScopeOptions(byProvince) {
  state.lastByProvince = byProvince;
  const counts = new Map(byProvince.map((row) => [row.name, row]));
  const withAlerts = state.provinces.filter((pv) => counts.get(pv.name)?.alerts)
    .sort((a, b) => counts.get(b.name).alerts - counts.get(a.name).alerts || a.name.localeCompare(b.name, "th"));
  const quiet = state.provinces.filter((pv) => !counts.get(pv.name)?.alerts).sort((a, b) => a.name.localeCompare(b.name, "th"));
  const option = (pv) => {
    const c = counts.get(pv.name);
    const worst = c?.critical ? ` · วิกฤต ${c.critical}` : c?.high ? ` · สูง ${c.high}` : "";
    const rain = state.provinceForecast.get(pv.name)?.[1]?.rain_mm;
    const parts = [c?.alerts ? `${c.alerts} จุด${worst}` : "", rain > RAIN_WORTH_NOTING ? `ฝนพรุ่งนี้ ${formatNumber(rain, 0)} มม.` : ""].filter(Boolean);
    return `<option value="${escapeHtml(pv.name)}">${escapeHtml(pv.name)}${parts.length ? ` (${parts.join(" · ")})` : ""}</option>`;
  };
  priorityScopeEl.innerHTML = `<option value="">ทั้งหมดในจอ</option>
    ${withAlerts.length ? `<optgroup label="มีจุดเตือน">${withAlerts.map(option).join("")}</optgroup>` : ""}
    <optgroup label="ไม่มีจุดเตือน">${quiet.map(option).join("")}</optgroup>`;
  priorityScopeEl.value = state.scope;
}

async function loadAlerts() {
  const requestId = ++state.alertsRequestId;
  const params = new URLSearchParams();
  if (state.scope) {
    params.set("province", state.scope);
  } else {
    const b = map.getBounds();
    for (const [k, v] of Object.entries({ min_lon: b.getWest(), min_lat: b.getSouth(), max_lon: b.getEast(), max_lat: b.getNorth() })) params.set(k, v);
  }
  try {
    const result = await getJSON(`/api/v1/alerts?${params}`);
    if (requestId !== state.alertsRequestId) return null;
    if (state.provinces.length) renderScopeOptions(result.data.by_province);
    renderPriority(result.data.features, result.meta?.observed_at_max, result.stale);
    const newsProvince = state.scope && result.data.features.length
      ? state.scope : result.data.features[0]?.properties?.province;
    loadNewsForProvince(newsProvince);
    return null;  // same upstream as flood points; its health is recorded there
  } catch (error) {
    if (requestId !== state.alertsRequestId) return null;
    alertCountEl.textContent = "—";  // unknown, never 0: a failed load must not read as "no alerts"
    priorityKpiEl.classList.remove("has-alerts");
    priorityMetaEl.textContent = "โหลดไม่สำเร็จ";
    setKpiNote("โหลดไม่สำเร็จ · ไม่ทราบจำนวนจุดเตือน", "error");
    mapAlertSummaryEl.textContent = "จุดเตือน: โหลดไม่สำเร็จ";
    mapAlertSummaryEl.classList.remove("has-alerts");
    priorityListEl.setAttribute("aria-busy", "false");
    priorityListEl.innerHTML = `<p class="priority-empty is-error"><svg class="sev" aria-hidden="true"><use href="#sev-unknown"/></svg><span>โหลดรายการจุดเตือนไม่สำเร็จ · ไม่ทราบสถานการณ์ ไม่ใช่ "ไม่มีจุดเตือน" · กดรีเฟรชเพื่อลองใหม่</span></p>`;
    loadNewsForProvince(null);
    console.warn("alerts unavailable", error);
    return null;
  }
}

async function loadProvinces() {
  try {
    state.provinces = (await getJSON("/api/v1/provinces")).provinces;
  } catch (error) {
    console.warn("province list unavailable", error);
    priorityScopeEl.disabled = true;
    state.scope = "";
  }
  if (state.scope && !state.provinces.some((pv) => pv.name === state.scope)) state.scope = "";
}

function setScope(name) {
  state.scope = name;
  storage.set(SCOPE_STORAGE_KEY, name);
  const pv = state.provinces.find((row) => row.name === name);
  if (pv?.bbox) map.fitBounds([[pv.bbox[1], pv.bbox[0]], [pv.bbox[3], pv.bbox[2]]], { padding: [16, 16] });
  loadAlerts();
  loadSatellitePriority();
  renderForecastLine();
  syncScopeForecastButton();
}

// "ดูฝนจังหวัดนี้": copies the list's province into the TMD form and jumps there. It never
// requests a forecast itself (TMD quota); the user still presses "ดูพยากรณ์".
const scopeForecastButton = document.querySelector("#scope-forecast");
const areaPrefillNoteEl = document.querySelector("#area-forecast-prefill");
function syncScopeForecastButton() {
  scopeForecastButton.hidden = !state.scope;
  scopeForecastButton.textContent = state.scope ? `ดูพยากรณ์ฝนรายชั่วโมง จ.${state.scope}` : "";
}
scopeForecastButton.addEventListener("click", () => {
  const option = [...areaProvinceEl.options].find((o) => o.textContent === state.scope);
  if (!option) { showToast("รายชื่อจังหวัดของฟอร์มพยากรณ์ยังโหลดไม่เสร็จ ลองอีกครั้ง", "warn"); return; }
  if (areaProvinceEl.value !== option.value) {
    areaProvinceEl.value = option.value;
    areaProvinceEl.dispatchEvent(new Event("change"));
  }
  areaPrefillNoteEl.textContent = `เติม จ.${state.scope} จากรายการจุดเตือนแล้ว · ยังไม่ดึงพยากรณ์ เลือกอำเภอ/ตำบลเพิ่มได้ แล้วกด "ดูพยากรณ์"`;
  areaPrefillNoteEl.hidden = false;
  jumpToSection("area-forecast-title", areaProvinceEl);
});

priorityScopeEl.addEventListener("change", () => setScope(priorityScopeEl.value));

// ---------------------------------------------------------------- dams
const DAM_TOP = 5;
state.damFeatures = [];
state.damMarkerById = new Map();
state.damShowAll = false;

// Fullest first. Equal percentages: the one gaining water fastest relative to its own size
// (absolute inflow would always favour the biggest dams).
function damRank(a, b) {
  const pa = a.properties.percent_storage ?? -1;
  const pb = b.properties.percent_storage ?? -1;
  const gain = (p) => ((p.inflow ?? 0) - (p.outflow ?? 0)) / (p.storage || 1);
  return pb - pa || gain(b.properties) - gain(a.properties);
}

function damTrend(p) {
  if (p.inflow == null || p.outflow == null) return { symbol: "", text: "ไม่มีข้อมูลน้ำเข้า/ระบาย" };
  const diff = p.inflow - p.outflow;
  if (Math.abs(diff) < 0.01) return { symbol: "■", text: "น้ำเข้าเท่ากับระบาย" };
  return diff > 0
    ? { symbol: "▲", text: `น้ำเข้ามากกว่าระบาย ${formatNumber(diff)} ล้าน ลบ.ม./วัน` }
    : { symbol: "▼", text: `ระบายมากกว่าน้ำเข้า ${formatNumber(-diff)} ล้าน ลบ.ม./วัน` };
}

state.damPhotos = {};

function damPhotoCredit(photo) {
  const license = photo.license_url
    ? `<a href="${escapeHtml(photo.license_url)}" target="_blank" rel="noreferrer">${escapeHtml(photo.license)}</a>`
    : escapeHtml(photo.license || "");
  return `ภาพ: ${escapeHtml(photo.artist || "ไม่ระบุผู้ถ่าย")} · ${license} · <a href="${escapeHtml(photo.page_url)}" target="_blank" rel="noreferrer">Wikimedia Commons</a>`;
}

function damPopup(p) {
  const photo = state.damPhotos[p.name];
  return `${photo ? `<figure class="dam-photo"><img src="${escapeHtml(photo.thumb_url)}" alt="${escapeHtml(p.name)}" loading="lazy">
      <figcaption>${damPhotoCredit(photo)}</figcaption></figure>` : ""}
    <div class="popup-title">${escapeHtml(p.name)}</div>
    <div class="popup-grid">
      <span>จังหวัด</span><b>${escapeHtml(p.province)}</b>
      <span>น้ำในเขื่อน</span><b>${formatNumber(p.volume)} ล้าน ลบ.ม.</b>
      <span>% ของความจุที่ระดับเก็บกัก</span><b>${formatNumber(p.percent_storage)}%${p.percent_storage > 100 ? " (เกินระดับเก็บกักปกติ)" : ""}</b>
      <span>ความจุที่ระดับเก็บกัก</span><b>${formatNumber(p.storage)} ล้าน ลบ.ม.</b>
      <span>ความจุสูงสุด</span><b>${formatNumber(p.capacity)} ล้าน ลบ.ม.</b>
      <span>ไหลเข้า</span><b>${formatNumber(p.inflow)} ล้าน ลบ.ม.</b>
      <span>ระบาย</span><b>${formatNumber(p.outflow)} ล้าน ลบ.ม.</b>
      <span>วันที่ข้อมูล</span><b>${escapeHtml(p.observed_at)}</b>
    </div><div class="popup-note">% คือสัดส่วนความจุ ไม่ใช่ระดับภัยน้ำท่วม</div>
    <div class="popup-source">${escapeHtml(p.source)}</div>`;
}

async function loadDamPhotos() {
  try {
    state.damPhotos = (await getJSON("/api/v1/dams/photos")).data.photos || {};
    if (state.damFeatures.length) renderDamList();
  } catch (error) {
    console.warn("dam photos unavailable", error);  // photos are decoration; the data still shows
  }
}

function renderDamList() {
  const dams = [...state.damFeatures].sort(damRank);
  damListEl.replaceChildren();
  const top = dams[0]?.properties;
  damMetaEl.textContent = top ? `${dams.length} เขื่อน · สูงสุด ${top.name.replace(/^เขื่อน/, "")} ${damPercentText(top.percent_storage)}` : `${dams.length} เขื่อน`;
  for (const feature of state.damShowAll ? dams : dams.slice(0, DAM_TOP)) {
    const p = feature.properties;
    const trend = damTrend(p);
    const percent = p.percent_storage;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "dam-item";
    button.style.setProperty("--fill", `${Math.max(0, Math.min(100, percent ?? 0))}%`);
    const photo = state.damPhotos[p.name];
    button.innerHTML = `${photo
        ? `<img class="dam-thumb" src="${escapeHtml(photo.thumb_url)}" alt="" loading="lazy" title="ภาพ: ${escapeHtml(photo.artist || "")} · ${escapeHtml(photo.license || "")} · Wikimedia Commons">`
        : `<span class="dam-thumb placeholder">${icon("dam")}</span>`}
      <span class="dam-copy"><strong>${escapeHtml(p.name)}</strong>
        <small>ความจุ ${formatNumber(p.storage, 0)} · ปัจจุบัน ${formatNumber(p.volume, 1)}</small>
        <span class="dam-bar" aria-hidden="true"><i></i></span></span>
      <span class="dam-level"><b>${percent == null ? "—" : `${formatNumber(percent, 1)}%`}</b>
        ${percent > 100 ? '<i class="dam-over">100%+</i>' : ""}
        <small title="${escapeHtml(trend.text)}">${trend.symbol}</small></span>`;
    button.setAttribute("aria-label", `${p.name} ความจุ ${formatNumber(p.storage, 0)} ล้าน ลบ.ม. ปัจจุบัน ${formatNumber(p.volume, 1)} ล้าน ลบ.ม. ${formatNumber(percent, 1)} เปอร์เซ็นต์ ${trend.text} กดเพื่อดูบนแผนที่`);
    button.addEventListener("click", () => focusDam(feature));
    damListEl.append(button);
  }
  damMoreButton.hidden = dams.length <= DAM_TOP;
  damMoreButton.textContent = state.damShowAll ? `แสดง ${DAM_TOP} อันดับแรก` : `ดูทั้งหมด ${dams.length} เขื่อน`;
}

function focusDam(feature) {
  const [lon, lat] = feature.geometry.coordinates;
  const toggle = document.querySelector("#dam-toggle");
  if (!toggle.checked) { toggle.checked = true; state.damLayer.addTo(map); damPanelEl.hidden = false; }
  map.flyTo([lat, lon], Math.max(map.getZoom(), 10), { duration: .65 });
  map.once("moveend", () => state.damMarkerById.get(feature.properties.id)?.openPopup());
}

damMoreButton.addEventListener("click", () => { state.damShowAll = !state.damShowAll; renderDamList(); });

// Floating dam panel on the map: collapsible (remembered; collapsed by default),
// hidden with the dam layer, and it must not pass clicks/scrolls through to the map.
const damPanelEl = document.querySelector("#dam-panel");
const damPanelToggle = document.querySelector("#dam-panel-toggle");
L.DomEvent.disableClickPropagation(damPanelEl);
L.DomEvent.disableScrollPropagation(damPanelEl);
function setDamPanelCollapsed(collapsed) {
  damPanelEl.classList.toggle("collapsed", collapsed);
  damPanelToggle.setAttribute("aria-expanded", String(!collapsed));
  storage.set("dam-panel-collapsed", collapsed ? "1" : "0");
}
const storedCollapse = storage.get("dam-panel-collapsed");
// Collapsed on first visit (header still shows the fullest dam); the user's choice is kept after.
setDamPanelCollapsed(storedCollapse ? storedCollapse === "1" : true);
damPanelToggle.addEventListener("click", () => setDamPanelCollapsed(!damPanelEl.classList.contains("collapsed")));
// The panel floats above the map, so a popup opening under it gets hidden. Slide the map just
// enough to clear it: sideways when both fit side by side, otherwise upwards.
map.on("popupopen", (event) => {
  if (damPanelEl.hidden) return;
  setTimeout(() => {  // after Leaflet's own autoPan animation (~250 ms)
    const popupEl = event.popup.getElement();
    if (!popupEl) return;
    const a = popupEl.getBoundingClientRect();
    const b = damPanelEl.getBoundingClientRect();
    const overlapX = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    const overlapY = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    if (overlapX <= 0 || overlapY <= 0) return;
    const sideBySide = a.width + b.width + 40 < map.getSize().x;
    // Move the popup's right edge left of the panel (or its bottom edge above it).
    map.panBy(sideBySide ? [a.right - b.left + 16, 0] : [0, a.bottom - b.top + 16]);
  }, 320);
});

const syncZoomDetail = () => map.getContainer().classList.toggle("map-zoom-detail", map.getZoom() >= DAM_DETAIL_ZOOM);
map.on("zoomend", syncZoomDetail);
syncZoomDetail();

async function loadDams() {
  try {
    const result = await getJSON("/api/v1/dams");
    state.damLayer.clearLayers();
    state.damMarkerById = new Map();
    for (const feature of result.data.features || []) {
      const [lon, lat] = feature.geometry.coordinates;
      const p = feature.properties;
      const marker = L.marker([lat, lon], {
        icon: damIcon(p), riseOnHover: true,
        zIndexOffset: (p.percent_storage ?? 0) >= DAM_LABEL_ALWAYS ? 400 : 0,  // full dams stay on top
      })
        .bindTooltip(`<b>${escapeHtml(p.name)}</b><br>ความจุ ${formatNumber(p.storage, 0)} · ปัจจุบัน ${formatNumber(p.volume, 1)} ล้าน ลบ.ม.<br>${formatNumber(p.percent_storage, 1)}% ของความจุ · ${escapeHtml(damTrend(p).text)}`, { direction: "top" })
        .bindPopup(() => damPopup(p), { maxWidth: 300 })
        .addTo(state.damLayer);
      state.damMarkerById.set(p.id, marker);
    }
    state.damFeatures = result.data.features || [];
    damObservedEl.textContent = `ข้อมูลกรมชลประทานรายวัน ณ ${formatObserved(state.damFeatures.map((f) => f.properties.observed_at).filter(Boolean).sort().at(-1))}${result.stale ? " · ข้อมูลสำรอง" : ""}`;
    renderDamList();
    state.damCount = result.data.features?.length || 0;
    updateLayerCounts();
    if (document.querySelector("#dam-toggle").checked && !map.hasLayer(state.damLayer)) state.damLayer.addTo(map);
    return record(outcome("เขื่อน", result));
  } catch (error) {
    damCountEl.textContent = "—";  // unknown, not zero: a failed join must not read as "no dams"
    damCountEl.title = "โหลดข้อมูลเขื่อนไม่สำเร็จ";
    damMetaEl.textContent = "โหลดไม่สำเร็จ";
    if (!state.damFeatures.length) damListEl.innerHTML = '<p class="priority-empty">โหลดข้อมูลเขื่อนไม่สำเร็จ · ลองรีเฟรชอีกครั้ง</p>';
    const result = failure("เขื่อน", error);
    updateLayerCounts();
    return result;
  }
}

// Below CLUSTER_MAX_ZOOM, DPM pins closer than CLUSTER_RADIUS px merge into one pin drawn at, and
// coloured by, their worst member, with the count on it (Rules.clusterPoints). Clicking it lists
// the members so any critical station is two clicks away. state.clusterEnabled = false restores
// one pin per station (the old behaviour) if grouping ever hides something it shouldn't.
const CLUSTER_MAX_ZOOM = 12;
const CLUSTER_RADIUS = 30;
state.clusterEnabled = true;

function floodPointPopup(p) {
  const isRoad = p.point_type === "road_flood";
  const measurement = isRoad
    ? `<span>ระดับน้ำขัง</span><b>${formatNumber(p.water_depth_cm)} ซม.</b>
       <span>ค่าสูงสุด</span><b>${formatNumber(p.maximum_depth_cm)} ซม.</b>`
    : `<span>ระดับน้ำ</span><b>${formatNumber(p.water_level_msl)} ม.รทก.</b>
       <span>ระดับตลิ่ง</span><b>${formatNumber(p.bank_level)} ม.รทก.</b>
       <span>ห่างจากตลิ่ง</span><b>${formatNumber(p.bank_clearance_m)} ม.</b>`;
  return `<div class="popup-title">${escapeHtml(p.name)}</div>
    <div class="popup-grid">
      <span>ประเภท</span><b>${isRoad ? "จุดวัดน้ำท่วมขังถนน" : "สถานีระดับน้ำแม่น้ำ"}</b>
      <span>ระดับตามเกณฑ์แผนที่</span><b>${severityLabels[p.severity] || severityLabels.unknown}</b>
      ${measurement}
      <span>จังหวัด</span><b>${escapeHtml(p.province)}</b>
      <span>เวลา</span><b>${escapeHtml(p.dpm_observed_at ?? p.observed_at)}</b>
    </div>${p.twin ? twinSection(p) : ""}${isRoad ? "" : `<div class="popup-note">${RIVER_GAUGE_NOTE}</div>`}
    <div class="popup-source">${escapeHtml(p.source)}</div>`;
}

// Opens one station out of a group: it becomes "forced" so it is drawn on its own.
function openFloodPoint(key) {
  state.forceVisible.add(`fp:${key}`);
  state.pendingFocusKey = key;
  map.closePopup();
  renderFloodPoints();
}

function clusterPopup(features, bounds) {
  const el = document.createElement("div");
  const bySeverity = severityBreakdown(features);
  el.innerHTML = `<div class="popup-title">${features.length} จุดเฝ้าระวังที่อยู่ใกล้กัน</div>
    <p class="cluster-summary">${escapeHtml(bySeverity)}</p><div class="cluster-list"></div>`;
  const list = el.querySelector(".cluster-list");
  for (const feature of features.slice(0, 8)) {
    const p = feature.properties;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "cluster-item";
    button.style.setProperty("--marker", severityColors[p.severity] || severityColors.unknown);
    button.innerHTML = `${severityIcon(p.severity)}<span>${escapeHtml(p.name)}</span><b>${severityLabels[p.severity] || severityLabels.unknown}</b>`;
    button.addEventListener("click", () => openFloodPoint(floodPointKey(feature)));
    list.append(button);
  }
  const zoom = document.createElement("button");
  zoom.type = "button";
  zoom.className = "link-button";
  zoom.textContent = features.length > 8 ? `ซูมเข้าเพื่อดูอีก ${features.length - 8} จุด` : "ซูมเข้าไปที่กลุ่มนี้";
  zoom.addEventListener("click", () => { map.closePopup(); map.fitBounds(bounds.pad(0.4), { maxZoom: CLUSTER_MAX_ZOOM }); });
  el.append(zoom);
  return el;
}

function clusterIcon(severity, count) {
  const color = severityColors[severity] || severityColors.unknown;
  return L.divIcon({
    className: "flood-point-icon",
    html: `<div class="flood-pin" style="--marker:${color}">${severityIcon(severity)}</div><span class="cluster-count">${count}</span>`,
    iconSize: [30, 30], iconAnchor: [15, 28], popupAnchor: [0, -27],
  });
}

// Renders from the last-fetched viewport (state.floodAllFeatures); does not call the API, so
// toggling risk-only mode, the ThaiWater twin layer, or grouping redraws instantly.
function renderFloodPoints() {
  const allFeatures = state.floodAllFeatures;
  // A currently open popup must survive the redraw even if risk-only mode would hide it.
  const openMarker = state.floodPointLayer.getLayers().find((marker) => marker.isPopupOpen());
  const openKey = openMarker?.pointKey;
  if (openKey) state.forceVisible.add(`fp:${openKey}`);
  // Popup to restore after this redraw: a single station or a group (keyed by its anchor).
  // A reopen scheduled by an earlier redraw that has not run yet carries over.
  if (openKey) state.reopenFlood = { key: openKey };
  else if (openMarker?.clusterKey) state.reopenFlood = { cluster: openMarker.clusterKey };
  // Read before clearLayers(): removing an open marker fires its popupclose, which drops the key.
  const forced = new Set([...state.forceVisible].filter((k) => k.startsWith("fp:")).map((k) => k.slice(3)));
  if (state.pendingFocusKey) forced.add(state.pendingFocusKey);
  state.floodPointLayer.clearLayers();
  if (openKey) state.forceVisible.add(`fp:${openKey}`);
  state.markerByKey = new Map();
  state.dpmByTwin = new Map(allFeatures.filter((f) => f.properties.twin_id).map((f) => [f.properties.twin_id, f.properties]));
  const zoom = map.getZoom();
  const twinsOnMap = map.hasLayer(THAIWATER_LAYERS["water-level"].group);
  const counts = classify("flood-points", allFeatures, {
    zoom, riskOnly: state.riskOnly, forced, keyOf: floodPointKey,
    covered: (f) => twinsOnMap && state.twDrawnIds.has(f.properties.twin_id),
  });
  state.pointCounts["flood-points"] = counts;
  const points = counts.shown.map((feature) => {
    const [lon, lat] = feature.geometry.coordinates;
    const { x, y } = map.latLngToLayerPoint([lat, lon]);
    return { x, y, rank: severityOrder[feature.properties.severity] ?? 0, key: floodPointKey(feature), feature };
  });
  const groups = state.clusterEnabled && zoom < CLUSTER_MAX_ZOOM
    ? clusterPoints(points, CLUSTER_RADIUS, forced)
    : points.map((point) => ({ anchor: point, members: [point] }));
  state.clusterByKey = new Map();
  for (const group of groups) {
    const feature = group.anchor.feature;
    const [lon, lat] = feature.geometry.coordinates;
    const p = feature.properties;
    const rank = group.anchor.rank;
    if (group.members.length > 1) {
      const members = group.members.map((m) => m.feature);
      const bounds = L.latLngBounds(members.map((m) => [m.geometry.coordinates[1], m.geometry.coordinates[0]]));
      const cluster = L.marker([lat, lon], {
        icon: clusterIcon(p.severity, members.length), riseOnHover: true, zIndexOffset: rank * 120 + 60,
        title: `${members.length} จุดใกล้กัน · สูงสุด ${severityLabels[p.severity] || severityLabels.unknown} · กดเพื่อดูรายการ`,
      }).bindPopup(() => clusterPopup(members, bounds), { maxWidth: 300 }).addTo(state.floodPointLayer);
      cluster.clusterKey = group.anchor.key;
      state.clusterByKey.set(group.anchor.key, cluster);
      continue;
    }
    const key = group.anchor.key;
    const marker = L.marker([lat, lon], {
      icon: floodPointIcon(p.severity), riseOnHover: true, zIndexOffset: rank * 120,
      title: `${p.name} · ${severityLabels[p.severity] || severityLabels.unknown}`,
    })
      .bindPopup(floodPointPopup(p))
      .on("popupclose", () => state.forceVisible.delete(`fp:${key}`))
      .addTo(state.floodPointLayer);
    marker.pointKey = key;
    state.markerByKey.set(key, marker);
    if (state.pendingFocusKey === key) { state.pendingFocusKey = null; state.reopenFlood = { key }; }
  }
  // Look the marker up when the timer fires, not now: another redraw may replace it first.
  if (state.reopenFlood) setTimeout(() => {
    const target = state.reopenFlood;
    state.reopenFlood = null;
    const marker = target && (target.key ? state.markerByKey.get(target.key) : state.clusterByKey.get(target.cluster));
    if (marker && !marker.isPopupOpen()) marker.openPopup();
  }, 0);
  updateLayerCounts();
  if (document.querySelector("#flood-point-toggle").checked && !map.hasLayer(state.floodPointLayer)) state.floodPointLayer.addTo(map);
}

async function loadFloodPoints() {
  const requestId = ++state.floodRequestId;
  const b = map.getBounds();
  const params = new URLSearchParams({
    min_lon: b.getWest(), min_lat: b.getSouth(), max_lon: b.getEast(), max_lat: b.getNorth(),
  });
  try {
    const result = await getJSON(`/api/v1/flood-points?${params}`);
    if (requestId !== state.floodRequestId) return null;
    state.floodAllFeatures = result.data.features || [];
    renderFloodPoints();
    return record(outcome("จุดเฝ้าระวัง ปภ.", result));
  } catch (error) {
    if (requestId !== state.floodRequestId) return null;
    const result = failure("จุดเฝ้าระวัง ปภ.", error);
    updateLayerCounts();
    return result;
  }
}

const thDay = (iso) => new Date(`${iso}T00:00:00+07:00`)
  .toLocaleDateString("th-TH", { weekday: "short", day: "numeric", month: "short" });
const thHour = (iso) => iso.slice(11, 16);

function rainForecastHtml(data) {
  const hourly = data.hourly || {};
  const times = hourly.time || [];
  const nowKey = (data.current?.time || "").slice(0, 13);
  let startIdx = times.findIndex((t) => t.slice(0, 13) >= nowKey);
  if (startIdx < 0) startIdx = 0;
  const idx = [...Array(48).keys()].map((i) => startIdx + i).filter((i) => i < times.length);
  if (!idx.length) return `<div class="metric"><strong>พยากรณ์ฝน</strong><br>—</div>`;
  const rain = idx.map((i) => hourly.precipitation?.[i] ?? 0);
  const prob = idx.map((i) => hourly.precipitation_probability?.[i]);
  const sum = (a) => a.reduce((x, y) => x + (Number(y) || 0), 0);
  const peak = Math.max(...rain);
  const peakAt = times[idx[rain.indexOf(peak)]];
  const scale = Math.max(peak, 1);
  const bars = idx.map((i, k) => `<i style="--h:${Math.round((rain[k] / scale) * 100)}%"
      title="${thHour(times[i])} · ${formatNumber(rain[k], 1)} มม.${prob[k] == null ? "" : ` · โอกาส ${prob[k]}%`}"></i>`).join("");
  return `
    <div class="metric">
      <strong>พยากรณ์ฝน ${idx.length} ชม. ข้างหน้า</strong> <span class="tag">พยากรณ์</span>
      <div class="bars" role="img" aria-label="กราฟฝนรายชั่วโมง ${idx.length} ชั่วโมง สูงสุด ${formatNumber(peak, 1)} มม.">${bars}</div>
      <div class="bars-axis"><span>ตอนนี้</span><span>+24 ชม.</span><span>+48 ชม.</span></div>
      24 ชม.: <b>${formatNumber(sum(rain.slice(0, 24)), 1)} มม.</b>
      · 48 ชม.: <b>${formatNumber(sum(rain), 1)} มม.</b><br>
      โอกาสฝนสูงสุด 24 ชม.: <b>${formatNumber(Math.max(0, ...prob.slice(0, 24).filter(Number.isFinite)), 0)}%</b>
      ${peak > 0 ? ` · หนักสุด ${formatNumber(peak, 1)} มม. เวลา ${thHour(peakAt)}` : ""}
    </div>`;
}

function riverForecastHtml(data) {
  const daily = data.daily || {};
  const days = daily.time || [];
  const mean = daily.river_discharge || [];
  const maxes = daily.river_discharge_max || [];
  if (!days.length) return `<div class="metric"><strong>River discharge</strong><br>ไม่มีข้อมูลลำน้ำ ณ จุดนี้</div>`;
  const finiteMax = maxes.filter(Number.isFinite);
  const peak = finiteMax.length ? Math.max(...finiteMax) : null;
  const peakDay = peak == null ? null : days[maxes.indexOf(peak)];
  const scale = peak || 1;
  const rows = days.map((d, i) => `<div class="day-row">
      <span>${thDay(d)}</span>
      <span class="day-bar"><i style="--w:${Math.round(((maxes[i] ?? 0) / scale) * 100)}%"></i></span>
      <b>${formatNumber(maxes[i], 0)}</b></div>`).join("");
  return `
    <div class="metric">
      <strong>River discharge 7 วัน (m³/s)</strong> <span class="tag">พยากรณ์ GloFAS</span><br>
      วันนี้เฉลี่ย <b>${formatNumber(mean[0], 1)}</b> · สูงสุดช่วงพยากรณ์ <b>${formatNumber(peak, 1)}</b>${peakDay ? ` (${thDay(peakDay)})` : ""}
      <div class="day-list" aria-label="ค่าสูงสุดรายวัน river_discharge_max">${rows}</div>
      <small class="hint">ค่าสูงสุดรายวันจาก river_discharge_max · ความละเอียด ~5 กม. ไม่ใช่ระดับน้ำท่วม ณ จุดคลิก</small>
    </div>`;
}

async function inspectPoint(event) {
  const requestId = ++state.inspectRequestId;
  const { lat, lng: lon } = event.latlng;
  pointData.classList.remove("empty-state");
  pointData.textContent = "กำลังโหลด…";
  // Independent requests: one failing source must not hide the other.
  const [weather, river] = await Promise.allSettled([
    getJSON(`/api/v1/weather/current?lat=${lat}&lon=${lon}`),
    getJSON(`/api/v1/river?lat=${lat}&lon=${lon}`),
  ]);
  if (requestId !== state.inspectRequestId) return;
  const current = weather.status === "fulfilled" ? weather.value.data.current || {} : null;
  pointData.innerHTML = `
    <div class="metric"><strong>พิกัด</strong><br>${lat.toFixed(4)}, ${lon.toFixed(4)}</div>
    <div class="metric"><strong>ฝน ณ จุดที่เลือก</strong> <span class="tag">แบบจำลองปัจจุบัน</span><br>
      ${current ? `${formatNumber(current.precipitation, 1)} มม./ชม.` : "โหลดไม่สำเร็จ"}</div>
    ${weather.status === "fulfilled" ? rainForecastHtml(weather.value.data) : `<div class="metric"><strong>พยากรณ์ฝน</strong><br>โหลดไม่สำเร็จ</div>`}
    ${river.status === "fulfilled" ? riverForecastHtml(river.value.data) : `<div class="metric"><strong>River discharge</strong><br>โหลดไม่สำเร็จ</div>`}`;
}

// ---------------------------------------------------------------- ThaiWater station layers
const THAIWATER_LAYERS = {
  "water-level": { toggle: "#tw-water-level-toggle", meta: "#tw-water-level-meta", unit: "สถานี", radius: 7, stroke: "#ffffff", minZoom: 8, source: "ThaiWater ระดับน้ำ" },
  canal: { toggle: "#tw-canal-toggle", meta: "#tw-canal-meta", unit: "สถานี", source: "ThaiWater คลอง", radius: 6, stroke: "#0b3954", minZoom: 9 },
  rain: { toggle: "#tw-rain-toggle", meta: "#tw-rain-meta", unit: "สถานี", source: "ThaiWater ฝน", radius: 4, stroke: "#07131d", minZoom: 8, opacity: 0.72 },
  watergate: { toggle: "#tw-watergate-toggle", meta: "#tw-watergate-meta", unit: "ประตู", source: "ThaiWater ประตูน้ำ", radius: 6, stroke: "#07131d", minZoom: 9, color: "#5aa9e6" },
};
// Below each layer's minZoom only alert-level stations are drawn (Rules.passesZoom).
// Canvas marker whose outline follows the severity family used in the legend: circle
// (normal/low/unknown), triangle (moderate/high), octagon (critical). Hit-testing stays the
// circle's radius, which is what Leaflet's canvas renderer uses for clicks.
const severityShape = (severity) => ({ moderate: "triangle", high: "triangle", critical: "octagon" }[severity] || "circle");
const ShapeMarker = L.CircleMarker.extend({
  _updatePath() {
    const renderer = this._renderer;
    if (this.options.shape === "circle" || !renderer._drawing || this._empty()) {
      if (this.options.shape === "circle") L.CircleMarker.prototype._updatePath.call(this);
      return;
    }
    const { x, y } = this._point;
    const r = Math.max(Math.round(this._radius), 1);
    const ctx = renderer._ctx;
    ctx.beginPath();
    if (this.options.shape === "triangle") {
      const t = r * 1.35;  // same visual weight as the circle it replaces
      ctx.moveTo(x, y - t);
      ctx.lineTo(x + t * 0.95, y + t * 0.62);
      ctx.lineTo(x - t * 0.95, y + t * 0.62);
    } else {
      const o = r * 1.12;
      for (let i = 0; i < 8; i++) {
        const a = Math.PI / 8 + (i * Math.PI) / 4;
        ctx[i ? "lineTo" : "moveTo"](x + o * Math.cos(a), y + o * Math.sin(a));
      }
    }
    ctx.closePath();
    renderer._fillStroke(ctx, this);
  },
});

// Bottom-to-top draw order in the shared canvas: dense rain gauges must never cover river alerts.
const STATION_DRAW_ORDER = ["rain", "canal", "watergate", "water-level"];
function raiseStationLayers(redrawn) {
  for (const layer of STATION_DRAW_ORDER.slice(STATION_DRAW_ORDER.indexOf(redrawn) + 1)) {
    THAIWATER_LAYERS[layer].group.eachLayer((marker) => marker.bringToFront());
  }
}
const withUnit = (value, unit, digits = 2) => (value == null ? "—" : `${formatNumber(value, digits)} ${unit}`);
for (const cfg of Object.values(THAIWATER_LAYERS)) {
  cfg.group = L.layerGroup();
  cfg.requestId = 0;  // bumped per load/toggle-off so late responses are dropped
  cfg.features = [];  // last-fetched viewport; renderThaiWater() redraws from this without refetching
  cfg.metaEl = document.querySelector(cfg.meta);
  cfg.defaultMeta = cfg.metaEl.textContent;
}

function twPopup(p) {
  const trend = p.trend_m == null ? "—"
    : `${p.trend_m > 0 ? "▲" : p.trend_m < 0 ? "▼" : "■"} ${formatNumber(Math.abs(p.trend_m))} ม.`;
  const rows = {
    river_gauge: `
      <span>แม่น้ำ</span><b>${escapeHtml(p.river)}</b>
      <span>ระดับน้ำ</span><b>${withUnit(p.water_level_msl, "ม.รทก.")}</b>
      <span>แนวโน้ม</span><b>${trend}</b>
      <span>% ความจุลำน้ำ</span><b>${withUnit(p.storage_percent, "%", 1)}</b>
      <span>เทียบตลิ่ง</span><b>${escapeHtml(p.bank_diff_text || (p.bank_diff_m == null ? null : `${formatNumber(p.bank_diff_m)} ม.`))}</b>`,
    rain_gauge: `
      <span>ฝน 24 ชม.</span><b>${withUnit(p.rain_24h_mm, "มม.", 1)}</b>
      <span>ฝน 1 ชม.</span><b>${withUnit(p.rain_1h_mm, "มม.", 1)}</b>`,
    canal_gauge: `
      <span>ระดับน้ำ</span><b>${withUnit(p.water_level_m, "ม.")}</b>
      <span>ห่างจากตลิ่ง</span><b>${withUnit(p.bank_clearance_m, "ม.")}</b>
      <span>เกณฑ์ ThaiWater (เตือน)</span><b>${withUnit(p.warning_level_m, "ม.")}</b>
      <span>เกณฑ์ ThaiWater (วิกฤต)</span><b>${withUnit(p.critical_level_m, "ม.")}</b>`,
    watergate: `
      <span>น้ำหน้าประตู</span><b>${withUnit(p.upstream_level_m, "ม.")}</b>
      <span>น้ำท้ายประตู</span><b>${withUnit(p.downstream_level_m, "ม.")}</b>
      <span>ผลต่าง</span><b>${withUnit(p.head_diff_m, "ม.")}</b>`,
  }[p.point_type] || "";
  return `<div class="popup-title">${escapeHtml(p.name)}</div>
    <div class="popup-grid">${rows}
      <span>จังหวัด</span><b>${escapeHtml(p.province)}</b>
      <span>เวลา</span><b>${escapeHtml(p.observed_at)}</b>
    </div>${p.point_type === "river_gauge" && state.dpmByTwin.has(p.id) ? dpmSection(state.dpmByTwin.get(p.id)) : ""}${p.point_type === "river_gauge" ? `<div class="popup-note">${RIVER_GAUGE_NOTE}</div>` : p.point_type === "canal_gauge" ? `<div class="popup-note">${CANAL_GAUGE_NOTE}</div>` : ""}
    <div class="popup-source">${escapeHtml(p.source)} · via ${escapeHtml(p.provider)}</div>`;
}

function setTwMeta(cfg, text, offline = false) {
  cfg.metaEl.textContent = text;
  cfg.metaEl.classList.toggle("offline", offline);
  syncTwGroupSummary();
}

// The ThaiWater group folds away, so its summary line carries what must stay visible: how many
// layers are on, and any failed or fallback layer.
const twGroupMetaEl = document.querySelector("#tw-group-meta");
function syncTwGroupSummary() {
  const layers = Object.values(THAIWATER_LAYERS);
  const on = layers.filter((cfg) => document.querySelector(cfg.toggle).checked).length;
  const failed = layers.filter((cfg) => cfg.metaEl.classList.contains("offline")).length;
  const stale = layers.filter((cfg) => cfg.metaEl.textContent.includes("ข้อมูลสำรอง")).length;
  twGroupMetaEl.textContent = [`เปิด ${on}/${layers.length} ชั้น`, failed ? `โหลดไม่สำเร็จ ${failed}` : "", stale ? `ข้อมูลสำรอง ${stale}` : ""].filter(Boolean).join(" · ");
  twGroupMetaEl.classList.toggle("offline", Boolean(failed || stale));
}

// Renders one layer from its last-fetched viewport (cfg.features); does not call the API, so
// toggling risk-only mode redraws instantly. Returns the metadata used to build the status line.
function renderThaiWater(layer, meta = {}) {
  const cfg = THAIWATER_LAYERS[layer];
  const features = cfg.features || [];
  const zoom = map.getZoom();
  // Redrawing closes any open popup, and opening a popup can autoPan the map into this very
  // reload; remember which station was open so it survives the redraw (and stays past risk-only).
  const openId = cfg.group.getLayers().find((marker) => marker.isPopupOpen())?.stationId;
  if (openId != null) state.forceVisible.add(`tw:${layer}:${openId}`);
  const prefix = `tw:${layer}:`;
  const forced = new Set([...state.forceVisible].filter((k) => k.startsWith(prefix)).map((k) => k.slice(prefix.length)));
  const counts = classify(layer, features, { zoom, minZoom: cfg.minZoom, riskOnly: state.riskOnly, forced, keyOf: (f) => String(f.properties.id) });
  state.pointCounts[layer] = counts;
  const visible = counts.shown;
  // Worst last, so it draws on top of its neighbours.
  visible.sort((a, b) => (severityOrder[a.properties.severity] ?? 0) - (severityOrder[b.properties.severity] ?? 0));
  const drawn = new Map();
  cfg.group.clearLayers();
  // clearLayers() fired popupclose on the open station; it is reopened below, so keep it forced.
  if (openId != null) state.forceVisible.add(`tw:${layer}:${openId}`);
  for (const feature of visible) {
    const [lon, lat] = feature.geometry.coordinates;
    const p = feature.properties;
    const radius = layer === "rain" ? Math.min(10, cfg.radius + Math.sqrt(p.rain_24h_mm || 0) / 2) : cfg.radius;
    const marker = new ShapeMarker([lat, lon], {
      renderer: stationRenderer, radius, shape: cfg.color ? "circle" : severityShape(p.severity),
      dashArray: p.severity === "unknown" && !cfg.color ? "2 2" : null,
      weight: p.severity === "critical" ? 3 : p.severity === "high" ? 2.5 : 1.5,
      color: cfg.stroke,
      fillColor: cfg.color || severityColors[p.severity] || severityColors.unknown, fillOpacity: cfg.opacity ?? 0.92,
    }).bindPopup(() => twPopup(p))
      .bindTooltip(`${severityLabels[p.severity] || severityLabels.unknown} · ${escapeHtml(p.name)}`)
      .on("popupclose", () => state.forceVisible.delete(`tw:${layer}:${p.id}`))
      .addTo(cfg.group);
    marker.stationId = p.id;
    drawn.set(p.id, marker);
  }
  raiseStationLayers(layer);
  const count = layerCountText(counts, cfg.unit, { riskLabel: layer === "rain" ? "ฝนไม่ถึง 35 มม." : "ต่ำกว่าเกณฑ์" });
  setTwMeta(cfg, meta.stale ? `${count} · ข้อมูลสำรอง` : count);
  cfg.metaEl.title = counts.risk.length ? `ซ่อนเพราะต่ำกว่าเกณฑ์: ${severityBreakdown(counts.risk)}` : "";
  updateLayerCounts();
  if (!map.hasLayer(cfg.group)) cfg.group.addTo(map);
  cfg.drawn = drawn;
  if (openId != null) cfg.reopenId = openId;
  if (layer === "water-level") {
    state.twDrawnIds = new Set(drawn.keys());
    state.twMarkerById = drawn;
    syncTwinMarkers();
    if (drawn.has(state.pendingTwinFocus)) { cfg.reopenId = state.pendingTwinFocus; state.pendingTwinFocus = null; }
  }
  // Looked up when the timer fires: a later redraw of this layer may have replaced the marker.
  if (cfg.reopenId != null) setTimeout(() => {
    const marker = cfg.drawn.get(cfg.reopenId);
    cfg.reopenId = null;
    if (marker && !marker.isPopupOpen()) marker.openPopup();
  }, 0);
}

async function loadThaiWater(layer) {
  const cfg = THAIWATER_LAYERS[layer];
  const requestId = ++cfg.requestId;
  if (!document.querySelector(cfg.toggle).checked) {  // don't fetch hidden layers
    state.outcomes.delete(cfg.source);
    return null;
  }
  if (map.getZoom() <= 6 && ["canal", "watergate"].includes(layer)) {
    cfg.group.clearLayers();
    delete state.pointCounts[layer];
    updateLayerCounts();
    setTwMeta(cfg, "ซูมเข้าเพื่อดูจุดในพื้นที่");
    state.outcomes.delete(cfg.source);
    renderHealth();
    return null;
  }
  const b = map.getBounds();
  const params = new URLSearchParams({
    min_lon: b.getWest(), min_lat: b.getSouth(), max_lon: b.getEast(), max_lat: b.getNorth(),
  });
  try {
    const result = await getJSON(`/api/v1/thaiwater/${layer}?${params}`);
    if (requestId !== cfg.requestId) return null;
    cfg.features = result.data.features || [];
    renderThaiWater(layer, { stale: result.stale });
    return record(outcome(cfg.source, result));
  } catch (error) {
    if (requestId !== cfg.requestId) return null;
    setTwMeta(cfg, "โหลดไม่สำเร็จ", true);
    return failure(cfg.source, error);
  }
}

function loadAllThaiWater() {
  return Promise.all(Object.keys(THAIWATER_LAYERS).map(loadThaiWater));
}

for (const [layer, cfg] of Object.entries(THAIWATER_LAYERS)) {
  document.querySelector(cfg.toggle).addEventListener("change", (event) => {
    if (event.target.checked) loadThaiWater(layer);
    else {
      cfg.requestId++; map.removeLayer(cfg.group); setTwMeta(cfg, "ปิดอยู่ · ไม่นับ"); state.outcomes.delete(cfg.source); renderHealth();
      if (layer === "water-level") syncTwinMarkers();  // bring the DPM markers back
      updateLayerCounts();
    }
  });
}
syncTwGroupSummary();

const radarToggle = document.querySelector("#radar-toggle");
const radarPlay = document.querySelector("#radar-play");
function updateRadarControls() {
  const enabled = radarToggle.checked && state.radarFrames.length > 0;
  radarRange.disabled = !enabled;
  radarPlay.disabled = !enabled;
  radarControlsEl.classList.toggle("is-disabled", !radarToggle.checked);
}
radarRange.addEventListener("input", () => showRadar(Number(radarRange.value)));
radarPlay.addEventListener("click", (event) => {
  if (state.timer) {
    clearInterval(state.timer); state.timer = null; event.currentTarget.innerHTML = "<span>▶</span> เล่นภาพเคลื่อนไหว"; return;
  }
  if (!state.radarFrames.length) return;
  event.currentTarget.innerHTML = "<span>■</span> หยุดภาพเคลื่อนไหว";
  state.timer = setInterval(() => {
    radarRange.value = (Number(radarRange.value) + 1) % state.radarFrames.length;
    showRadar(Number(radarRange.value));
  }, 1200);
});
radarToggle.addEventListener("change", (event) => {
  mapRadarStatusEl.hidden = !event.target.checked;
  if (!event.target.checked && state.timer) {
    clearInterval(state.timer);
    state.timer = null;
    radarPlay.innerHTML = "<span>▶</span> เล่นภาพเคลื่อนไหว";
  }
  updateRadarControls();
  if (!state.radarLayer) return;
  if (event.target.checked) {
    state.radarLayer.setOpacity(0.58).addTo(map);
    state.radarVisibleLayer = state.radarLayer;
  } else {
    if (state.radarVisibleLayer && map.hasLayer(state.radarVisibleLayer)) map.removeLayer(state.radarVisibleLayer);
    if (map.hasLayer(state.radarLayer)) map.removeLayer(state.radarLayer);
  }
});
updateRadarControls();
floodToggle.addEventListener("change", () => {
  if (floodToggle.checked) { loadFlood(); if (state.scope) loadSatellitePriority(); return; }
  state.floodCellRequestId++;
  map.removeLayer(state.floodTiles);
  map.removeLayer(state.floodCells);
  setFloodMeta("GISTDA · ปิดอยู่");
  renderSatellitePriority(null);
  state.outcomes.delete(FLOOD_SOURCE);
  renderHealth();
});
floodWindowSelect.addEventListener("change", () => {
  state.floodTiles.setUrl(`/api/v1/gistda/tiles/flood-${floodWindowSelect.value}/{z}/{x}/{y}.png`);
  loadFlood();
  if (state.scope) loadSatellitePriority();
});
floodFreqToggle.addEventListener("change", () => {
  floodFreqToggle.checked ? state.floodFreqTiles.addTo(map) : map.removeLayer(state.floodFreqTiles);
});
document.querySelector("#dam-toggle").addEventListener("change", (event) => {
  event.target.checked ? state.damLayer.addTo(map) : map.removeLayer(state.damLayer);
  damPanelEl.hidden = !event.target.checked;
});
document.querySelector("#flood-point-toggle").addEventListener("change", (event) => {
  event.target.checked ? state.floodPointLayer.addTo(map) : map.removeLayer(state.floodPointLayer);
  updateLayerCounts();
});
document.querySelector("#reset-view").addEventListener("click", () => map.flyTo([13.2, 101.2], 6, { duration: .7 }));

// Redraws every marker layer from its already-fetched data, so this never re-hits the API.
const riskOnlyToggle = document.querySelector("#risk-only-toggle");
riskOnlyToggle.checked = state.riskOnly;  // reflect the persisted choice (checkbox defaults to checked in the markup)
riskOnlyToggle.addEventListener("change", (event) => {
  state.riskOnly = event.target.checked;
  storage.set(MARKER_MODE_KEY, state.riskOnly ? "risk" : "all");
  renderFloodPoints();
  for (const layer of Object.keys(THAIWATER_LAYERS)) {
    if (document.querySelector(THAIWATER_LAYERS[layer].toggle).checked) renderThaiWater(layer);
  }
});

// ---------------------------------------------------------------- current location
const locateButton = document.querySelector("#locate-me");
const LOCATE_ERRORS = {
  1: "ไม่ได้รับอนุญาตให้เข้าถึงตำแหน่ง · เปิดสิทธิ์ตำแหน่งในเบราว์เซอร์",
  2: "หาตำแหน่งปัจจุบันไม่ได้",
  3: "หาตำแหน่งนานเกินไป · ลองอีกครั้ง",
};
state.locationLayer = L.layerGroup().addTo(map);

function showLocation(lat, lon, accuracy) {
  state.locationLayer.clearLayers();
  // Accuracy ring shares the station canvas but is not interactive, so it never eats clicks.
  L.circle([lat, lon], {
    renderer: stationRenderer, radius: accuracy, interactive: false,
    color: "#2f7df6", weight: 1, fillColor: "#2f7df6", fillOpacity: 0.1,
  }).addTo(state.locationLayer);
  L.marker([lat, lon], {
    icon: L.divIcon({ className: "location-icon", html: '<div class="location-dot"></div>', iconSize: [22, 22], iconAnchor: [11, 11] }),
    keyboard: false, zIndexOffset: 1000,
  }).bindPopup(`<div class="popup-title">ตำแหน่งของคุณ</div>
      <div class="popup-grid"><span>พิกัด</span><b>${lat.toFixed(5)}, ${lon.toFixed(5)}</b>
      <span>ความแม่นยำ</span><b>± ${formatNumber(accuracy, 0)} ม.</b></div>`)
    .addTo(state.locationLayer);
}

locateButton.addEventListener("click", () => {
  if (!("geolocation" in navigator)) { showToast("เบราว์เซอร์นี้ไม่รองรับการหาตำแหน่ง", "warn"); return; }
  locateButton.classList.add("loading");
  locateButton.disabled = true;
  navigator.geolocation.getCurrentPosition((position) => {
    locateButton.classList.remove("loading");
    locateButton.disabled = false;
    locateButton.classList.add("active");
    const { latitude: lat, longitude: lon, accuracy } = position.coords;
    showLocation(lat, lon, accuracy);
    map.flyTo([lat, lon], Math.max(map.getZoom(), 13), { duration: .7 });
    inspectPoint({ latlng: L.latLng(lat, lon) });  // rain and river outlook for where the user is
  }, (error) => {
    locateButton.classList.remove("loading");
    locateButton.disabled = false;
    showToast(LOCATE_ERRORS[error.code] || "หาตำแหน่งไม่สำเร็จ", "warn");
  }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
});

// ---------------------------------------------------------------- Base map + traffic switcher
const basemapButtons = document.querySelectorAll("[data-basemap]");
const trafficButton = document.querySelector("#traffic-toggle");
let trafficConfigured = null;  // null until /traffic/status answers

function setBasemap(name) {
  if (name !== activeBasemap) {
    map.removeLayer(BASEMAPS[activeBasemap]);
    BASEMAPS[name].addTo(map);
    activeBasemap = name;
    storage.set("basemap", name);
  }
  for (const button of basemapButtons) button.setAttribute("aria-pressed", String(button.dataset.basemap === name));
}

function setTraffic(on) {
  if (on) trafficLayer.addTo(map); else map.removeLayer(trafficLayer);
  trafficButton.setAttribute("aria-pressed", String(on));
  storage.set("traffic", on ? "1" : "0");
}

async function loadTrafficStatus() {
  try {
    trafficConfigured = (await getJSON("/api/v1/traffic/status")).configured;
  } catch {
    trafficConfigured = false;
  }
  trafficButton.setAttribute("aria-disabled", String(!trafficConfigured));
  trafficButton.title = trafficConfigured ? "การจราจร (TomTom)" : "ยังไม่ได้ตั้งค่าชั้นจราจร (TOMTOM_API_KEY)";
  if (trafficConfigured && storage.get("traffic") === "1") setTraffic(true);
}

// One warning per failure burst, not one per failed tile.
let trafficErrorShown = false;
trafficLayer.on("tileerror", () => {
  if (trafficErrorShown) return;
  trafficErrorShown = true;
  showToast("โหลดข้อมูลจราจรไม่สำเร็จ", "warn");
});
trafficLayer.on("load", () => { trafficErrorShown = false; });

for (const button of basemapButtons) button.addEventListener("click", () => setBasemap(button.dataset.basemap));
trafficButton.addEventListener("click", () => {
  if (!trafficConfigured) {
    showToast(trafficConfigured === null ? "กำลังตรวจสอบชั้นจราจร…" : "ยังไม่ได้ตั้งค่าชั้นจราจร (TOMTOM_API_KEY)", "warn");
    return;
  }
  setTraffic(!map.hasLayer(trafficLayer));
});
setBasemap(activeBasemap);
loadTrafficStatus();
async function refreshAll() {
  if (state.scope) loadSatellitePriority();
  const results = (await Promise.all([loadRadar(), loadFlood(), loadDams(), loadFloodPoints(), loadAllThaiWater(), loadAlerts()]))
    .flat().filter((r) => r && !r.skipped);
  return results;
}

function refreshSummary(results) {
  const failed = results.filter((r) => !r.ok);
  const stale = results.filter((r) => r.ok && r.stale);
  const live = results.filter((r) => r.ok && r.status === "live");
  const cached = results.filter((r) => r.ok && r.status === "cached");
  if (!results.length) return { text: "ไม่มีชั้นข้อมูลที่เปิดอยู่", ok: true };
  if (failed.length === results.length) return { text: "รีเฟรชไม่สำเร็จ · เชื่อมต่อแหล่งข้อมูลไม่ได้", ok: false };
  const parts = [];
  if (live.length) parts.push(`ข้อมูลใหม่ ${live.length} แหล่ง`);
  if (cached.length) parts.push(`ยังเป็นรอบล่าสุด ${cached.length} แหล่ง`);
  if (stale.length) parts.push(`ข้อมูลสำรอง: ${stale.map((r) => r.source).join(", ")}`);
  if (failed.length) parts.push(`ล้มเหลว: ${failed.map((r) => r.source).join(", ")}`);
  return { text: parts.join(" · "), ok: !failed.length && !stale.length };
}

refreshButton.addEventListener("click", async () => {
  refreshButton.classList.add("loading");
  refreshButton.disabled = true;
  const summary = refreshSummary(await refreshAll());
  refreshButton.classList.remove("loading");
  refreshButton.disabled = false;
  showToast(summary.text, summary.ok ? "ok" : "warn");
});
sidebarButton.addEventListener("click", () => {
  setSidebarOpen(!document.body.classList.contains("sidebar-open"));
});
mapAlertSummaryEl.addEventListener("click", () => setSidebarOpen(true, prioritySectionEl));
map.on("click", (event) => {
  inspectPoint(event);
  if (window.innerWidth <= 820) setSidebarOpen(true, selectedSectionEl);
});
map.on("moveend", () => {
  loadFlood(); loadFloodPoints(); loadAllThaiWater();
  if (state.keepPriorityOnce) state.keepPriorityOnce = false;
  else if (!state.scope) loadAlerts();
});

loadProvinceForecast();
loadAreaChoices(null, areaProvinceEl, "เลือกจังหวัด");
loadDamPhotos();
setInterval(loadProvinceForecast, 60 * 60 * 1000);  // backend caches 3 h; this only picks that up
loadProvinces().then(() => {
  priorityScopeEl.value = state.scope;
  if (state.scope) setScope(state.scope); else loadAlerts();
});
refreshAll();
setInterval(loadRadar, 5 * 60 * 1000);
setInterval(loadDams, 60 * 60 * 1000);
setInterval(loadFloodPoints, 5 * 60 * 1000);
setInterval(loadAlerts, 5 * 60 * 1000);
setInterval(loadAllThaiWater, 5 * 60 * 1000);
