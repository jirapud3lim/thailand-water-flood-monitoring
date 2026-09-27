// Pure display rules: which markers are drawn, why the rest are hidden, how counts and short
// status texts read. No DOM or Leaflet here, so tests/frontend/rules.test.mjs can run them in
// Node. Loaded as a classic script before app.js (globals under window.Rules).
(function (root) {
  // "แสดงเฉพาะจุดเสี่ยง": what counts as a risk per layer. Rain uses a stricter floor (>35 mm/24h)
  // since it is most of the dots on the map; layers without a key (dams, watergates) are never
  // filtered by this mode because they aren't severity-scored.
  const RISK_LEVELS = {
    "flood-points": new Set(["moderate", "high", "critical"]),
    "water-level": new Set(["moderate", "high", "critical"]),
    canal: new Set(["moderate", "high", "critical"]),
    rain: new Set(["high", "critical"]),
  };
  const isRiskVisible = (layer, severity) => !RISK_LEVELS[layer] || RISK_LEVELS[layer].has(severity);

  // Zoom rules that existed before risk-only mode; they still apply in both modes.
  const ALERT_LEVELS = new Set(["moderate", "high", "critical"]);
  const FLOOD_POINT_FAR_LEVELS = new Set(["low", "moderate", "high", "critical"]);
  const FLOOD_POINT_FAR_ZOOM = 7;   // DPM pins at or below this zoom: alert levels only
  const RAIN_ZERO_MIN_ZOOM = 11;    // rain gauges with 0 mm show only from this zoom

  function passesZoom(layer, props, zoom, minZoom) {
    const severity = props.severity;
    if (layer === "flood-points") return zoom > FLOOD_POINT_FAR_ZOOM || FLOOD_POINT_FAR_LEVELS.has(severity);
    if (zoom <= 6 && layer === "rain") return severity === "critical";
    if (zoom <= 6 && layer === "water-level") return severity === "critical" || severity === "high";
    if (minZoom != null && zoom < minZoom) return ALERT_LEVELS.has(severity);
    if (layer === "rain" && zoom < RAIN_ZERO_MIN_ZOOM) return props.rain_24h_mm > 0;
    return true;
  }

  // Every loaded feature lands in exactly one bucket, so shown + every hidden bucket always adds
  // up to what was loaded for the view. Order of checks = order of reasons shown to the user.
  // `forced` keys (open popup, station focused from the list) are always shown.
  function classify(layer, features, { zoom, minZoom = null, riskOnly = true, forced = new Set(), keyOf = (f) => f.properties.id, covered = null }) {
    const out = { loaded: features.length, shown: [], zoom: [], risk: [], twin: [] };
    for (const feature of features) {
      const p = feature.properties;
      if (forced.has(keyOf(feature))) out.shown.push(feature);
      else if (!passesZoom(layer, p, zoom, minZoom)) out.zoom.push(feature);
      else if (riskOnly && !isRiskVisible(layer, p.severity)) out.risk.push(feature);
      else if (covered && covered(feature)) out.twin.push(feature);
      else out.shown.push(feature);
    }
    return out;
  }

  const HIDDEN_SEVERITY_LABEL = { normal: "ปกติ", low: "เฝ้าระวังต่ำ", moderate: "เฝ้าระวัง", high: "สูง", critical: "วิกฤต", unknown: "ไม่มีข้อมูล" };
  function severityBreakdown(features) {
    const counts = new Map();
    for (const f of features) {
      const label = HIDDEN_SEVERITY_LABEL[f.properties.severity] || HIDDEN_SEVERITY_LABEL.unknown;
      counts.set(label, (counts.get(label) || 0) + 1);
    }
    return [...counts].map(([label, n]) => `${label} ${n}`).join(" · ");
  }

  const fmt = (n) => Number(n).toLocaleString("th-TH");
  // One line per layer, e.g. "แสดง 69 จาก 474 · ซ่อน: ต่ำกว่าเกณฑ์ 353, ซ้ำกับ ThaiWater 52".
  // The hidden reasons are listed separately and each is a count of the same loaded set.
  function layerCountText(c, unit = "หมุด", { riskLabel = "ต่ำกว่าเกณฑ์" } = {}) {
    if (!c.loaded) return `ไม่มี${unit}ในจอ`;
    const shown = c.shown.length;
    if (shown === c.loaded) return `แสดงครบ ${fmt(shown)} ${unit}ในจอ`;
    const reasons = [
      c.risk.length ? `${riskLabel} ${fmt(c.risk.length)}` : "",
      c.zoom.length ? `ซูมเข้าเพื่อดู ${fmt(c.zoom.length)}` : "",
      c.twin.length ? `ซ้ำกับ ThaiWater ${fmt(c.twin.length)}` : "",
    ].filter(Boolean).join(", ");
    return `แสดง ${fmt(shown)} จาก ${fmt(c.loaded)} ${unit} · ซ่อน: ${reasons}`;
  }

  // Greedy screen-space grouping, worst severity first so each group is anchored on (and drawn
  // at) its most severe member — a group of ordinary pins can never sit on top of a critical one.
  // points: [{x, y, rank, key}] in pixels; `forced` keys never join a group.
  function clusterPoints(points, radius, forced = new Set()) {
    const sorted = [...points].sort((a, b) => b.rank - a.rank);
    const groups = [];
    const r2 = radius * radius;
    for (const point of sorted) {
      if (!forced.has(point.key)) {
        const group = groups.find((g) => !g.forced && (g.anchor.x - point.x) ** 2 + (g.anchor.y - point.y) ** 2 <= r2);
        if (group) { group.members.push(point); continue; }
      }
      groups.push({ anchor: point, members: [point], forced: forced.has(point.key) });
    }
    return groups;
  }

  // Header status that still reads without colour, short enough for a phone header.
  function statusText(total, failed, stale) {
    if (!total) return { short: "…", long: "กำลังเชื่อมต่อ…", level: "offline" };
    if (failed === total) return { short: "ออฟไลน์", long: "เชื่อมต่อแหล่งข้อมูลไม่ได้", level: "offline" };
    if (failed) return { short: `ขาด ${failed}/${total}`, long: `ขาด ${failed}/${total} แหล่ง`, level: "degraded" };
    if (stale) return { short: `สำรอง ${stale}/${total}`, long: `ข้อมูลสำรอง ${stale}/${total} แหล่ง`, level: "degraded" };
    return { short: `ครบ ${total}`, long: `ออนไลน์ · ครบ ${total} แหล่ง`, level: "online" };
  }

  // KPI label: says where it counts and that a station seen by two agencies counts once.
  const kpiLabel = (scope) => `จุดถึงระดับเตือน${scope ? `ใน จ.${scope}` : "ในจอ"} · นับไม่ซ้ำ`;

  const Rules = {
    RISK_LEVELS, isRiskVisible, ALERT_LEVELS, RAIN_ZERO_MIN_ZOOM, FLOOD_POINT_FAR_ZOOM,
    passesZoom, classify, severityBreakdown, layerCountText, clusterPoints, statusText, kpiLabel,
  };
  if (typeof module === "object" && module.exports) module.exports = Rules;
  else root.Rules = Rules;
})(typeof window !== "undefined" ? window : globalThis);
