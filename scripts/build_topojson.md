# Building the Explore choropleth layer

The Explore map (`ui/charts/Choropleth.tsx`) renders **pre-projected SVG paths**
rather than a runtime geo-projection library. This keeps the frontend free of any
new dependency (`d3-geo` / `topojson-client`) and ships a tiny, lazy-loaded asset.

## Regenerate the layer

```bash
python scripts/build_poa_paths.py
```

Reads `docs/chronicle/assets/POA_2016_NSW_reduced.geojson` (gitignored — see
`docs/chronicle/README.md` to restore it) and writes
`frontend/public/geo/poa_nsw.paths.json`:

```json
{ "viewBox": [1000, 412.1], "features": [ { "postcode": "2000", "d": "M..Z" }, … ] }
```

Current output: **616 postcode shapes, ~261 KB raw / ~72 KB gzipped** — well under
the 1 MB budget, and lazy-loaded only when a map first renders.

## How it works

* **Projection** — equirectangular with a `cos(lat)` longitude correction (fine for
  a single state), scaled uniformly to a 1000-wide viewBox so aspect is preserved.
  The projection is baked in once here, so the browser only draws `<path>`s.
* **Simplification** — Douglas–Peucker in projected pixels (`DP_TOLERANCE = 0.4`),
  seeded with the farthest point so closed rings simplify correctly.
* **Runtime** — `Choropleth.tsx` fetches the layer once (cached per layer id),
  builds a `postcode → value` map from its rows, and shades each path with a
  red→amber→green ramp (`--bad`/`--warn`/`--good` tokens). `diverging: true`
  centres the ramp on 0 (used for a profile Δ). Hover shows the postcode + value;
  click calls `onSelect(postcode)` to add a filter.

## Adding another layer

1. Point `build_poa_paths.py` at the new geojson and set the property key that
   carries the shape id (here `POA_CODE16`).
2. Write `frontend/public/geo/<layer>.paths.json`.
3. Reference it from a dataset's manifest geo binding: `{"dimension": "<key>",
   "layer": "<layer>"}` in `services/backend-api/app/explore/manifest.py`. The map
   renders **only** when the active dataset declares a geo binding.

## Alternative: mapshaper

If you prefer a standard topojson pipeline, `npx mapshaper` can simplify + quantize
the geojson to topojson; you would then add `topojson-client` + `d3-geo` to the
frontend and project at runtime. We chose the pre-projected-paths route to avoid
those dependencies — see the component header for the rationale.
