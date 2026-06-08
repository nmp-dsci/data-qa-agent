-- raw_yfinance: append-only "bronze" schema (spec §2.2).
--
-- Mirrors the raw CSV contracts (§1.3). NO natural-key primary keys: the raw
-- layer is append-only and must accept duplicate / restated rows. Each data
-- table has a surrogate identity PK plus load-lineage columns
-- (load_id, source_file, _loaded_at) and a NON-UNIQUE natural-key index that
-- serves the dbt dedup queries (§3.3).
--
-- Applied idempotently via CREATE ... IF NOT EXISTS, so re-running migrate is a
-- no-op.

CREATE SCHEMA IF NOT EXISTS raw_yfinance;

CREATE TABLE IF NOT EXISTS raw_yfinance.eod_prices (
    _row_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker       text        NOT NULL,
    date         date        NOT NULL,
    open         numeric,
    high         numeric,
    low          numeric,
    close        numeric,
    adj_close    numeric,
    volume       bigint,
    currency     text,
    source       text        NOT NULL,
    ingested_at  timestamptz NOT NULL,            -- pull time (from the source row)
    load_id      uuid        NOT NULL,            -- the load run that appended this row
    source_file  text        NOT NULL,            -- e.g. AAPL_202606040959.csv
    _loaded_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_eod_prices_nk
    ON raw_yfinance.eod_prices (ticker, date);

CREATE TABLE IF NOT EXISTS raw_yfinance.corporate_actions (
    _row_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker       text        NOT NULL,
    date         date        NOT NULL,
    action_type  text        NOT NULL,
    value        numeric,
    source       text        NOT NULL,
    ingested_at  timestamptz NOT NULL,
    load_id      uuid        NOT NULL,
    source_file  text        NOT NULL,
    _loaded_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_corp_actions_nk
    ON raw_yfinance.corporate_actions (ticker, date, action_type);

-- Single table for all three statements (balance_sheet / income_statement /
-- cash_flow), discriminated by `statement` — simpler for dbt sources (§2.2).
CREATE TABLE IF NOT EXISTS raw_yfinance.financial_statements (
    _row_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker       text        NOT NULL,
    statement    text        NOT NULL,
    freq         text        NOT NULL,
    period_end   date        NOT NULL,
    line_item    text        NOT NULL,
    value        numeric,
    currency     text,
    source       text        NOT NULL,
    ingested_at  timestamptz NOT NULL,
    load_id      uuid        NOT NULL,
    source_file  text        NOT NULL,
    _loaded_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fin_stmts_nk
    ON raw_yfinance.financial_statements (ticker, statement, freq, period_end, line_item);

CREATE TABLE IF NOT EXISTS raw_yfinance.company_profile (
    _row_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker       text        NOT NULL,
    company_name text,
    sector       text,
    industry     text,
    currency     text,
    exchange     text,
    country      text,
    ingested_at  timestamptz NOT NULL,
    load_id      uuid        NOT NULL,
    source_file  text        NOT NULL,
    _loaded_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_company_profile_nk
    ON raw_yfinance.company_profile (ticker);

-- Audit log: one row per loaded source file (the loader's idempotency ledger).
-- UNIQUE(source_file) enforces "load each raw file at most once".
CREATE TABLE IF NOT EXISTS raw_yfinance._load_audit (
    load_id       uuid        NOT NULL,
    dataset       text        NOT NULL,
    ticker        text,
    source_file   text        NOT NULL,
    rows_inserted int,
    started_at    timestamptz,
    finished_at   timestamptz,
    status        text,
    message       text,
    UNIQUE (source_file)
);
