-- Thin 1:1 view over raw_yfinance.eod_prices: rename/cast only, NO dedup, NO joins (§3.3).
select
    _row_id,
    ticker::text                  as ticker,
    date::date                    as date,
    open::numeric                 as open,
    high::numeric                 as high,
    low::numeric                  as low,
    close::numeric                as close,
    adj_close::numeric            as adj_close,
    volume::bigint                as volume,
    currency::text                as currency,
    source::text                  as source,
    ingested_at,
    load_id,
    source_file
from {{ source('raw_yfinance', 'eod_prices') }}
