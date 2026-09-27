// Mapzen Terrarium display helpers. Pure math so the tile colours and point readout
// use the same decoding; these values are terrain context, never a flood-risk score.
(function (root) {
  const TILE_SIZE = 256;
  const SAMPLE_ZOOM = 12;
  const BANDS = [
    { max: 5, color: [75, 121, 169], label: "0–5 ม." },
    { max: 20, color: [76, 158, 167], label: "5–20 ม." },
    { max: 100, color: [105, 174, 137], label: "20–100 ม." },
    { max: 500, color: [180, 179, 119], label: "100–500 ม." },
    { max: Infinity, color: [208, 185, 142], label: "มากกว่า 500 ม." },
  ];

  const decode = (red, green, blue) => (red * 256 + green + blue / 256) - 32768;

  function colorForElevation(elevation) {
    // Negative ocean bathymetry is not land elevation. Mask it instead of painting
    // the sea as a low-lying flood-risk area. On-land negatives are also omitted.
    if (!Number.isFinite(elevation) || elevation < 0) return null;
    return BANDS.find((band) => elevation < band.max).color;
  }

  function tileForLatLng(lat, lon, zoom = SAMPLE_ZOOM) {
    if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < -85 || lat > 85 || lon < -180 || lon > 180 ||
        !Number.isInteger(zoom) || zoom < 0 || zoom > SAMPLE_ZOOM) return null;
    const n = 2 ** zoom;
    const xWorld = Math.min(n - 1e-9, Math.max(0, ((lon + 180) / 360) * n));
    const mercator = Math.asinh(Math.tan(lat * Math.PI / 180));
    const yWorld = Math.min(n - 1e-9, Math.max(0, ((1 - mercator / Math.PI) / 2) * n));
    return {
      z: zoom, x: Math.floor(xWorld), y: Math.floor(yWorld),
      pixelX: Math.min(TILE_SIZE - 1, Math.floor((xWorld % 1) * TILE_SIZE)),
      pixelY: Math.min(TILE_SIZE - 1, Math.floor((yWorld % 1) * TILE_SIZE)),
    };
  }

  function samplePixel(data, x, y, width = TILE_SIZE) {
    const i = (y * width + x) * 4;
    if (i < 0 || i + 3 >= data.length || data[i + 3] === 0) return null;
    const value = decode(data[i], data[i + 1], data[i + 2]);
    return value < 0 ? null : value;
  }

  const Terrain = { TILE_SIZE, SAMPLE_ZOOM, BANDS, decode, colorForElevation, tileForLatLng, samplePixel };
  if (typeof module !== "undefined" && module.exports) module.exports = Terrain;
  else root.Terrain = Terrain;
})(typeof window !== "undefined" ? window : globalThis);
