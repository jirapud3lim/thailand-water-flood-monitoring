// Geometry helpers for the national tile filter and the inverse map mask.
// Input coordinates are GeoJSON [longitude, latitude]. No network request is needed at runtime.
(function (root) {
  const geometry = root.THAILAND_GEOMETRY || (typeof module !== "undefined" ? require("./thailand-boundary.js") : null);
  const polygons = geometry.coordinates.map((rings) => ({
    rings,
    bounds: ringBounds(rings[0]),
  }));

  function ringBounds(ring) {
    const xs = ring.map((point) => point[0]);
    const ys = ring.map((point) => point[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }

  function overlaps(a, b) {
    return a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
  }

  function inRect(point, rect) {
    return point[0] >= rect[0] && point[0] <= rect[2] && point[1] >= rect[1] && point[1] <= rect[3];
  }

  function inRing(point, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const a = ring[j];
      const b = ring[i];
      if ((a[1] > point[1]) !== (b[1] > point[1]) &&
          point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
    }
    return inside;
  }

  function contains(lon, lat) {
    const point = [lon, lat];
    return polygons.some(({ rings, bounds }) =>
      inRect(point, bounds) && inRing(point, rings[0]) && !rings.slice(1).some((hole) => inRing(point, hole)));
  }

  function crosses(a, b, c, d) {
    const turn = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    const abC = turn(a, b, c), abD = turn(a, b, d);
    const cdA = turn(c, d, a), cdB = turn(c, d, b);
    return ((abC <= 0 && abD >= 0) || (abC >= 0 && abD <= 0)) &&
      ((cdA <= 0 && cdB >= 0) || (cdA >= 0 && cdB <= 0));
  }

  function ringCrossesRect(ring, rect) {
    const [west, south, east, north] = rect;
    const corners = [[west, south], [east, south], [east, north], [west, north]];
    for (let i = 1; i < ring.length; i++) {
      const a = ring[i - 1], b = ring[i];
      if (!overlaps(ringBounds([a, b]), rect)) continue;
      if (inRect(a, rect) || inRect(b, rect)) return true;
      for (let edge = 0; edge < 4; edge++) {
        if (crosses(a, b, corners[edge], corners[(edge + 1) % 4])) return true;
      }
    }
    return false;
  }

  function intersectsRect(rect) {
    if (!polygons.some(({ bounds }) => overlaps(bounds, rect))) return false;
    const [west, south, east, north] = rect;
    const corners = [[west, south], [east, south], [east, north], [west, north]];
    if (corners.some(([lon, lat]) => contains(lon, lat))) return true;
    return polygons.some(({ rings, bounds }) => overlaps(bounds, rect) &&
      (rings[0].some((point) => inRect(point, rect)) || rings.some((ring) => ringCrossesRect(ring, rect))));
  }

  function maskRings() {
    const world = [[85, -180], [85, 180], [-85, 180], [-85, -180], [85, -180]];
    return [world, ...polygons.flatMap(({ rings }) => rings.map((ring) =>
      ring.map(([lon, lat]) => [lat, lon])))];
  }

  const clip = { contains, intersectsRect, maskRings };
  if (typeof module !== "undefined" && module.exports) module.exports = clip;
  else root.ThailandClip = clip;
})(typeof window !== "undefined" ? window : globalThis);
