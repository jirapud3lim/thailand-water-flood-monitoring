import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const geometry = require("../../frontend/thailand-boundary.js");
const clip = require("../../frontend/country-clip.js");

test("Natural Earth geometry includes mainland and islands", () => {
  assert.equal(geometry.type, "MultiPolygon");
  assert.ok(geometry.coordinates.length > 1);
  assert.equal(clip.contains(100.5, 13.7), true); // Bangkok
  assert.equal(clip.contains(98.3, 7.9), true); // Phuket
  assert.equal(clip.contains(104.5, 12.5), false); // Cambodia, inside old rectangle
  assert.equal(clip.contains(100, 10), false); // Gulf of Thailand
});

test("tile intersection excludes foreign tiles but keeps coastal and large tiles", () => {
  assert.equal(clip.intersectsRect([100.4, 13.6, 100.6, 13.8]), true);
  assert.equal(clip.intersectsRect([104.4, 12.4, 104.6, 12.6]), false);
  assert.equal(clip.intersectsRect([98.1, 7.7, 98.35, 8.0]), true);
  assert.equal(clip.intersectsRect([96, 4, 107, 22]), true);
});

test("inverse mask retains every border ring", () => {
  const rings = clip.maskRings();
  assert.equal(rings.length, 1 + geometry.coordinates.reduce((n, polygon) => n + polygon.length, 0));
  assert.deepEqual(rings[0][0], [85, -180]);
});
