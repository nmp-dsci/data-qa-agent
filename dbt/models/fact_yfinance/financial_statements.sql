-- Deduped, PK-enforced silver table. One row per
-- (ticker, statement, freq, period_end, line_item): latest pull / restatement wins.
with ranked as (
    select
        *,
        row_number() over (
            partition by ticker, statement, freq, period_end, line_item
            order by ingested_at desc, _row_id desc
        ) as rn
    from {{ ref('stg_financial_statements') }}
)
select
    ticker,
    statement,
    freq,
    period_end,
    line_item,
    value,
    currency,
    source,
    ingested_at,
    load_id,
    source_file
from ranked
where rn = 1
