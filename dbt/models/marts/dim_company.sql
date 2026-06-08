-- Conformed company dimension. Profile snapshot enriched with the S&P 500 seed.
with profile as (
    select *
    from {{ ref('company_profile') }}
),

seed as (
    select
        ticker,
        company_name      as seed_company_name,
        gics_sector,
        gics_sub_industry,
        date_added,
        cik
    from {{ ref('sp500_constituents') }}
)

select
    p.ticker,
    coalesce(p.company_name, s.seed_company_name) as company_name,
    p.sector,
    p.industry,
    s.gics_sector,
    s.gics_sub_industry,
    p.exchange,
    p.country,
    p.currency,
    s.cik,
    s.date_added                                  as sp500_date_added,
    (s.ticker is not null)                        as in_sp500,
    p.ingested_at                                 as profile_ingested_at
from profile p
left join seed s
    on p.ticker = s.ticker
