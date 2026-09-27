const map = L.map("map", { preferCanvas: true }).setView([13.2, 101.2], 6);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "© OpenStreetMap contributors",
}).addTo(map);

const state = {
  radarFrames: [], radarHost: "", radarLayer: null, radarVisibleLayer: null, floodLayer: null,
  damLayer: L.layerGroup(), floodPointLayer: L.layerGroup(), timer: null,
  damCount: 0, floodPointCount: 0, visibleFloodPointCount: 0,
  radarTransitionId: 0, floodRequestId: 0, inspectRequestId: 0,
  markerByKey: new Map(), pendingFocusKey: null,
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
const mapRadarStatusEl = document.querySelector("#map-radar-status");
const selectedSectionEl = document.querySelector("#selected-title").closest("section");
const prioritySectionEl = document.querySelector("#priority-title").closest("section");
const severityColors = {
  normal: "#36c98f", low: "#9bd653", moderate: "#f1c84b",
  high: "#ff8a3d", critical: "#ff4d61", unknown: "#8999a6",
};
const severityLabels = {
  normal: "ปกติ", low: "เฝ้าระวังต่ำ", moderate: "เฝ้าระวัง",
  high: "สูง", critical: "วิกฤต", unknown: "ไม่มีข้อมูล",
};
const severitySymbols = {
  normal: "○", low: "·", moderate: "≈", high: "▲", critical: "!", unknown: "?",
};
const severityOrder = { critical: 5, high: 4, moderate: 3, low: 2, normal: 1, unknown: 0 };
const RIVER_GAUGE_NOTE = "ระดับน้ำในลำน้ำ ไม่ใช่การยืนยันน้ำท่วมพื้นที่รอบสถานี";

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

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function setConnection(text, level = "offline", detail = "") {
  statusEl.classList.toggle("online", level === "online");
  statusEl.classList.toggle("degraded", level === "degraded");
  statusEl.querySelector("span").textContent = text;
  statusEl.title = detail || text;
  statusEl.setAttribute("aria-label", detail || text);
}

function renderHealth() {
  const active = [...state.outcomes.values()].filter((r) => !r.skipped);
  if (!active.length) return;
  const failed = active.filter((r) => !r.ok);
  const stale = active.filter((r) => r.ok && r.stale);
  const detail = [
    failed.length ? `ล้มเหลว: ${failed.map((r) => r.source).join(", ")}` : "",
    stale.length ? `ใช้ข้อมูลสำรอง: ${stale.map((r) => r.source).join(", ")}` : "",
  ].filter(Boolean).join(" · ");
  if (failed.length === active.length) {
    setConnection("เชื่อมต่อแหล่งข้อมูลไม่ได้", "offline", detail);
    freshnessEl.textContent = "ตรวจสอบไม่ได้";
  } else if (failed.length || stale.length) {
    const text = failed.length ? `ขาด ${failed.length}/${active.length} แหล่ง` : `ข้อมูลสำรอง ${stale.length} แหล่ง`;
    setConnection(text, "degraded", detail);
    freshnessEl.textContent = "ข้อมูลไม่ครบ";
  } else {
    setConnection(`ออนไลน์ · ครบ ${active.length} แหล่ง`, "online");
    freshnessEl.textContent = `ตรวจล่าสุด ${new Date().toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" })}`;
  }
}

let toastTimer;
function showToast(text, tone = "ok") {
  toastEl.textContent = text;
  toastEl.classList.toggle("warn", tone === "warn");
  toastEl.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("show"), 2200);
}

function setSidebarOpen(open, focusSection = null) {
  document.body.classList.toggle("sidebar-open", open);
  sidebarButton.setAttribute("aria-expanded", String(open));
  sidebarButton.setAttribute("aria-label", open ? "ปิดเมนู" : "เปิดเมนู");
  if (open && focusSection) focusSection.scrollIntoView({ behavior: "smooth", block: "start" });
  setTimeout(() => map.invalidateSize(), 260);
}

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
    showRadar(Number(radarRange.value));
    return record(outcome("เรดาร์ฝน", result));
  } catch (error) {
    mapRadarStatusEl.textContent = "โหลดเวลาเรดาร์ไม่สำเร็จ";
    return failure("เรดาร์ฝน", error);
  }
}

function markFloodNotConfigured() {
  state.floodNotConfigured = true;
  const toggle = document.querySelector("#flood-toggle");
  toggle.checked = false;
  toggle.disabled = true;
  const control = document.querySelector('label[for="flood-toggle"]');
  control.classList.add("is-unavailable");
  control.querySelector("small").textContent = "ยังไม่ได้เชื่อมต่อ GISTDA · ไม่ใช่การยืนยันว่าไม่มีน้ำท่วม";
  control.title = "ต้องตั้งค่า GISTDA_FLOOD_URL และ API key ก่อน (ดู README)";
  if (state.floodLayer && map.hasLayer(state.floodLayer)) map.removeLayer(state.floodLayer);
}

async function loadFlood() {
  const skipped = { source: "ขอบเขตน้ำท่วม (GISTDA)", skipped: true };
  if (state.floodNotConfigured) return record(skipped);
  const b = map.getBounds();
  const params = new URLSearchParams({
    min_lon: b.getWest(), min_lat: b.getSouth(), max_lon: b.getEast(), max_lat: b.getNorth(),
  });
  try {
    const result = await getJSON(`/api/v1/flood/current?${params}`);
    if (result.meta?.status === "not_configured" || result.data?.source_status === "not_configured") {
      markFloodNotConfigured();
      return record(skipped);
    }
    if (state.floodLayer) map.removeLayer(state.floodLayer);
    state.floodLayer = L.geoJSON(result.data, {
      renderer: L.canvas(),
      style: { color: "#ff5c66", weight: 1, fillColor: "#ff3948", fillOpacity: 0.38 },
    });
    if (document.querySelector("#flood-toggle").checked) state.floodLayer.addTo(map);
    return record(outcome("ขอบเขตน้ำท่วม (GISTDA)", result));
  } catch (error) {
    return failure("ขอบเขตน้ำท่วม (GISTDA)", error);
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

function updateLayerCounts() {
  const pointSummary = state.outcomes.get("จุดเฝ้าระวัง ปภ.")?.ok === false
    ? "จุดเฝ้าระวัง —"
    : state.visibleFloodPointCount < state.floodPointCount
      ? `${state.visibleFloodPointCount}/${state.floodPointCount} จุด (ซูมเข้าเพื่อดูทั้งหมด)`
      : `${state.floodPointCount} จุดเฝ้าระวังในหน้าจอ`;
  const damText = state.outcomes.get("เขื่อน")?.ok === false ? "เขื่อน —" : `${state.damCount} เขื่อน`;
  layerCounts.textContent = `${damText} · ${pointSummary}`;
  if (state.outcomes.get("เขื่อน")?.ok) { damCountEl.textContent = formatNumber(state.damCount, 0); damCountEl.title = ""; }
}

function damIcon() {
  return L.divIcon({
    className: "dam-icon",
    html: '<div class="dam-pin"><span>▰</span></div>',
    iconSize: [30, 30], iconAnchor: [15, 28], popupAnchor: [0, -27],
  });
}

function floodPointIcon(severity) {
  const color = severityColors[severity] || severityColors.unknown;
  const symbol = severitySymbols[severity] || severitySymbols.unknown;
  return L.divIcon({
    className: "flood-point-icon",
    html: `<div class="flood-pin" style="--marker:${color}"><span>${symbol}</span></div>`,
    iconSize: [30, 30], iconAnchor: [15, 28], popupAnchor: [0, -27],
  });
}

function floodPointKey(feature) {
  const p = feature.properties || {};
  const [lon, lat] = feature.geometry.coordinates;
  return `${p.point_type}:${p.id || p.name}:${lon.toFixed(5)}:${lat.toFixed(5)}`;
}

function focusPriority(feature) {
  const [lon, lat] = feature.geometry.coordinates;
  const key = floodPointKey(feature);
  const toggle = document.querySelector("#flood-point-toggle");
  if (!toggle.checked) {
    toggle.checked = true;
    state.floodPointLayer.addTo(map);
  }
  state.pendingFocusKey = key;
  state.markerByKey.get(key)?.openPopup();
  setSidebarOpen(false);
  map.flyTo([lat, lon], Math.max(map.getZoom(), 10), { duration: .65 });
}

function renderPriority(features, observedAt, stale) {
  const alerts = features.filter((feature) => ["low", "moderate", "high", "critical"].includes(feature.properties.severity));
  alerts.sort((a, b) => severityOrder[b.properties.severity] - severityOrder[a.properties.severity]
    || String(b.properties.observed_at || "").localeCompare(String(a.properties.observed_at || "")));
  alertCountEl.textContent = formatNumber(alerts.length, 0);
  priorityKpiEl.classList.toggle("has-alerts", alerts.length > 0);
  priorityMetaEl.textContent = `${alerts.length} จุดในจอ`;
  priorityObservedEl.textContent = observedAt
    ? `ข้อมูลสถานี ปภ. ล่าสุด ${formatObserved(observedAt)}${stale ? " · ข้อมูลสำรอง" : ""}`
    : "ไม่ทราบเวลาข้อมูลสถานี ปภ.";
  mapAlertSummaryEl.textContent = `${alerts.length} จุดเตือนในจอ`;
  mapAlertSummaryEl.classList.toggle("has-alerts", alerts.length > 0);
  mapAlertSummaryEl.setAttribute("aria-label", `${alerts.length} จุดเตือนจาก ปภ. ในพื้นที่ที่เห็น เปิดรายการจุดเตือน`);
  priorityListEl.replaceChildren();
  if (!alerts.length) {
    const empty = document.createElement("p");
    empty.className = "priority-empty";
    empty.textContent = "ไม่พบจุดถึงระดับเตือนจากสถานี ปภ. ในพื้นที่ที่เห็น · ข้อมูลนี้ไม่ยืนยันว่าพื้นที่ปลอดน้ำท่วม";
    priorityListEl.append(empty);
    return;
  }
  for (const [type, label] of [["road_flood", "น้ำขังถนน"], ["river_gauge", "ระดับน้ำแม่น้ำ"]]) {
    const group = alerts.filter((feature) => feature.properties.point_type === type);
    if (!group.length) continue;
    const heading = document.createElement("div");
    heading.className = "priority-group";
    heading.innerHTML = `${label}<span>${group.length} จุด</span>`;
    priorityListEl.append(heading);
    for (const feature of group.slice(0, 3)) {
      const p = feature.properties;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "priority-item";
      button.style.setProperty("--marker", severityColors[p.severity]);
      button.innerHTML = `<span class="priority-badge" aria-hidden="true">${severitySymbols[p.severity]}</span>
        <span class="priority-copy"><strong>${escapeHtml(p.name)}</strong>
        <small>${escapeHtml(p.province)} · ${escapeHtml(formatObserved(p.observed_at))}</small></span>
        <span class="priority-level">${severityLabels[p.severity]}</span>`;
      button.setAttribute("aria-label", `${p.name} ${label} ระดับ${severityLabels[p.severity]} กดเพื่อดูบนแผนที่`);
      button.addEventListener("click", () => focusPriority(feature));
      priorityListEl.append(button);
    }
  }
}

async function loadDams() {
  try {
    const result = await getJSON("/api/v1/dams");
    state.damLayer.clearLayers();
    for (const feature of result.data.features || []) {
      const [lon, lat] = feature.geometry.coordinates;
      const p = feature.properties;
      L.marker([lat, lon], { icon: damIcon(), riseOnHover: true })
        .bindPopup(`<div class="popup-title">${escapeHtml(p.name)}</div>
          <div class="popup-grid">
            <span>จังหวัด</span><b>${escapeHtml(p.province)}</b>
            <span>น้ำในเขื่อน</span><b>${formatNumber(p.volume)} ล้าน ลบ.ม.</b>
            <span>ความจุ</span><b>${formatNumber(p.percent_storage)}%</b>
            <span>สถานะกักเก็บ</span><b>${escapeHtml(p.storage_label)}</b>
            <span>ไหลเข้า</span><b>${formatNumber(p.inflow)} ล้าน ลบ.ม.</b>
            <span>ระบาย</span><b>${formatNumber(p.outflow)} ล้าน ลบ.ม.</b>
            <span>วันที่ข้อมูล</span><b>${escapeHtml(p.observed_at)}</b>
          </div><div class="popup-note">สถานะกักเก็บใช้เกณฑ์แสดงผลของโครงการ ไม่ใช่ระดับน้ำท่วม</div>
          <div class="popup-source">${escapeHtml(p.source)}</div>`)
        .addTo(state.damLayer);
    }
    state.damCount = result.data.features?.length || 0;
    updateLayerCounts();
    if (document.querySelector("#dam-toggle").checked && !map.hasLayer(state.damLayer)) state.damLayer.addTo(map);
    return record(outcome("เขื่อน", result));
  } catch (error) {
    damCountEl.textContent = "—";  // unknown, not zero: a failed join must not read as "no dams"
    damCountEl.title = "โหลดข้อมูลเขื่อนไม่สำเร็จ";
    const result = failure("เขื่อน", error);
    updateLayerCounts();
    return result;
  }
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
    state.floodPointLayer.clearLayers();
    state.markerByKey = new Map();
    const allFeatures = result.data.features || [];
    const alertLevels = new Set(["low", "moderate", "high", "critical"]);
    const visibleFeatures = map.getZoom() <= 7
      ? allFeatures.filter((feature) => alertLevels.has(feature.properties.severity))
      : allFeatures;
    for (const feature of visibleFeatures) {
      const [lon, lat] = feature.geometry.coordinates;
      const p = feature.properties;
      const isRoad = p.point_type === "road_flood";
      const measurement = isRoad
        ? `<span>ระดับน้ำขัง</span><b>${formatNumber(p.water_depth_cm)} ซม.</b>
           <span>ค่าสูงสุด</span><b>${formatNumber(p.maximum_depth_cm)} ซม.</b>`
        : `<span>ระดับน้ำ</span><b>${formatNumber(p.water_level_msl)} ม.รทก.</b>
           <span>ระดับตลิ่ง</span><b>${formatNumber(p.bank_level)} ม.รทก.</b>
           <span>ห่างจากตลิ่ง</span><b>${formatNumber(p.bank_clearance_m)} ม.</b>`;
      const marker = L.marker([lat, lon], {
        icon: floodPointIcon(p.severity), riseOnHover: true,
        title: `${p.name} · ${severityLabels[p.severity] || severityLabels.unknown}`,
      })
        .bindPopup(`<div class="popup-title">${escapeHtml(p.name)}</div>
          <div class="popup-grid">
            <span>ประเภท</span><b>${isRoad ? "จุดวัดน้ำท่วมขังถนน" : "สถานีระดับน้ำแม่น้ำ"}</b>
            <span>ระดับตามเกณฑ์แผนที่</span><b>${severityLabels[p.severity] || severityLabels.unknown}</b>
            ${measurement}
            <span>จังหวัด</span><b>${escapeHtml(p.province)}</b>
            <span>เวลา</span><b>${escapeHtml(p.observed_at)}</b>
          </div>${isRoad ? "" : `<div class="popup-note">${RIVER_GAUGE_NOTE}</div>`}
          <div class="popup-source">${escapeHtml(p.source)}</div>`)
        .addTo(state.floodPointLayer);
      const key = floodPointKey(feature);
      state.markerByKey.set(key, marker);
      if (state.pendingFocusKey === key) {
        state.pendingFocusKey = null;
        setTimeout(() => marker.openPopup(), 0);
      }
    }
    state.floodPointCount = allFeatures.length;
    state.visibleFloodPointCount = visibleFeatures.length;
    renderPriority(allFeatures, result.meta?.observed_at_max, result.stale);
    updateLayerCounts();
    if (document.querySelector("#flood-point-toggle").checked && !map.hasLayer(state.floodPointLayer)) state.floodPointLayer.addTo(map);
    return record(outcome("จุดเฝ้าระวัง ปภ.", result));
  } catch (error) {
    if (requestId !== state.floodRequestId) return null;
    alertCountEl.textContent = "—";
    priorityKpiEl.classList.remove("has-alerts");
    priorityMetaEl.textContent = "โหลดไม่สำเร็จ";
    priorityObservedEl.textContent = "ไม่ทราบเวลาข้อมูลสถานี ปภ.";
    mapAlertSummaryEl.textContent = "จุดเตือน: โหลดไม่สำเร็จ";
    mapAlertSummaryEl.classList.remove("has-alerts");
    priorityListEl.innerHTML = '<p class="priority-empty">โหลดข้อมูลจุดเฝ้าระวังไม่สำเร็จ · ลองรีเฟรชอีกครั้ง</p>';
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
    <div class="metric"><strong>ฝนขณะนี้</strong> <span class="tag">แบบจำลอง</span><br>
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
// Below each layer's minZoom only alert-level stations are drawn, to keep low zooms readable.
const ALERT_LEVELS = new Set(["moderate", "high", "critical"]);
// River alerts get their own pane/canvas so they always draw above dense rain gauges.
map.createPane("twAlerts").style.zIndex = 460;
const twRenderer = L.canvas({ padding: 0.3 });
const twAlertRenderer = L.canvas({ padding: 0.3, pane: "twAlerts" });
const withUnit = (value, unit, digits = 2) => (value == null ? "—" : `${formatNumber(value, digits)} ${unit}`);
for (const cfg of Object.values(THAIWATER_LAYERS)) {
  cfg.group = L.layerGroup();
  cfg.requestId = 0;  // bumped per load/toggle-off so late responses are dropped
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
      <span>ระดับเตือนภัย</span><b>${withUnit(p.warning_level_m, "ม.")}</b>
      <span>ระดับวิกฤต</span><b>${withUnit(p.critical_level_m ?? p.bank_level_m, "ม.")}</b>`,
    watergate: `
      <span>น้ำหน้าประตู</span><b>${withUnit(p.upstream_level_m, "ม.")}</b>
      <span>น้ำท้ายประตู</span><b>${withUnit(p.downstream_level_m, "ม.")}</b>
      <span>ผลต่าง</span><b>${withUnit(p.head_diff_m, "ม.")}</b>`,
  }[p.point_type] || "";
  return `<div class="popup-title">${escapeHtml(p.name)}</div>
    <div class="popup-grid">${rows}
      <span>จังหวัด</span><b>${escapeHtml(p.province)}</b>
      <span>เวลา</span><b>${escapeHtml(p.observed_at)}</b>
    </div>${p.point_type === "river_gauge" ? `<div class="popup-note">${RIVER_GAUGE_NOTE}</div>` : ""}
    <div class="popup-source">${escapeHtml(p.source)} · via ${escapeHtml(p.provider)}</div>`;
}

function setTwMeta(cfg, text, offline = false) {
  cfg.metaEl.textContent = text;
  cfg.metaEl.classList.toggle("offline", offline);
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
    const features = result.data.features || [];
    const zoom = map.getZoom();
    const visible = zoom <= 6 && layer === "rain"
      ? features.filter((f) => f.properties.severity === "critical")
      : zoom <= 6 && layer === "water-level"
        ? features.filter((f) => ["critical", "high"].includes(f.properties.severity))
        : zoom < cfg.minZoom
          ? features.filter((f) => ALERT_LEVELS.has(f.properties.severity))
          : features;
    cfg.group.clearLayers();
    for (const feature of visible) {
      const [lon, lat] = feature.geometry.coordinates;
      const p = feature.properties;
      const radius = layer === "rain" ? Math.min(10, cfg.radius + Math.sqrt(p.rain_24h_mm || 0) / 2) : cfg.radius;
      L.circleMarker([lat, lon], {
        renderer: layer === "water-level" ? twAlertRenderer : twRenderer, radius,
        weight: p.severity === "critical" ? 3 : p.severity === "high" ? 2.5 : 1.5,
        color: cfg.stroke,
        fillColor: cfg.color || severityColors[p.severity] || severityColors.unknown, fillOpacity: cfg.opacity ?? 0.92,
      }).bindPopup(() => twPopup(p))
        .bindTooltip(`${severityLabels[p.severity] || severityLabels.unknown} · ${escapeHtml(p.name)}`)
        .addTo(cfg.group);
    }
    const count = visible.length < features.length
      ? `${visible.length}/${features.length} ${cfg.unit} · ${zoom <= 6 && layer === "rain" ? "เฉพาะวิกฤต" : zoom <= 6 && layer === "water-level" ? "สูงและวิกฤต" : "ซูมเพื่อดูทั้งหมด"}`
      : `${features.length} ${cfg.unit}ในหน้าจอ`;
    setTwMeta(cfg, result.stale ? `${count} · cache` : count);
    if (!map.hasLayer(cfg.group)) cfg.group.addTo(map);
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
    else { cfg.requestId++; map.removeLayer(cfg.group); setTwMeta(cfg, cfg.defaultMeta); state.outcomes.delete(cfg.source); renderHealth(); }
  });
}

radarRange.addEventListener("input", () => showRadar(Number(radarRange.value)));
document.querySelector("#radar-play").addEventListener("click", (event) => {
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
document.querySelector("#radar-toggle").addEventListener("change", (event) => {
  mapRadarStatusEl.hidden = !event.target.checked;
  if (!state.radarLayer) return;
  if (event.target.checked) {
    state.radarLayer.setOpacity(0.58).addTo(map);
    state.radarVisibleLayer = state.radarLayer;
  } else {
    if (state.radarVisibleLayer && map.hasLayer(state.radarVisibleLayer)) map.removeLayer(state.radarVisibleLayer);
    if (map.hasLayer(state.radarLayer)) map.removeLayer(state.radarLayer);
  }
});
document.querySelector("#flood-toggle").addEventListener("change", (event) => {
  if (!state.floodLayer) return;
  event.target.checked ? state.floodLayer.addTo(map) : map.removeLayer(state.floodLayer);
});
document.querySelector("#dam-toggle").addEventListener("change", (event) => {
  event.target.checked ? state.damLayer.addTo(map) : map.removeLayer(state.damLayer);
});
document.querySelector("#flood-point-toggle").addEventListener("change", (event) => {
  event.target.checked ? state.floodPointLayer.addTo(map) : map.removeLayer(state.floodPointLayer);
});
document.querySelector("#reset-view").addEventListener("click", () => map.flyTo([13.2, 101.2], 6, { duration: .7 }));
async function refreshAll() {
  const results = (await Promise.all([loadRadar(), loadFlood(), loadDams(), loadFloodPoints(), loadAllThaiWater()]))
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
map.on("moveend", () => { loadFlood(); loadFloodPoints(); loadAllThaiWater(); });

refreshAll();
setInterval(loadRadar, 5 * 60 * 1000);
setInterval(loadDams, 60 * 60 * 1000);
setInterval(loadFloodPoints, 5 * 60 * 1000);
setInterval(loadAllThaiWater, 5 * 60 * 1000);
