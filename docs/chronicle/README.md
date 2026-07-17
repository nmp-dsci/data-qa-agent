# chronicle — legacy NSW property profiling tool (reference)

A self-contained, working copy of the legacy static profiling tool from
`nmp-dsci.github.io`, vendored here as the reference implementation for the
**Explore** feature (plan: `.lavish/s19_chronicle-explore-plan.html`).

Two pages, three tabs each (Profiling / Trends / Data Extract), all client-side
(D3 v6 + jQuery + Bootstrap), fed by a pre-computed OLAP-style cube serialized
as JS globals (`data_df`, `lookup_df`).

## Run it

```bash
cd docs/chronicle
python3 -m http.server 8932
# open http://localhost:8932/profile_nswrent.html
# open http://localhost:8932/profile_nswsold.html
```

CDN dependencies (jQuery, chroma-js, pako) load remotely — needs internet.

## What's committed vs gitignored (decision D5)

| Path | Status | Why |
|------|--------|-----|
| `profile_nswrent.html` · `profile_nswsold.html` | committed | byte-identical to the originals |
| `profile_nswgov.py` · `profile_rentboard.py` · `property_yield_20241003.py` | committed | cube/dataset builders (reference for the Explore port) |
| `tool_utils/` (65 JS/CSS) · `resources/d3` · `vendor/bootstrap` · `assets/ajax-loader.gif` | committed | the tool's code (~1.5 MB) |
| `datafeed/*.json` (~9.1 MB) | **gitignored** | derived data — regenerate or copy (below) |
| `assets/POA_2016_NSW_reduced.geojson` (8.5 MB) | **gitignored** | large static geo asset — copy (below) |

## Restoring the gitignored data

Copy from the original repos (fastest):

```bash
SRC=/Users/nathanphillips/git/nmp-projects/nmp-dsci.github.io
mkdir -p datafeed assets
cp "$SRC"/datafeed/profile_nswrent.json      datafeed/
cp "$SRC"/datafeed/profile_nswrent_lookup.json datafeed/
cp "$SRC"/datafeed/profile_nswsold.json      datafeed/
cp "$SRC"/datafeed/profile_nswsold_lookup.json datafeed/
cp "$SRC"/assets/POA_2016_NSW_reduced.geojson assets/
```

Or regenerate the datafeed JSONs with `profile_nswgov.py` (sales) /
`profile_rentboard.py` (rent). Both expect the raw CSVs
(`../data/propertyiq_getdata/...` in the original layout — the same source data
as this repo's `data/nswgov_df.csv` / `data/rentboard_df.csv`) plus
`tool_utils/util_postcode.csv`, and write `datafeed/profile_<sourceid>[_lookup].json`.
Note the legacy region filters: rent = Newcastle SA3, sales = "Sydney — North
Sydney and Hornsby" SA4, 2012–2021, RESIDENCE, ≤ $8M.

## Verified working (2026-07-17)

Both pages open in Chrome with all three tabs functional, zero console errors,
60/60 asset requests 200. Python scripts and HTML are byte-identical to the
originals in `nmp-projects/chronicle` and `nmp-dsci.github.io`.
