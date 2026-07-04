{{
  config(
    materialized='table',
    indexes=[
      {'columns': ['postcode', 'suburb', 'property_type', 'month']},
      {'columns': ['zoning']},
    ],
    post_hook="{{ apply_dataset_rls('nsw_sales') }}"
  )
}}

-- Sale summary broken out by land SEGMENT — the same building block as
-- mart_sales_summary (sum/count/median per postcode + suburb + property_type +
-- month), with area_band and zoning added to the grain so "price by lot size" /
-- "price by planning zone" questions don't have to drop to the ~3M-row staging
-- table. area_band is the standardised size band from stg_sales ('<400' ..
-- '5000+', or 'unknown'); zoning is the NSW planning code (e.g. R2, RU5) or
-- 'unknown'. property_type is 'house', 'unit', or 'ALL' (blended).
--
-- Deliberately a SEPARATE mart, not extra columns on mart_sales_summary:
-- mart_sales_summary keeps its (postcode, suburb, property_type, month) grain
-- and the `property_type = 'ALL'` idiom the agent/yield mart rely on. Here
-- area_band and zoning are first-class parts of the grain (always specific —
-- there is NO 'ALL' area_band/zoning row), so summing across them double-counts:
-- for an all-segment figure use mart_sales_summary, not sum() over this table.
--
-- Same building-block rules as mart_sales_summary: no precomputed growth%, every
-- bucket kept, n_sold exposed so a query can pick its own reliability floor
-- (a single (suburb, area_band, zoning, month) cell is often one sale).
with base as (
    select
        postcode,
        suburb,
        property_type,
        coalesce(area_band, 'unknown') as area_band,
        coalesce(zoning, 'unknown') as zoning,
        sale_month,
        sale_price
    from {{ ref('stg_sales') }}
)
select
    postcode,
    suburb,
    case when grouping(property_type) = 1 then 'ALL' else property_type end as property_type,
    area_band,
    zoning,
    sale_month as month,
    sum(sale_price) as total_sale_value,
    count(*) as n_sold,
    round(percentile_cont(0.5) within group (order by sale_price)::numeric) as median_price
from base
group by grouping sets (
    (postcode, suburb, property_type, area_band, zoning, sale_month),
    (postcode, suburb, area_band, zoning, sale_month)
)
