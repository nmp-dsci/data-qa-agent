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
single state), scaled uniformly to a 1000-wide viewBox. Geometry is simplified
with Douglas-Peucker in projected pixels so the payload stays well under 1 MB
while keeping recognisable postcode shapes.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "chronicle" / "assets" / "POA_2016_NSW_reduced.geojson"
OUT = ROOT / "frontend" / "public" / "geo" / "poa_nsw.paths.json"

VIEW_W = 1000.0
DP_TOLERANCE = 0.4  # pixels in the 1000-wide space


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
    return [p for p, k in zip(points, keep) if k]


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
    view_h = round((max_lat - min_lat) * scale, 1)

    def project(lon: float, lat: float) -> tuple[float, float]:
        x = (lon - min_lon) * kx * scale
        y = (max_lat - lat) * scale  # flip so north is up
        return round(x, 1), round(y, 1)

    out_features: list[dict] = []
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
            d = "M" + " L".join(f"{x} {y}" for x, y in simplified) + "Z"
            segments.append(d)
        if segments:
            out_features.append({"postcode": postcode, "d": "".join(segments)})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"viewBox": [VIEW_W, view_h], "features": out_features}
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    size_kb = OUT.stat().st_size / 1024
    print(f"wrote {len(out_features)} postcode shapes -> {OUT} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
