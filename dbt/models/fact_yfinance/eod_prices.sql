-- Deduped, PK-enforced silver table. One row per (ticker, date): latest pull wins.
-- Postgres has no QUALIFY, so use a windowed subselect (§3.3).
with ranked as (
    select
        *,
        row_number() over (
            partition by ticker, date
            order by ingested_at desc, _row_id desc
        ) as rn
    from {{ ref('stg_eod_prices') }}
)
select
    ticker,
    date,
    open,
    high,
    low,
    close,
    adj_close,
    volume,
    currency,
    source,
    ingested_at,
    load_id,
    source_file
from ranked
where rn = 1
