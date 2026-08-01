"""Offline: NSW POA geojson -> pre-projected SVG paths for the Explore choropleth.

Rather than ship an 8.5 MB geojson and a runtime geo-projection library, we bake
the projection here (once) and emit tiny SVG `path` strings the frontend renders
directly. The result is a single small JSON — no d3-geo / topojson-client at
runtime, no new frontend dependency, and it lazy-loads only when a map renders.

    python scripts/build_poa_paths.py

Reads docs/chronicle/assets/POA_2016_NSW_reduced.geojson (gitignored — see
docs/chronicle/README.md to restore) and writes
frontend/public/geo/poa_nsw.paths.json = {viewBox:[w,h], features:[{postcode,d}]}.

Projection: equirectangular with a cos(lat) longitude correction (fine for a
single state), scaled uniformly to a VIEW_W-wide viewBox.

Two things decide how the map looks at the choropleth's deepest zoom, and both
are set here:

* the **coordinate grid** (VIEW_W). Coordinates are emitted as integers, so
  VIEW_W *is* the precision — a 1000-wide grid quantises every vertex to ~2.7
  screen pixels at 40x zoom, which reads as a staircase no matter how many
  vertices survive simplification.
* the **simplification tolerance** (DP_TOLERANCE), how far an edge may deviate
  from the source outline.

Both are now well under a screen pixel at full zoom, so the limit on detail is
the source geometry rather than this encoder. Paths use relative ``l`` deltas
instead of absolute ``L`` points, which is what keeps that fidelity affordable:
neighbouring vertices are a few grid units apart, so the deltas are 1-3 digits
where absolute coordinates would be 5-6 (~2.5x smaller gzipped).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "chronicle" / "assets" / "POA_2016_NSW_reduced.geojson"
OUT = ROOT / "frontend" / "public" / "geo" / "poa_nsw.paths.json"

# Integer coordinate grid. This is the knob that mattered: a coarse grid does not
# just blur a vertex, it collapses neighbouring source vertices onto the same
# grid line, and the outline then reads as a rectilinear staircase however many
# vertices survive. Measured share of axis-aligned (staircase) segments against
# this source, and the gzipped payload:
#
#      50000  21.7%  317 KB      200000   5.2%  466 KB
#     100000  11.2%  391 KB      400000   2.4%  536 KB
#
# 200000 is where the staircase stops being the thing you notice; past it you
# are paying real bytes to chase the source outline's own vertex spacing
# (~190 m median), which no grid can undo. The frontend reads the viewBox out of
# the payload, so this can be re-tuned here alone — stroke widths scale off it.
VIEW_W = 200_000.0
# Deviation allowed from the source outline, in grid units — ~0.3 screen px at
# the choropleth's zoom cap. At this tolerance nearly every source vertex
# survives, so the outlines are as detailed as the reduced ABS geometry can be.
DP_TOLERANCE = VIEW_W / 100_000


def _load_geojson(path: Path) -> dict:
    raw = path.read_text()
    return json.loads(raw[raw.index("{") :])


def _iter_rings(geometry: dict):
    t = geometry["type"]
    if t == "Polygon":
        yield from geometry["coordinates"]
    elif t == "MultiPolygon":
        for poly in geometry["coordinates"]:
            yield from poly


def _bounds(features: list[dict]) -> tuple[float, float, float, float]:
    min_lon = min_lat = math.inf
    max_lon = max_lat = -math.inf
    for f in features:
        for ring in _iter_rings(f["geometry"]):
            for lon, lat in ring:
                min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
                min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
    return min_lon, min_lat, max_lon, max_lat


def _dp(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker simplification of a projected ring."""
    n = len(points)
    if n < 4:
        return points
    # iterative DP to avoid recursion limits on big rings
    keep = [False] * n
    keep[0] = keep[-1] = True
    # Closed rings start and end on the same point, so the (0, n-1) baseline is
    # degenerate — seed a second anchor at the point farthest from the start so
    # DP has a real chord to measure against.
    ax, ay = points[0]
    far, fi = -1.0, 0
    for i in range(1, n - 1):
        dist = math.hypot(points[i][0] - ax, points[i][1] - ay)
        if dist > far:
            far, fi = dist, i
    if fi:
        keep[fi] = True
        stack = [(0, fi), (fi, n - 1)]
    else:
        stack = [(0, n - 1)]
    while stack:
        lo, hi = stack.pop()
        ax, ay = points[lo]
        bx, by = points[hi]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1e-9
        dmax, idx = 0.0, -1
        for i in range(lo + 1, hi):
            px, py = points[i]
            dist = abs((px - ax) * dy - (py - ay) * dx) / norm
            if dist > dmax:
                dmax, idx = dist, i
        if dmax > tol and idx != -1:
            keep[idx] = True
            stack.append((lo, idx))
            stack.append((idx, hi))
    return [p for p, k in zip(points, keep, strict=True) if k]


def main() -> int:
    if not SRC.exists():
        print(f"source geojson missing: {SRC} (see docs/chronicle/README.md)")
        return 1
    g = _load_geojson(SRC)
    features = g["features"]
    min_lon, min_lat, max_lon, max_lat = _bounds(features)
    mid_lat = (min_lat + max_lat) / 2
    kx = math.cos(math.radians(mid_lat))
    x_span = (max_lon - min_lon) * kx
    scale = VIEW_W / x_span
    view_h = round((max_lat - min_lat) * scale)

    def project(lon: float, lat: float) -> tuple[int, int]:
        x = (lon - min_lon) * kx * scale
        y = (max_lat - lat) * scale  # flip so north is up
        return round(x), round(y)

    def to_path(points: list[tuple[int, int]]) -> str:
        """One absolute moveto, then relative linetos — see the module docstring
        for why the deltas matter."""
        parts = [f"M{points[0][0]} {points[0][1]}"]
        px, py = points[0]
        for x, y in points[1:]:
            parts.append(f"l{x - px} {y - py}")
            px, py = x, y
        return "".join(parts) + "Z"

    out_features: list[dict] = []
    vertices = 0
    for f in features:
        postcode = str(f["properties"].get("POA_CODE16", "")).strip()
        if not postcode:
            continue
        segments: list[str] = []
        for ring in _iter_rings(f["geometry"]):
            projected = [project(lon, lat) for lon, lat in ring]
            simplified = _dp(projected, DP_TOLERANCE)
            if len(simplified) < 4:
                continue
            vertices += len(simplified)
            segments.append(to_path(simplified))
        if segments:
            out_features.append({"postcode": postcode, "d": "".join(segments)})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"viewBox": [VIEW_W, view_h], "features": out_features}
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    size_kb = OUT.stat().st_size / 1024
    print(
        f"wrote {len(out_features)} postcode shapes, {vertices} vertices "
        f"-> {OUT} ({size_kb:.0f} KB, grid {VIEW_W:.0f}x{view_h})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
