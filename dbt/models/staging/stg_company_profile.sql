-- Thin 1:1 view over raw_yfinance.company_profile: rename/cast only, NO dedup (§3.3).
select
    _row_id,
    ticker::text        as ticker,
    company_name::text  as company_name,
    sector::text        as sector,
    industry::text      as industry,
    currency::text      as currency,
    exchange::text      as exchange,
    country::text       as country,
    ingested_at,
    load_id,
    source_file
from {{ source('raw_yfinance', 'company_profile') }}
