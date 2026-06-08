# data-qa-agent

A data agent that answers stakeholder questions about business data. **Stage 1** is an
idempotent **ELT pipeline** that pulls S&P 500 equity data with `yfinance`, lands raw CSVs,
loads them append-only into **Postgres** (`raw_yfinance`), and transforms them with **dbt**
into deduped `fact_yfinance` tables and `analytics_yfinance` marts.

> See [`AGENTS.md`](AGENTS.md) for architecture and [`ai_specs/s1_data_pipeline.md`](ai_specs/s1_data_pipeline.md)
> for the full stage-1 spec.

---

## Prerequisites

```bash
uv sync --all-groups          # install deps (Python 3.12, uv)
cp .env.example .env          # DATABASE_URL etc. — defaults match docker-compose
docker compose up -d          # start local Postgres 16 (db=data_qa on :5432)
```

`DATABASE_URL` default: `postgresql://postgres:postgres@localhost:5432/data_qa`

---

## Run the pipeline

### All at once (the milestone)

```bash
uv run python -m data_qa_agent.pipeline run --tickers AAPL
```

This chains EXTRACT → LOAD → TRANSFORM. Re-running is a no-op (writes no new raw files,
loads 0 new rows, dbt stays green). Useful flags: `--universe sp500`, `--years 10`,
`--datasets eod_prices,...`, `--force` / `--force-dataset`, `--sleep 0.5`, `--dry-run`.

### Stage by stage

```bash
# 1. EXTRACT — pull from yfinance into data/raw/<dataset>/<ticker>_<UTC>.csv (append-only)
uv run python -m data_qa_agent.ingest.run_extract --tickers AAPL --years 10

# 2. LOAD — create schema/tables, then COPY-append the raw CSVs into raw_yfinance.*
uv run python -m data_qa_agent.db.migrate
uv run python -m data_qa_agent.db.run_load --datasets all

# 3. TRANSFORM — build + test + snapshot + seed
cd dbt && uv run dbt build --profiles-dir .
```

> **Seed refresh (out of band, manual):** the S&P 500 constituent list is a committed
> seed, refreshed only by this script — never during a pipeline run:
> ```bash
> uv run python scripts/refresh_sp500_seed.py            # rewrite dbt/seeds/sp500_constituents.csv
> uv run python scripts/refresh_sp500_seed.py --dry-run  # show the diff, write nothing
> ```

---

## See the data

### A) Where it lives

| Layer | Schema | What |
|-------|--------|------|
| Raw landing (files) | `data/raw/<dataset>/` | immutable timestamped CSVs (gitignored) |
| Bronze (append-only) | `raw_yfinance` | verbatim mirror of the CSVs, incl. duplicates |
| Silver (deduped, PK'd) | `fact_yfinance` | one row per natural key — **trust this** |
| Gold (marts) | `analytics_yfinance` | `dim_company`, `fct_daily_prices`, `fct_valuation_metrics` |

Raw files on disk:

```bash
ls data/raw/eod_prices/        # AAPL_<YYYYMMDDHHMM>.csv
head data/raw/eod_prices/AAPL_*.csv
```

### B) Connect to Postgres directly

```bash
# psql inside the container (no local psql install needed)
docker compose exec postgres psql -U postgres -d data_qa

# …or from your host if you have psql / pgcli
psql "$DATABASE_URL"
pgcli "$DATABASE_URL"
```

Useful queries once connected:

```sql
\dn                                         -- list schemas
\dt fact_yfinance.*                         -- tables in the silver layer
select count(*) from raw_yfinance.eod_prices;
select * from fact_yfinance.eod_prices order by date desc limit 10;
select * from analytics_yfinance.fct_valuation_metrics where ticker = 'AAPL';
select * from raw_yfinance._load_audit;     -- one row per loaded file
```

One-liner without opening a shell:

```bash
docker compose exec postgres psql -U postgres -d data_qa \
  -c "select ticker, date, close from fact_yfinance.eod_prices order by date desc limit 5;"
```

A GUI client (TablePlus, DBeaver, DataGrip) works too — point it at
`localhost:5432`, db `data_qa`, user/pass `postgres`/`postgres`.

### C) View data through dbt

dbt can preview model results and serve a browsable catalog of every table/column with
lineage:

```bash
cd dbt

# Preview rows from a model (runs a LIMITed SELECT, prints a table)
uv run dbt show --profiles-dir . -s fct_daily_prices --limit 10
uv run dbt show --profiles-dir . -s fct_valuation_metrics

# Run an ad-hoc query against the warehouse
uv run dbt show --profiles-dir . --inline "select * from {{ ref('dim_company') }}"

# Browsable docs site: model/column descriptions + the lineage DAG
uv run dbt docs generate --profiles-dir .
uv run dbt docs serve --profiles-dir .      # opens http://localhost:8080

# Data quality / freshness as testable questions
uv run dbt test --profiles-dir .
uv run dbt source freshness --profiles-dir .
```

`dbt docs serve` is the best way to *explore* what exists (tables, columns, descriptions,
how models depend on each other); `dbt show` is the quickest way to *peek at rows*;
`psql` is best for arbitrary SQL.

---

## Development

```bash
uv run ruff format . && uv run ruff check . --fix   # format + lint
uv run mypy src                                     # type check (strict)
uv run pytest -q                                    # tests (mocked yfinance, no live network)
docker compose down                                 # stop Postgres (add -v to wipe the volume)
```
