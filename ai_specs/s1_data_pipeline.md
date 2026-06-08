# S1 — Finance Data Pipeline (ELT) Spec

> **Audience:** the coding agent implementing stage 1 of `data-qa-agent`.
> **Goal:** stand up an idempotent **ELT** pipeline that pulls S&P 500 equity data
> with `yfinance`, lands raw CSVs, loads them into **Postgres** under the
> `raw_yfinance` schema, and exposes a **dbt** transform layer for downstream
> reporting/analytics.
>
> **Milestone (definition of done):** the entire E→L→T runs end to end for a single
> ticker, **AAPL**, and is re-runnable (a second run adds only new data, never
> duplicates or clobbers existing data). The S&P 500 universe is wired in but the
> acceptance gate is the one-ticker run.

Read [`AGENTS.md`](../AGENTS.md) first. This spec refines stage 1 ("Ingest &
transform") of that document.

---

## Execution model — three independently shippable tasks

This spec is built to be executed as **three separate subagent tasks** — EXTRACT (§1),
LOAD (§2), TRANSFORM (§3) — each with **fresh context**. A subagent for one stage needs
to read only: **this Execution model + §0 (shared prerequisites) + its own section**.

**Hard dependency, not parallelism.** The stages form a chain — L reads E's files, T reads
L's tables — so they can't be *verified* in parallel. But each can be **built
independently against the contract below**, then signed off in order (E → L → T). When the
upstream stage isn't available yet, the downstream agent **stubs it with the fixture noted
below** so it can develop and self-test in isolation.

| Stage | Owns | Consumes (contract) | Produces (contract) | Stub upstream with |
|-------|------|---------------------|---------------------|--------------------|
| **§1 EXTRACT** | `src/.../ingest/` | yfinance API (§0) | timestamped raw CSVs §1.3 + `_state.json` | n/a (top of chain) |
| **§2 LOAD** | `src/.../db/` | raw CSVs §1.3 | `raw_yfinance.*` append-only tables §2.2 | hand-write 1 sample CSV per dataset matching §1.3 |
| **§3 TRANSFORM** | `dbt/` | `raw_yfinance.*` §2.2 | `fact_yfinance.*` (PK'd) + marts §3.3 | `INSERT` ~20 fixture rows (incl. an intentional duplicate) into `raw_yfinance.*` |

**Per-task signoff = a live AAPL run.** Each stage is "done" only when its **Definition of
done** block (§1.7 / §2.5 / §3.6) passes — a real `uv run`/`dbt` invocation against the
**AAPL** ticker producing the observable result stated there. §5 is the end-to-end
integration signoff that composes the three once all are individually green.

---

## 0. Source material (read before coding)

Vendored source is checked into `opensrc/` — read the real APIs instead of guessing:

| Package  | Path                 | Use it for |
|----------|----------------------|------------|
| yfinance | `opensrc/yfinance`   | `Ticker`, `download`, `history`, `get_balance_sheet`/`get_income_stmt`/`get_cash_flow` signatures and returned DataFrame shapes |
| postgres | `opensrc/postgres`   | reference only (this is the Postgres C source, not the Python driver) |
| dbt      | `opensrc/dbt-core`   | dbt project structure, `sources`/`models`/`tests`/`snapshots`, profiles |

> **Note on the prompt:** it referenced `opensrc/dtb`; the real path is
> `opensrc/dbt-core`.

### Key yfinance facts confirmed from source

- `yf.Ticker(symbol).history(start=, end=, interval="1d", auto_adjust=False, actions=True)`
  returns a DataFrame indexed by date with columns
  `Open, High, Low, Close, Adj Close, Volume, Dividends, Stock Splits`.
  `end` is **exclusive**; `start` is **inclusive** (`opensrc/yfinance/yfinance/scrapers/history.py`).
- `yf.download(tickers, start=, end=, group_by="ticker", auto_adjust=False, actions=True, threads=True)`
  is the batch path for many tickers (`opensrc/yfinance/yfinance/multi.py`).
- Financials are properties / getters on `Ticker`
  (`opensrc/yfinance/yfinance/base.py`, `ticker.py`):
  - `get_balance_sheet(freq="yearly")` / `.balance_sheet` / `.quarterly_balance_sheet`
  - `get_income_stmt(freq="yearly")` / `.income_stmt` / `.quarterly_income_stmt`
  - `get_cash_flow(freq="yearly")` / `.cashflow` / `.quarterly_cashflow`
  - Each returns a **wide** DataFrame: rows = line items, columns = period-end dates.
- yfinance has **no built-in S&P 500 constituent list** — we must supply it (see §1.1).

### Dependency corrections (do this first)

`pyproject.toml` currently pins `postgres>=4.0` and `dbt>=1.0.0.40.17`. Neither is
right for this work. Replace with:

```bash
uv add "psycopg[binary]>=3.2"   # COPY-based bulk load + upserts
uv add "dbt-postgres>=1.8"      # dbt + the Postgres adapter (pulls dbt-core)
uv add "pandas>=2.2" "pyarrow>=16"   # yfinance returns pandas; pyarrow for parquet option
# keep: yfinance, pydantic, langgraph (langgraph is for later stages)
# remove: postgres, dbt   ->  uv remove postgres dbt
```

---

## 1. EXTRACT

**Principle:** the extractor is **append-only and idempotent**. It pulls only data
that is *new* relative to what is already cached, and never overwrites cached data
unless `--force` is passed. yfinance/Yahoo is the system of record; our raw cache is
a faithful, auditable copy.

### 1.1 Ticker universe

yfinance/Yahoo has **no S&P 500 constituent endpoint** (confirmed against
`opensrc/yfinance` — it only resolves tickers you already supply). So the list is a
**version-controlled seed**, refreshed **out of band** by an explicit script and committed
— never scraped during a pipeline run.

- Seed file: `dbt/seeds/sp500_constituents.csv` (single canonical copy — it *is* the dbt
  seed, and `ingest/universe.py` reads the same file) with columns
  `ticker, company_name, gics_sector, gics_sub_industry, date_added, cik, retrieved_at`.
- Selection flags on the extractor: `--universe sp500` iterates the seed; `--tickers
  AAPL,MSFT` overrides with an explicit list (the AAPL milestone uses `--tickers AAPL`).

**`scripts/refresh_sp500_seed.py`** — the explicit, manually-run refresher (not part of
the pipeline run path):

- **Source:** the Wikipedia "List of S&P 500 companies" table via
  `pandas.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")`. The
  first table is the current constituents; map its columns →
  `Symbol→ticker, Security→company_name, GICS Sector→gics_sector,
  GICS Sub-Industry→gics_sub_industry, Date added→date_added, CIK→cik`.
- **Normalize:** uppercase tickers and convert Yahoo's class-share convention — Wikipedia
  uses `BRK.B`/`BF.B`, yfinance expects `BRK-B`/`BF-B` (replace `.`→`-`). Stamp
  `retrieved_at` (UTC). Sort by ticker for clean diffs.
- **Validate before writing:** expect ~500–505 rows, no null/duplicate tickers, all
  tickers match `^[A-Z]+(-[A-Z])?$`. Abort with a clear error if the scrape returns an
  unexpected shape (Wikipedia layout drift) so a bad list can't silently land.
- **Write + commit:** overwrite `dbt/seeds/sp500_constituents.csv`. This is the **one
  place** the constituent list is allowed to change, and it lands in a reviewable git
  diff. Run cadence is occasional/manual (e.g. quarterly after index rebalances):

  ```bash
  uv run python scripts/refresh_sp500_seed.py        # refresh the committed seed
  uv run python scripts/refresh_sp500_seed.py --dry-run   # print diff vs current, write nothing
  ```

- **Network isolation:** this is the *only* component that hits Wikipedia. The extract
  pipeline and tests never do — tests use a committed fixture seed.
- **Dependency:** `pandas.read_html` needs an HTML parser — `uv add --group scripts lxml`
  (keep it out of the runtime deps; it's only for this out-of-band script).

- **Recommendation (survivorship bias):** the seed above is *current* membership only,
  which biases 10-year history toward today's constituents. As a follow-up, extend the
  refresher to append to a point-in-time membership table
  (`ticker, effective_date, removed_date`) instead of overwriting, so analytics can
  reconstruct the index as it actually was. Out of scope for the S1 AAPL milestone, but
  design the seed/script so this is additive, not a rewrite.

### 1.2 Datasets to extract (per ticker)

| Dataset            | yfinance call                                   | Grain | Cache file |
|--------------------|--------------------------------------------------|-------|------------|
| EOD prices         | `history(interval="1d", auto_adjust=False, actions=True)` | ticker × date | `data/raw/eod_prices/<ticker>.csv` |
| Corporate actions  | derived from the `Dividends`/`Stock Splits` cols of history | ticker × date × action | `data/raw/corporate_actions/<ticker>.csv` |
| Balance sheet      | `get_balance_sheet(freq="yearly")` and `freq="quarterly"` | ticker × period_end × line_item | `data/raw/balance_sheet/<ticker>.csv` |
| Income statement   | `get_income_stmt(freq=...)`                       | same | `data/raw/income_statement/<ticker>.csv` |
| Cash flow          | `get_cash_flow(freq=...)`                         | same | `data/raw/cash_flow/<ticker>.csv` |
| Company profile    | `Ticker.info` (subset of stable keys)            | ticker (snapshot) | `data/raw/company_profile/<ticker>.csv` |

> **EOD prices:** pull **10 years** by default (`start = today - 10y`), `auto_adjust=False`
> so we keep raw OHLC **and** `Adj Close` separately. Adjustments are a *transform*
> concern (dbt), not an extract concern — storing raw preserves auditability and lets
> us re-derive adjusted series if Yahoo restates splits/dividends.

### 1.3 Output schema (raw CSV contracts)

**Raw is an immutable, append-only landing zone — never mutate a file once written.**
Every extraction run writes a **new, timestamped file per ticker × dataset** containing
only the rows that pull fetched (the *delta*). This preserves a faithful, auditable
record of exactly what the source returned on each pull and literally cannot "override
existing data."

**File naming:** `<dataset>/<ticker>_<UTC pull timestamp>.csv`, where the timestamp is
**`YYYYMMDDHHMM` in UTC** (minute precision) so a lexical sort is chronological and the
name is filesystem-safe:

```
data/raw/eod_prices/AAPL_202606041415.csv
data/raw/corporate_actions/AAPL_202606041415.csv
data/raw/balance_sheet/AAPL_202606041415.csv
data/raw/income_statement/AAPL_202606041415.csv
data/raw/cash_flow/AAPL_202606041415.csv
data/raw/company_profile/AAPL_202606041415.csv
```

> Minute precision means two pulls of the same dataset within the same minute would
> collide — fine for the daily-EOD cadence here. If a sub-minute re-pull is ever needed,
> append a short `load_id` prefix or widen to seconds (`YYYYMMDDHHMMSS`).

- All files in a single pipeline run share **one** timestamp (= the run's `load_id` pull
  time), so the slice that came from one run is trivially identifiable.
- If a pull finds **no new rows**, write **no file** — record the no-op in the run
  summary / `_load_audit` instead of littering empty files.
- Write **tidy/long** financials so new line items never require schema changes.

Column contracts (the *content* of each file; `ingested_at` = the run timestamp):

`eod_prices`
```
ticker, date, open, high, low, close, adj_close, volume, currency, source, ingested_at
```

`corporate_actions`
```
ticker, date, action_type, value, source, ingested_at      # action_type in {dividend, split}
```

`balance_sheet | income_statement | cash_flow`
```
ticker, statement, freq, period_end, line_item, value, currency, source, ingested_at
# statement in {balance_sheet, income_statement, cash_flow}; freq in {annual, quarterly}
```

`company_profile`
```
ticker, company_name, sector, industry, currency, exchange, country, ingested_at
```

- `source` = `"yfinance"` (allow swapping later). `ingested_at` = the run's UTC pull
  timestamp (matches the one in the filename).
- Reshape the wide financial DataFrames to long with `df.melt(...)`; drop all-NaN
  values; coerce `value` to numeric.

### 1.4 Incremental logic (the core requirement)

Because raw files are immutable per-pull deltas (§1.3), the extractor never reads or
edits prior files. It computes a **watermark** to decide what to fetch, writes a new
delta file, and advances the watermark. Idempotent dedup happens at LOAD time via the
primary key (§2.3), not here.

**Watermark source:** a small mutable `data/raw/_state.json` manifest (this is
*metadata*, not raw data, so mutating it is allowed), mapping each
`ticker × dataset` → last fetched date and the set of fetched `(freq, period_end)`.
If the manifest is missing/corrupt, **rebuild it by scanning existing raw filenames +
their contents** (the raw files remain the source of truth). Keeping the watermark in a
manifest means EXTRACT does not require the DB to be up.

For each ticker × dataset:

1. **EOD prices / corporate actions (date-grained):**
   - `last = watermark[ticker].last_date` (or `today − 10y` if none).
   - Fetch `history(start=last + 1 day, end=tomorrow)` (end exclusive).
   - If non-empty, write `eod_prices/<ticker>_<ts>.csv` with just those rows; update
     the watermark to the new `max(date)`.
2. **Financials (period-grained):**
   - `known = watermark[ticker].period_ends` per `(statement, freq)`.
   - Fetch annual + quarterly statements; keep only rows whose `(freq, period_end)` is
     **not** in `known` (a newly reported fiscal period). Write the delta file; add the
     new periods to the watermark.
3. **Restatements (recommendation):** Yahoo can revise a previously reported period.
   Default = **do not overwrite** (matches the spec) — and with immutable files there is
   nothing to overwrite. Detect drift by comparing newly pulled period values against
   what's already loaded in Postgres (or the latest prior raw file); on a mismatch, **log
   a warning and still write the new values as a normal timestamped file** — the two
   versions now coexist in the landing zone, and true SCD history is materialized
   downstream via dbt snapshots (§3.4).
4. `--force` (or `--force-dataset eod_prices`) re-pulls the **full 10y window** and
   writes it as a fresh timestamped file (it does **not** delete prior files — raw stays
   immutable). The re-pulled rows are simply **appended** at LOAD alongside the originals;
   any overlap is resolved later by the dedup logic in the dbt `fact_yfinance` layer (§3.3),
   which keeps the most recently ingested version. **Nothing in EXTRACT or LOAD ever mutates or
   deletes data** — "overwrite" is a *transform-time* resolution, not a write-time one.

### 1.5 Robustness

- **Rate limiting / retries:** wrap calls with retry + exponential backoff + jitter;
  honor a configurable `--sleep` between tickers. Yahoo throttles aggressively at
  500-ticker scale.
- **Partial failure isolation:** one ticker failing must not abort the run. Collect
  per-ticker results and emit a run summary (`ok`, `skipped`, `failed` with reason).
- **Timezone:** store `date` as a naive trading date (exchange-local), not a timestamp.
- **Determinism:** stable column order and sort (`ticker, date` / `ticker, period_end,
  line_item`) so CSV diffs are meaningful and loads are reproducible.

### 1.6 Suggested module layout

```
src/data_qa_agent/ingest/
├── universe.py        # resolve_tickers(), load S&P 500 seed
├── extract_prices.py  # EOD prices + corporate actions, incremental
├── extract_financials.py  # balance sheet / income / cash flow -> long
├── extract_profile.py # company profile snapshot
├── landing.py         # write_delta_file(), _state.json watermark read/update/rebuild
├── yf_client.py       # thin retry/backoff wrapper around yfinance
└── run_extract.py     # CLI orchestration + run summary

scripts/
└── refresh_sp500_seed.py   # out-of-band seed refresher (§1.1); NOT in the run path

dbt/seeds/
└── sp500_constituents.csv  # canonical committed seed — dbt seed + read by universe.py
```

### 1.7 Definition of done — live AAPL signoff

EXTRACT is complete when this runs clean (no DB required — top of the chain):

```bash
uv run python -m data_qa_agent.ingest.run_extract --tickers AAPL --years 10
```

Observe (all must hold):
- New files exist: `data/raw/{eod_prices,corporate_actions,balance_sheet,income_statement,cash_flow,company_profile}/AAPL_YYYYMMDDHHMM.csv`.
- `eod_prices` file: ~10 years of daily rows; columns match §1.3 exactly; sorted; no dup `(ticker,date)` within the file.
- Financials files: rows for **both** `annual` and `quarterly`; long format `(…, line_item, value)`.
- `data/raw/_state.json` records AAPL's last date + known period-ends.
- **Re-run the same command** → it writes **no new files** (nothing new to fetch) and logs a no-op in the run summary.
- `uv run pytest -q tests/ingest` passes (mocked yfinance — no live network in tests).

---

## 2. LOAD

**Principle: append-only, immutable bronze.** Load is a **pure COPY append** from the raw
CSVs into `raw_yfinance.*`. It **never updates, deletes, dedups, or upserts** — every row
of every loaded file is retained verbatim, including duplicates and restated values, so
`raw_yfinance` is a perfect, replayable mirror of the landing zone. No transformation
beyond column typing. **All dedup and validation happen in the dbt TRANSFORM stage (§3),
not here.** The only idempotency the loader enforces is at the *file* level (don't append
the same source file twice) — that prevents duplicate ingestion, not duplicate data.

### 2.1 Local Postgres

Add `docker-compose.yml` (per AGENTS.md) exposing Postgres 16 and read
`DATABASE_URL` from `.env`. Provide:
```bash
docker compose up -d
```

### 2.2 Schema & tables (`raw_yfinance`)

Create with a migration script (`src/data_qa_agent/db/ddl/raw_yfinance.sql`, applied
idempotently with `CREATE SCHEMA/TABLE IF NOT EXISTS`). Mirror the CSV contracts.

**No natural-key primary keys** — the raw layer is append-only and must accept duplicate
and restated rows. Each table gets a **surrogate identity PK** plus **load-lineage**
columns (`load_id`, `source_file`, `_loaded_at`) so every row is traceable to the exact
pull and file it came from. A **non-unique index** on the natural key serves the dbt
dedup queries (§3.3).

```sql
CREATE SCHEMA IF NOT EXISTS raw_yfinance;

CREATE TABLE IF NOT EXISTS raw_yfinance.eod_prices (
    _row_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker       text        NOT NULL,
    date         date        NOT NULL,
    open         numeric, high numeric, low numeric, close numeric,
    adj_close    numeric, volume bigint,
    currency     text, source text NOT NULL,
    ingested_at  timestamptz NOT NULL,          -- pull time (from the source row)
    load_id      uuid        NOT NULL,           -- the load run that appended this row
    source_file  text        NOT NULL,           -- e.g. AAPL_202606041415.csv
    _loaded_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_eod_prices_nk ON raw_yfinance.eod_prices (ticker, date);

CREATE TABLE IF NOT EXISTS raw_yfinance.corporate_actions (
    _row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker text NOT NULL, date date NOT NULL,
    action_type text NOT NULL, value numeric,
    source text NOT NULL, ingested_at timestamptz NOT NULL, load_id uuid NOT NULL,
    source_file text NOT NULL, _loaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_corp_actions_nk ON raw_yfinance.corporate_actions (ticker, date, action_type);

-- one table per statement, identical shape (or a single `financial_statements`
-- table discriminated by `statement` — prefer ONE table, simpler for dbt sources)
CREATE TABLE IF NOT EXISTS raw_yfinance.financial_statements (
    _row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker text NOT NULL, statement text NOT NULL, freq text NOT NULL,
    period_end date NOT NULL, line_item text NOT NULL, value numeric,
    currency text, source text NOT NULL, ingested_at timestamptz NOT NULL, load_id uuid NOT NULL,
    source_file text NOT NULL, _loaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fin_stmts_nk
    ON raw_yfinance.financial_statements (ticker, statement, freq, period_end, line_item);

CREATE TABLE IF NOT EXISTS raw_yfinance.company_profile (
    _row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker text NOT NULL, company_name text, sector text, industry text,
    currency text, exchange text, country text, ingested_at timestamptz NOT NULL,
    load_id uuid NOT NULL, source_file text NOT NULL, _loaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_company_profile_nk ON raw_yfinance.company_profile (ticker);

-- audit log: one row per loaded source file (the loader's idempotency ledger)
CREATE TABLE IF NOT EXISTS raw_yfinance._load_audit (
    load_id uuid NOT NULL, dataset text NOT NULL, ticker text,
    source_file text NOT NULL, rows_inserted int,
    started_at timestamptz, finished_at timestamptz, status text, message text,
    UNIQUE (source_file)        -- enforce "load each raw file at most once"
);
```

### 2.3 Load algorithm (file-driven, append-only)

The loader discovers raw files, **skips any whose `source_file` already has a
successful `_load_audit` row** (file-level idempotency — never ingest the same file
twice), and appends the rest oldest-first (filenames sort chronologically by design,
§1.3). Each file loads in its own transaction so one bad file doesn't poison the batch.

Per file:

1. Generate/stamp one `load_id` (uuid) for the run.
2. **Skip** if `source_file` already loaded ok → re-runs are free and never re-append.
3. `COPY raw_yfinance.<table> (<explicit data columns>, load_id, source_file)
   FROM STDIN WITH (FORMAT csv, HEADER true)` via `psycopg.cursor.copy()`, stamping
   `load_id`/`source_file` for the run. `_row_id` and `_loaded_at` default automatically.
4. That's it — **no staging table, no `ON CONFLICT`, no `UPDATE`/`DELETE`.** The rows are
   appended exactly as they are in the file. Duplicate or restated rows from a later file
   land as additional rows; the dbt `fact_yfinance` layer (§3.3) dedups them downstream.
5. Record `source_file`, `rows_inserted` (= rows in the file), status in `_load_audit`.

> Because raw is append-only, **idempotency is a property of the loader's file-skip, not
> of the data**: `raw_yfinance` may legitimately hold multiple rows for the same natural
> key (re-pulls, `--force`, restatements). That is by design — dedup is TRANSFORM's job.

### 2.4 Module layout

```
src/data_qa_agent/db/
├── connection.py   # psycopg connection from DATABASE_URL
├── ddl/raw_yfinance.sql
├── migrate.py      # apply DDL idempotently
└── load.py         # discover_files() + copy_append() + audit (append-only), CLI run_load.py
```

### 2.5 Definition of done — live AAPL signoff

Prereq: AAPL raw files exist (run §1, or stub with one hand-written sample CSV per dataset
matching §1.3). Then:

```bash
docker compose up -d
uv run python -m data_qa_agent.db.migrate          # creates raw_yfinance schema + tables
uv run python -m data_qa_agent.db.run_load --datasets all
```

Observe (all must hold):
- `raw_yfinance.{eod_prices,corporate_actions,financial_statements,company_profile}` contain AAPL rows; counts equal the row counts of the loaded files.
- Every row has `load_id`, `source_file`, `_loaded_at`, `_row_id` populated.
- `_load_audit` has one row per loaded file with `status='ok'`.
- **Re-run `run_load`** → loads **0 new rows** (files skipped via `_load_audit`); `raw_yfinance` counts unchanged; no `UPDATE`/`DELETE` issued.
- **Append a second copy** of a file (or run §1 `--force`) → row counts **increase** (append-only is working; dedup is *not* this stage's job).
- `uv run pytest -q tests/db` passes (fixtures/test Postgres — no live network).

---

## 3. TRANSFORM (dbt)

**Principle:** dbt owns all transformation for **governance, lineage, testing, and
auditability**. Raw stays immutable; dbt builds layered, tested models on top.

### 3.1 Project setup

Three transform layers, each its own Postgres schema:

| Layer | Schema | Materialization | Purpose |
|-------|--------|-----------------|---------|
| sources | `raw_yfinance` | (the append-only bronze tables from §2) | immutable source of record |
| **fact** | **`fact_yfinance`** | **table, PK-enforced** | **deduped, typed, primary-keyed 1:1 cleanup of each raw table — the trusted "silver" layer** |
| marts | `analytics_yfinance` | table | business-facing fused reporting/analytics (built on `fact_yfinance`) |

```
dbt/
├── dbt_project.yml          # maps each model dir to its schema (see below)
├── profiles.yml             # or ~/.dbt/profiles.yml; target = postgres from DATABASE_URL
├── models/
│   ├── staging/             # thin cast/rename VIEWS over sources (no dedup yet)
│   │   ├── _yfinance__sources.yml   # declares raw_yfinance.* as sources + freshness
│   │   ├── stg_eod_prices.sql
│   │   ├── stg_corporate_actions.sql
│   │   ├── stg_financial_statements.sql
│   │   └── stg_company_profile.sql
│   ├── fact_yfinance/       # deduped + PK-enforced TABLES, 1:1 with raw  ── schema: fact_yfinance
│   │   ├── _fact_yfinance.yml        # contracts + PK constraints + tests
│   │   ├── eod_prices.sql
│   │   ├── corporate_actions.sql
│   │   ├── financial_statements.sql
│   │   └── company_profile.sql
│   ├── intermediate/        # fused/reshaped building blocks on fact_yfinance
│   │   ├── int_prices_adjusted.sql        # apply split/div adjustment
│   │   └── int_financials_pivoted.sql     # long -> wide per statement/period
│   └── marts/               # ── schema: analytics_yfinance
│       ├── dim_company.sql
│       ├── fct_daily_prices.sql           # OHLCV + returns
│       └── fct_valuation_metrics.sql      # ratios fusing prices × fundamentals
├── seeds/sp500_constituents.csv
└── snapshots/financial_statements_snapshot.sql
```

`dbt_project.yml` routes each layer to its schema and materialization:

```yaml
models:
  data_qa_agent:
    staging:      { +schema: fact_yfinance, +materialized: view }
    fact_yfinance:
      +schema: fact_yfinance
      +materialized: table
      +contract: { enforced: true }   # lets dbt create the PK constraint
    intermediate: { +schema: fact_yfinance, +materialized: view }
    marts:        { +schema: analytics_yfinance, +materialized: table }
```

### 3.2 Sources & freshness

Declare `raw_yfinance` tables as dbt **sources** with `freshness` (warn after ~30h on
`ingested_at`) and `loaded_at_field: ingested_at`. This makes "is the data stale?" a
first-class, testable question.

### 3.3 Layering rules — **`fact_yfinance` is the deduped, primary-keyed layer**

This stage turns the append-only raw layer into clean, deduplicated, primary-keyed,
trusted tables. EXTRACT/LOAD never dedup; the transform does.

- **staging (`stg_`, views):** 1:1 thin pass over each source — rename/cast columns,
  light cleaning, **no dedup, no joins.** Keeps SQL readable and isolates source-shape
  changes.

- **`fact_yfinance` (tables, PK-enforced):** the canonical, deduped, primary-keyed copy
  of each raw table — what every downstream model and the stage-2 agent should read.
  Because raw holds duplicate/restated rows per natural key (§2.3), each fact model keeps
  exactly one row per natural key: the **most recently ingested** version. Postgres has no
  `QUALIFY`, so use a windowed subselect:

  ```sql
  -- models/fact_yfinance/eod_prices.sql   -> fact_yfinance.eod_prices
  with ranked as (
      select *,
             row_number() over (
                 partition by ticker, date
                 order by ingested_at desc, _row_id desc   -- latest pull wins
             ) as rn
      from {{ ref('stg_eod_prices') }}
  )
  select ticker, date, open, high, low, close, adj_close, volume, currency, source,
         ingested_at, load_id, source_file
  from ranked
  where rn = 1
  ```

  Apply the same `row_number()` dedup keyed on each table's natural key — the PK each fact
  table enforces:
  - `fact_yfinance.eod_prices` → PK `(ticker, date)`
  - `fact_yfinance.corporate_actions` → PK `(ticker, date, action_type)`
  - `fact_yfinance.financial_statements` → PK `(ticker, statement, freq, period_end, line_item)`
  - `fact_yfinance.company_profile` → PK `(ticker)`

  Declare the PK (and `not_null`s) via dbt **`constraints`** in `_fact_yfinance.yml` with
  `contract: {enforced: true}` (set in §3.1), so dbt issues the actual `PRIMARY KEY` DDL
  on the materialized table. The `row_number` filter guarantees the PK can never be
  violated; if it ever is, the build fails loudly — exactly the governance signal we want.

- **intermediate (`int_`, views):** reusable fused/reshaped building blocks, built on the
  deduped `fact_yfinance` tables. `int_prices_adjusted` derives adjusted OHLC from raw
  OHLC + corporate actions. `int_financials_pivoted` pivots the long statements into
  per-statement wide tables.
- **marts (`analytics_yfinance`):** business-facing, fused tables (the "reporting /
  analytics layer" the prompt asks for). `fct_daily_prices` adds daily/period returns;
  `dim_company` is the conformed company dimension; `fct_valuation_metrics` fuses price ×
  fundamentals (P/E, P/B, market cap, etc.). Tables; incremental where large.

### 3.4 Snapshots (restatement history)

The append-only raw layer already *retains* every restated version (each is a separate
row with its own `ingested_at`). The dbt **snapshot** (`check` strategy on `value`, keyed
by `(ticker, statement, freq, period_end, line_item)`) turns that raw history into a clean
**SCD2** table with `dbt_valid_from`/`dbt_valid_to`, enabling point-in-time ("as reported
on date X") analytics. `fact_yfinance.financial_statements` (§3.3) gives you the *latest*
value; the snapshot gives you *any historical* value — both derive from the same immutable
raw rows.

### 3.5 Tests — **the validation gate**

Validation lives here, not in EXTRACT/LOAD. Ship dbt tests with the models; `dbt test`
must pass for the AAPL milestone.

- **Dedup proof:** `unique` (combination) on each `fact_yfinance` model's natural key —
  this is the assertion that the §3.3 dedup produced one row per key. It's also enforced
  structurally by the PK `constraint`, so a dedup regression fails the **build**, not just
  the test. (The raw source is deliberately *not* unique — don't test uniqueness there.)
- `not_null` on every PK column of the `fact_yfinance` tables.
- `relationships`: `fact_yfinance.*.ticker` and `marts.fct_*.ticker` → `dim_company.ticker`.
- `accepted_values`: `statement`, `freq`, `action_type`.
- Custom/`dbt_utils` tests: non-negative `volume`/`close`, `high >= low`, no gaps in
  trading-day sequence (warn), `period_end` not in the future.
- **Source freshness** (§3.2) as a staleness test.

### 3.6 Definition of done — live AAPL signoff

Prereq: `raw_yfinance.*` hold AAPL rows (run §1+§2, or stub by `INSERT`-ing ~20 fixture
rows including **one intentional duplicate** of a natural key). Then:

```bash
cd dbt && uv run dbt build      # runs models + tests + snapshot + seed
```

Observe (all must hold):
- `fact_yfinance.{eod_prices,corporate_actions,financial_statements,company_profile}` exist as **tables with PRIMARY KEY constraints** on their natural keys (verify in `psql` / information_schema).
- Each `fact_yfinance` table holds **one row per natural key** for AAPL (the seeded duplicate collapses to one); the surviving row is the latest `ingested_at`.
- **All `dbt test`s pass**, including the `unique` test on each `fact_yfinance` grain.
- Marts build: `dim_company`, `fct_daily_prices` (returns present), `fct_valuation_metrics` populated for AAPL.
- Re-run `dbt build` → idempotent, still green.
---

## 4. Orchestration & CLI

Single entry point (`uv run`) that chains the stages:

```bash
# full single-ticker run (the milestone)
uv run python -m data_qa_agent.pipeline run --tickers AAPL

# stage-by-stage
uv run python -m data_qa_agent.ingest.run_extract --tickers AAPL --years 10
uv run python -m data_qa_agent.db.run_load --datasets all
cd dbt && uv run dbt build           # run + test + snapshot + seed

# scale-out later
uv run python -m data_qa_agent.pipeline run --universe sp500 --sleep 0.5
```

Flags: `--tickers`, `--universe`, `--years` (default 10), `--datasets`,
`--force` / `--force-dataset`, `--sleep`, `--dry-run`.

> **Future orchestration (recommendation):** wrap this in a scheduler (cron for a
> simple daily EOD pull, or Dagster/Airflow if dependencies grow). dbt freshness +
> `_load_audit` give the signals a scheduler needs. Out of scope for S1.

---

## 5. Acceptance criteria (S1 done) — end-to-end integration signoff

The per-stage AAPL signoffs (**§1.7, §2.5, §3.6**) are the gate for each subagent's task.
This section is the **integration** gate: it must hold when the three real stages are
chained for AAPL with **no stubs/fixtures** in the path.

1. `uv sync` installs the corrected deps; `docker compose up -d` brings up Postgres.
2. The chained run completes with no errors:
   ```bash
   uv run python -m data_qa_agent.pipeline run --tickers AAPL
   ```
   (equivalently §1.7 → §2.5 → §3.6 in order, each green against the previous stage's real
   output).
3. **All three per-stage signoffs pass on real upstream output** — §1.7 produced the raw
   files §2.5 loaded, which §3.6 transformed; no hand-written CSV or seeded row was used.
4. **End-to-end immutability/idempotency:** running the whole pipeline **twice** writes no
   new raw files, loads 0 new rows (`_load_audit` skip), leaves `raw_yfinance` counts
   unchanged, and `dbt build` stays green — no row anywhere is mutated or deleted.
5. **Cross-stage dedup check:** a `--force` re-pull appends duplicate rows into
   `raw_yfinance` (counts go **up**), yet `fact_yfinance.*` and mart counts are
   **unchanged** and the PK build still succeeds — proving append-only LOAD + transform
   dedup compose correctly.
6. Repo hygiene: `uv run ruff check .` and `uv run mypy src` are clean; `uv run pytest -q`
   passes (all stages' unit tests, mocked yfinance + test Postgres — no live network).

---

## 6. Improvement backlog (recommendations beyond S1)

These were surfaced while writing this spec; implement opportunistically or file as
follow-ups:

1. **Survivorship bias** — point-in-time S&P 500 membership table (§1.1) so historical
   index analytics aren't biased by today's constituents.
2. **Restatement handling** — dbt snapshot (§3.4) + restatement quarantine (§1.4.3)
   for true as-reported point-in-time fundamentals.
3. **Raw, not adjusted, prices at extract** — keep adjustment in dbt so split/dividend
   revisions are re-derivable; never lose the original OHLC.
4. **Data-quality observability** — beyond dbt tests, log row counts, null rates, and
   trading-day gaps to `_load_audit`; alert on freshness breaches.
5. **Rate limiting & batching** — use `yf.download` batches + backoff for the full 500
   universe; checkpoint progress so a crashed run resumes.
6. **Currency normalization** — multi-currency fundamentals; store reporting currency,
   convert in a mart, don't mutate raw.
7. **Secrets/config** — pydantic `Settings` loader (`config.py`) for `DATABASE_URL`
   etc.; never hardcode (per AGENTS.md).
8. **Storage format** — optional parquet mirror of raw for cheaper re-loads and
   schema evolution; CSV remains the human-auditable contract.
9. **Schedule & incremental marts** — daily EOD cron; make large marts dbt
   `incremental` keyed on `date`.
10. **Lineage/catalog** — publish `dbt docs` so stakeholders (and the LangGraph agent
    in stage 2) can discover tables and column meanings.
```
