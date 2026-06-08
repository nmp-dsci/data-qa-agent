-- Deduped, PK-enforced silver table. One row per (ticker, date, action_type): latest wins.
with ranked as (
    select
        *,
        row_number() over (
            partition by ticker, date, action_type
            order by ingested_at desc, _row_id desc
        ) as rn
    from {{ ref('stg_corporate_actions') }}
)
select
    ticker,
    date,
    action_type,
    value,
    source,
    ingested_at,
    load_id,
    source_file
from ranked
where rn = 1
