-- Thin 1:1 view over raw_yfinance.financial_statements: rename/cast only, NO dedup (§3.3).
select
    _row_id,
    ticker::text       as ticker,
    statement::text    as statement,
    freq::text         as freq,
    period_end::date   as period_end,
    line_item::text    as line_item,
    value::numeric     as value,
    currency::text     as currency,
    source::text       as source,
    ingested_at,
    load_id,
    source_file
from {{ source('raw_yfinance', 'financial_statements') }}
