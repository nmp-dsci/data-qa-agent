-- Thin 1:1 view over raw_yfinance.corporate_actions: rename/cast only, NO dedup (§3.3).
select
    _row_id,
    ticker::text       as ticker,
    date::date         as date,
    action_type::text  as action_type,
    value::numeric     as value,
    source::text       as source,
    ingested_at,
    load_id,
    source_file
from {{ source('raw_yfinance', 'corporate_actions') }}
