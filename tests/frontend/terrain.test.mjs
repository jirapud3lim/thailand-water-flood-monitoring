import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const Terrain = require("../../frontend/terrain.js");

test("Terrarium channels decode to metres, including fractions", () => {
  assert.equal(Terrain.decode(128, 0, 0), 0);
  assert.equal(Terrain.decode(128, 1, 128), 1.5);
  assert.equal(Terrain.decode(127, 255, 0), -1);
});

test("elevation bands are display-only and negative bathymetry is masked", () => {
  assert.deepEqual(Terrain.colorForElevation(0), Terrain.BANDS[0].color);
  assert.deepEqual(Terrain.colorForElevation(5), Terrain.BANDS[1].color);
  assert.deepEqual(Terrain.colorForElevation(500), Terrain.BANDS[4].color);
  assert.equal(Terrain.colorForElevation(-1), null);
  assert.equal(Terrain.colorForElevation(NaN), null);
});

test("point sample selects valid tile and pixel at map bounds", () => {
  const thailand = Terrain.tileForLatLng(13.2, 101.2);
  assert.equal(thailand.z, 12);
  assert.ok(thailand.x >= 0 && thailand.x < 4096);
  assert.ok(thailand.y >= 0 && thailand.y < 4096);
  assert.ok(thailand.pixelX >= 0 && thailand.pixelX < 256);
  assert.ok(thailand.pixelY >= 0 && thailand.pixelY < 256);
  assert.equal(Terrain.tileForLatLng(0, 180, 0).x, 0);
  assert.equal(Terrain.tileForLatLng(95, 101), null);
});

test("point sample rejects transparent pixels and decodes land", () => {
  const data = new Uint8ClampedArray([128, 0, 0, 255, 0, 0, 0, 0]);
  assert.equal(Terrain.samplePixel(data, 0, 0, 2), 0);
  assert.equal(Terrain.samplePixel(data, 1, 0, 2), null);
});
