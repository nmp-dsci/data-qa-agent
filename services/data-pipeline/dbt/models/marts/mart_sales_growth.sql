{{
  config(
    materialized='table',
    post_hook="{{ apply_dataset_rls('nsw_sales') }}"
  )
}}

-- Sale-price growth by postcode + property_type: median price in the first vs
-- last year that had enough sales, over the available window. One row per
-- (postcode, property_type), where property_type is 'house', 'unit', or 'ALL'
-- (blended). suburb is a friendly label, not the join key — see
-- int_postcode_geo.sql.
with yearly as (
    select * from {{ ref('int_sales_yearly') }}
),
bounds as (
    select
        postcode,
        property_type,
        min(year) as first_year,
        max(year) as last_year,
        sum(n) as n_sales
    from yearly
    group by postcode, property_type
    having count(*) >= 2
),
paired as (
    select
        b.postcode,
        b.property_type,
        b.first_year,
        b.last_year,
        b.n_sales,
        yf.median_price as first_median_price,
        yl.median_price as last_median_price
    from bounds b
    join yearly yf on yf.postcode = b.postcode and yf.property_type = b.property_type
        and yf.year = b.first_year
    join yearly yl on yl.postcode = b.postcode and yl.property_type = b.property_type
        and yl.year = b.last_year
)
select
    p.postcode,
    g.suburb,
    p.property_type,
    p.first_year,
    p.last_year,
    round(p.first_median_price::numeric) as first_median_price,
    round(p.last_median_price::numeric) as last_median_price,
    round(((p.last_median_price - p.first_median_price) / p.first_median_price * 100)::numeric, 1)
        as sales_growth_pct,
    p.n_sales
from paired p
join {{ ref('int_postcode_geo') }} g using (postcode)
