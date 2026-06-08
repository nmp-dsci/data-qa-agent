-- Deduped, PK-enforced silver table. One row per (ticker): latest pull wins.
with ranked as (
    select
        *,
        row_number() over (
            partition by ticker
            order by ingested_at desc, _row_id desc
        ) as rn
    from {{ ref('stg_company_profile') }}
)
select
    ticker,
    company_name,
    sector,
    industry,
    currency,
    exchange,
    country,
    ingested_at,
    load_id,
    source_file
from ranked
where rn = 1
