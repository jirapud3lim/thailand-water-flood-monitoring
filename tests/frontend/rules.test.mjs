// Run: node --test tests/frontend
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const Rules = createRequire(import.meta.url)("../../frontend/rules.js");
const f = (id, severity, extra = {}) => ({ properties: { id, severity, ...extra } });

test("every loaded feature lands in exactly one bucket", () => {
  const features = [
    f("a", "critical"), f("b", "normal"), f("c", "low"), f("d", "unknown"), f("e", "moderate", { twin_id: "t1" }),
  ];
  const c = Rules.classify("flood-points", features, {
    zoom: 10, riskOnly: true, covered: (x) => x.properties.twin_id === "t1",
  });
  assert.equal(c.loaded, 5);
  assert.equal(c.shown.length + c.zoom.length + c.risk.length + c.twin.length, c.loaded);
  assert.deepEqual(c.shown.map((x) => x.properties.id), ["a"]);
  assert.deepEqual(c.risk.map((x) => x.properties.id), ["b", "c", "d"]);
  assert.deepEqual(c.twin.map((x) => x.properties.id), ["e"]);
});

test("zoom and risk are separate reasons, never mixed", () => {
  // At zoom 7 DPM pins below "low" are hidden by zoom, "low" passes zoom but fails risk.
  const c = Rules.classify("flood-points", [f("n", "normal"), f("l", "low"), f("h", "high")], { zoom: 7, riskOnly: true });
  assert.deepEqual(c.zoom.map((x) => x.properties.id), ["n"]);
  assert.deepEqual(c.risk.map((x) => x.properties.id), ["l"]);
  assert.deepEqual(c.shown.map((x) => x.properties.id), ["h"]);
});

test("risk-only off keeps the old zoom behaviour", () => {
  const features = [f("n", "normal"), f("l", "low")];
  assert.equal(Rules.classify("flood-points", features, { zoom: 10, riskOnly: false }).shown.length, 2);
  assert.equal(Rules.classify("flood-points", features, { zoom: 6, riskOnly: false }).shown.length, 1);
});

test("rain needs high or critical in risk-only mode", () => {
  const rain = [f("m", "moderate", { rain_24h_mm: 20 }), f("h", "high", { rain_24h_mm: 50 }), f("z", "normal", { rain_24h_mm: 0 })];
  const c = Rules.classify("rain", rain, { zoom: 12, minZoom: 8, riskOnly: true });
  assert.deepEqual(c.shown.map((x) => x.properties.id), ["h"]);
  // Zoomed out, 0 mm gauges are a zoom reason, not a risk reason.
  const far = Rules.classify("rain", rain, { zoom: 9, minZoom: 8, riskOnly: true });
  assert.deepEqual(far.zoom.map((x) => x.properties.id), ["z"]);
  assert.deepEqual(far.risk.map((x) => x.properties.id), ["m"]);
});

test("forced stations stay visible whatever their level", () => {
  const c = Rules.classify("water-level", [f("n", "normal")], { zoom: 5, minZoom: 8, riskOnly: true, forced: new Set(["n"]) });
  assert.equal(c.shown.length, 1);
});

test("watergates and other unscored layers are not filtered by risk", () => {
  assert.equal(Rules.isRiskVisible("watergate", "unknown"), true);
  assert.equal(Rules.isRiskVisible("water-level", "normal"), false);
});

test("count text lists each hidden reason against the same loaded total", () => {
  const c = Rules.classify("flood-points", [f("a", "critical"), f("b", "normal"), f("e", "moderate", { t: 1 })], {
    zoom: 10, riskOnly: true, covered: (x) => x.properties.t === 1,
  });
  assert.equal(Rules.layerCountText(c), "แสดง 1 จาก 3 หมุด · ซ่อน: ต่ำกว่าเกณฑ์ 1, ซ้ำกับ ThaiWater 1");
  assert.equal(Rules.layerCountText({ loaded: 0, shown: [], zoom: [], risk: [], twin: [] }), "ไม่มีหมุดในจอ");
  const all = Rules.classify("flood-points", [f("a", "critical")], { zoom: 10 });
  assert.equal(Rules.layerCountText(all), "แสดงครบ 1 หมุดในจอ");
});

test("clusters anchor on the worst member and never absorb forced points", () => {
  const points = [
    { x: 0, y: 0, rank: 1, key: "normal" },
    { x: 5, y: 5, rank: 5, key: "critical" },
    { x: 8, y: 0, rank: 3, key: "moderate" },
    { x: 200, y: 0, rank: 1, key: "far" },
    { x: 2, y: 2, rank: 1, key: "focused" },
  ];
  const groups = Rules.clusterPoints(points, 30, new Set(["focused"]));
  const near = groups.find((g) => g.members.length > 1);
  assert.equal(near.anchor.key, "critical");
  assert.deepEqual(near.members.map((m) => m.key).sort(), ["critical", "moderate", "normal"]);
  assert.ok(groups.some((g) => g.anchor.key === "focused" && g.members.length === 1));
  assert.ok(groups.some((g) => g.anchor.key === "far" && g.members.length === 1));
});

test("status text never relies on colour alone", () => {
  assert.equal(Rules.statusText(6, 0, 0).short, "ครบ 6");
  assert.equal(Rules.statusText(6, 1, 0).short, "ขาด 1/6");
  assert.equal(Rules.statusText(6, 0, 2).short, "สำรอง 2/6");
  assert.equal(Rules.statusText(6, 6, 0).short, "ออฟไลน์");
});

test("KPI label names its scope and the de-duplication", () => {
  assert.equal(Rules.kpiLabel(""), "จุดถึงระดับเตือนในจอ · นับไม่ซ้ำ");
  assert.equal(Rules.kpiLabel("สุโขทัย"), "จุดถึงระดับเตือนใน จ.สุโขทัย · นับไม่ซ้ำ");
});
