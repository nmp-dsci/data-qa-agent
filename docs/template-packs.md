# Template packs — the contract (s48)

Plan: `.lavish/s48_template-pack-plan.html`. Decisions D1–D6 locked 2026-09-10.
This file is the shared contract every workstream codes against. Change it here first.

## 1. What a pack is

A pack is two Google Workspace files plus a synced JSON snapshot in the repo:

| Piece | Lives in | Who edits | Purpose |
|---|---|---|---|
| `Pack.slides` | Drive folder `Data Pilot/packs/<name>-v<n>/` | curator, in Slides | **library slides**: one slide per layout, built from ordinary shapes; the builder duplicates a library slide per `add_slide` |
| `Pack.sheet` | same folder | curator, in Sheets | `_pack` tab (the catalogue) + one `tpl_*` tab per table template, each holding sample rows and a **hand-styled chart** whose spec the builder clones |
| `packs/<name>/pack.json` | repo | `scripts/pack_sync.py` (never by hand) | reviewable snapshot the runtime reads; its sha256 folds into the `av-*` fingerprint via `layouts.md` |

**Why library slides, not master layouts.** The Slides API cannot create master layouts, and the Slides
editor cannot author CHART/TABLE placeholders. A library slide is authorable by API (the scaffold) and by a
curator (any shape, any styling, no theme editor), and `duplicateObject` + an `objectIds` map lets the builder
copy it and fill it in one atomic batch. Master layouts remain supported (`source: layout`) for hand-built packs.

## 2. Slots — how a shape becomes a slot

A slot is any page element on a library slide whose **alt-text title** (`pageElement.title`, set in Slides via
Format options → Alt text) is one of:

`headline` · `commentary` · `kpi` · `kpi_label` · `chart` · `table` · `subtitle` · `footer` · `source`

- `headline`, `commentary`, `kpi`, `kpi_label`, `subtitle`, `footer`, `source`: text is inserted into the shape
  (`deleteText` all then `insertText`); the shape's own styling is preserved.
- `chart`: the shape is deleted and a `createSheetsChart` (LINKED) is placed at its exact size/transform.
- `table`: the shape is deleted and a `createTable` is placed at its geometry.
- A slot missing from a library slide is simply not filled; content the agent passed for it is reported in
  `SlideRecord.dropped` exactly as today.
- `footer` and `source` are filled by the builder, not the agent: `footer` = "Data Pilot · <run short id> · <date>",
  `source` = "Source: <mart(s)>; SQL in the Sheet's Manifest tab".

## 3. `_pack` tab (source of truth) → `pack.json`

`_pack` tab columns, header row exact, one row per layout:

| column | type | meaning |
|---|---|---|
| `id` | `L1`… | stable id; used in the manifest and metrics |
| `name` | text | the exact string the agent passes as `layout` |
| `enabled` | TRUE/FALSE | only TRUE rows reach `layouts.md` |
| `use_when` | text | the sentence the agent reads to choose; highest-leverage text in the feature |
| `source` | `slide` \| `layout` | library slide (default) or master layout |
| `ref` | text | for `slide`: the library slide's alt-text title (the scaffold sets it to the id, e.g. `L2`); for `layout`: `layoutProperties.displayName` |
| `table_template` | tab name or blank | which `tpl_*` tab's shape and chart to clone |
| `chart_template` | text | `<tab>!<chart title>` — the chart on that tab to clone (blank = no chart) |
| `series_max` | int | max numeric series kept from `columns` (default 3) |
| `grader_shape` | `scalar`/`series`/`ranked_set`/`row_set`/`` | informational, for the Inspector |
| `notes` | text | curator notes; ignored by the runtime |

`pack.json` (written by `pack_sync`, read by the runtime):

```json
{
  "name": "nsw-property", "version": 1, "synced_at": "2026-09-10T02:00:00Z",
  "slides_id": "...", "sheet_id": "...", "folder_id": "...",
  "page": {"width_in": 10, "height_in": 5.625},
  "layouts": [
    {
      "id": "L2", "name": "Headline + Trend", "enabled": true,
      "use_when": "One measure over time, 1-3 series.",
      "source": "slide", "ref": "L2", "slide_object_id": "p_l2",
      "slots": {
        "headline":   {"object_id": "g1a2", "rect": [0.5, 0.35, 9.0, 0.8]},
        "commentary": {"object_id": "g1a3", "rect": [0.5, 4.6, 9.0, 0.7]},
        "chart":      {"object_id": "g1a4", "rect": [0.5, 1.3, 9.0, 3.2]}
      },
      "table_template": "tpl_series",
      "chart_template": {"tab": "tpl_series", "title": "trend", "chart_id": 123456, "sheet_id": 987},
      "series_max": 3, "grader_shape": "series"
    }
  ],
  "table_templates": {
    "tpl_series": {"sheet_id": 987, "header": ["period", "series_1", "series_2", "series_3"], "x_role": "first"}
  },
  "issues": []
}
```

`rect` = `[x, y, w, h]` in inches. `issues` lists validation problems (unknown `ref`, enabled layout with no
`headline` slot, `chart_template` not found); an enabled layout with an issue is treated as disabled at runtime.

## 4. Seed catalogue (D5) — what the scaffold builds

| id | name | slots on the library slide | table_template | chart_template | grader_shape |
|---|---|---|---|---|---|
| L1 | Cover | headline, subtitle, footer | — | — | — |
| L2 | Headline + Trend | headline, chart, commentary, source, footer | tpl_series | tpl_series!trend (LINE) | series |
| L3 | KPI + Trend | headline, kpi, kpi_label, chart, commentary, source, footer | tpl_series | tpl_series!trend | scalar |
| L4 | Ranked Bars | headline, chart, commentary, source, footer | tpl_ranked | tpl_ranked!ranked (BAR, horizontal) | ranked_set |
| L6 | Table | headline, table, commentary, source, footer | tpl_table | — | row_set |
| L9 | Sources & SQL | headline, table | tpl_sources | — | — (auto-appended by the builder) |

Styling defaults for the scaffold: Flight Deck light palette (background `#f6f4ee`, text `#23293a`, accent
`#9a7328`, muted `#67604e`, series colours `#2456c9 #9a7328 #35803f #c03535`), Inter for text with Arial fallback,
headline 24pt semibold, commentary 12pt, footer 9pt muted. Chart specs: no chart title (the headline is the
title), legend bottom, gridlines muted, axis number format from `units.py`.

## 5. The run Sheet (D1)

One spreadsheet per run, created fresh (not copied), tabs in this order:

- `Data` — for each slide, a title row `#<n> · <layout name>` (bold, hyperlinked to the slide), then a Sheets
  **Table** named `s<nn>_<frame>` via `addTable` with typed columns (date/number/currency/percent/text from
  `units.py`), then two blank rows. The native chart is anchored two columns to the right of its table.
  If `addTable` is rejected by the API, fall back to a bold header row + a named range of the same name and set
  `manifest.table_kind = "range"`.
- `Manifest` — columns: `slide, layout_id, layout, table, table_range, chart_id, headline, frame, query_ref, mart, rows, slide_url`.
- `README` — question, dataset, run id, pack name + version, deck URL, "How to extend" (copy the file, edit the
  Table, charts follow).
- Overflow: a frame wider than 26 columns or longer than 2000 rows gets its own tab `s<nn>_<frame>` and the
  Data block holds a one-line pointer; Manifest `table_range` points at the tab.

Both generated files carry Drive `appProperties`: `{"dp_run_id", "dp_pack", "dp_pack_version", "dp_kind": "deck"|"sheet"}`.

## 6. Manifest (`SlideRecord`) — new fields

Existing flat fields stay. Add: `layout_id` (L2), `slide_object_id`, `table_name`, `table_range` (A1 with tab),
`table_kind` (`table`|`range`|`tab`), `chart_id` (int|null), `chart_object_id` (Slides element id|null),
`query_ref`, `mart`, `notes_object_id`. The artifact dict gains `pack: {name, version}` and `folder_id`.
These are what the change-log differ keys on; keep them stable.

## 7. Change log (D3)

Migration `0038_artifact_handover.py` (services/db-migrate/migrations/versions):

```sql
create table app.artifact_snapshots (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references app.query_runs(id) on delete cascade,
  kind text not null check (kind in ('deck','sheet')),
  file_id text not null,
  drive_version bigint not null,
  modified_time timestamptz not null,
  snapshot jsonb not null,              -- normalised: slides[] {slide_object_id, index, headline, commentary, notes, charts[] {object_id,chart_id,spreadsheet_id,linking_mode}, tables[] {object_id,rows,cols,cells}, texts} / {tables[] {name,header,rows}, charts: {chart_id: {chartType}}} (sheet kind only)
  taken_at timestamptz not null default now(),
  unique (run_id, kind, drive_version)
);
create table app.artifact_edits (
  id bigserial primary key,
  run_id uuid not null references app.query_runs(id) on delete cascade,
  kind text not null,                   -- deck | sheet
  event text not null,                  -- headline_changed | commentary_changed | notes_changed | chart_type_changed | chart_deleted | chart_added | slide_added | slide_deleted | slide_reordered | table_values_changed | table_rows_added | table_rows_deleted | file_renamed
  layout_id text, slide_index int, slide_object_id text, table_name text,
  before jsonb, after jsonb,
  actor text,                           -- from Drive Activity API when available, else null
  drive_version bigint not null,
  observed_at timestamptz not null default now()
);
create index on app.artifact_edits (run_id, observed_at);
alter table app.query_runs add column artifact_last_checked timestamptz, add column artifact_opened_at timestamptz, add column artifact_edit_count int not null default 0;
```

Poller: `scripts/handover_poll.py` (runs in the data-agent env; `make handover-poll`, and a `handover-poller`
compose service with `profiles: [handover]`, 15-minute loop). For each run with `artifact_deck_url` and
`artifact_last_checked` older than 15 min within the last 30 days: `files.get(fields=version,modifiedTime,name)`;
if `version` > last snapshot's, take a snapshot (`presentations.get`, `values.get` on Data + Manifest), diff
against the previous snapshot (or the stored manifest when none), insert edits, bump `artifact_edit_count`.
Never write to the user's files. The first snapshot after creation is taken by the builder itself (version 1
baseline) and persisted by `routers/ask.py` as the `drive_version = 1` row the moment the answer that built the
deck is saved — the manifest-derived baseline (`_baseline_from_manifest` in the poller) is kept only as a
fallback for runs that predate that write and never got a real baseline row.

`normalise_deck` lives in `agent/handover_snapshot.py` and is re-exported from `agent/handover.py` — the
builder's baseline and the poller's later snapshot go through the exact same function, so the two are always
comparable. `chart_type_changed` needs the sheet's own chart types (a chart's type is a Sheets property, not a
deck one): `agent/handover_snapshot.snapshot_chart_types` reads `spreadsheets.get(fields=sheets.charts(chartId,spec))`
once per poll and the result is stashed as `snapshot.charts` on the **sheet** kind's row, so the next poll's
"before" chart types come from the last stored sheet snapshot rather than a second live fetch.

Actor attribution (`app.artifact_edits.actor`) is a best-effort call to the Drive Activity API v2
(`activity:query`, filtered to `time > <last snapshot's taken_at>`) in `scripts/handover_poll.py::_actor_since`.
**As deployed, this reliably comes back `None`**: the generating account's OAuth credential
(`scripts/google_auth.py` `SCOPES`) only carries `drive.file`/`spreadsheets`/`presentations`, and Google
authorizes a request on the token's actual granted scopes — `drive.file` does not itself imply
`drive.activity.readonly`. A human wanting real attribution needs to re-run the OAuth consent flow with that
scope added and re-mint `GOOGLE_DECK_REFRESH_TOKEN`; until then every edit's `actor` is `null`, which is the
correct default rather than an error.

Metrics endpoint (backend-api): `GET /analytics/handover?days=30` → `{decks, opened, edited, edit_rate,
median_minutes_to_first_edit, edits_by_event[], edits_by_layout[] {layout_id, decks, edits, headline_edits,
chart_edits}, layout_usage[] {layout_id, slides}, recent[] {run_id, question, deck_url, edits, last_edit_at}}`.

## 8. Runtime config

`GOOGLE_SLIDES_TEMPLATE_ID` (Pack.slides) and new `GOOGLE_SHEET_TEMPLATE_ID` (Pack.sheet), `PACK_NAME`
(default `nsw-property`), `PACK_DIR` (default `packs`, relative to repo root; the container gets it mounted or
baked). The runtime reads `packs/<PACK_NAME>/pack.json`; if absent or the ids are blank it uses
`DEFAULT_CATALOGUE` exactly as before. `layouts.md` renders from pack.json's enabled, issue-free layouts.

## 9. Scripts

- `scripts/pack_scaffold.py --name nsw-property [--version 1]` — creates the folder and both files, builds the
  seed catalogue (§4), writes `GOOGLE_SLIDES_TEMPLATE_ID` / `GOOGLE_SHEET_TEMPLATE_ID` into `.env`, then runs
  sync. Idempotent: if a folder with that name and appProperties exists, reuse it. Prints both URLs.
- `scripts/pack_sync.py [--name]` — reads `_pack` + both files, validates, writes `pack.json`, prints issues.
  `--check` exits non-zero if pack.json is stale (CI-friendly).
- `scripts/handover_poll.py [--once]` — §7.

## 10. Non-goals for this build

No Workspace Events / Pub/Sub, no Apps Script, no Shared Drive (gmail.com account), no thumbnails outside the
Inspector's cached fetch, no layout-identity grading.
