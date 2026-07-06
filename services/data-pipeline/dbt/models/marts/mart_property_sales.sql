{{
  config(
    materialized='table',
    alias='property_sales',
    indexes=[
      {'columns': ['postcode', 'suburb', 'property_type', 'month']},
      {'columns': ['area_band', 'zoning']},
    ],
    post_hook="{{ apply_dataset_rls('nsw_sales') }}"
  )
}}

-- One aggregate mart for the cleaned sales staging table.
--
-- Grain: postcode + suburb + property_type + area_band + zoning + month.
-- These are cleaned attributes from staging.property_sales. There are no
-- precomputed ALL rows and no derived growth/yield metrics here; consumers can
-- re-aggregate additive metrics (total_sale_value, n_sold) to any higher level
-- and compute growth, means, or joined yield in their own query.
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
    property_type,
    area_band,
    zoning,
    sale_month as month,
    sum(sale_price) as total_sale_value,
    count(*) as n_sold,
    round(avg(sale_price)::numeric) as avg_sale_price,
    round(percentile_cont(0.5) within group (order by sale_price)::numeric) as median_sale_price,
    min(sale_price) as min_sale_price,
    max(sale_price) as max_sale_price
from base
group by postcode, suburb, property_type, area_band, zoning, sale_month
