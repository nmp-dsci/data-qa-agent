{{
  config(
    materialized='table',
    post_hook="{{ apply_dataset_rls('nsw_sales') }}"
  )
}}

-- Sale-price growth by suburb: median price in the first vs last year that had
-- enough sales, over the available window. One row per suburb (join key).
with yearly as (
    select * from {{ ref('int_sales_yearly') }}
),
bounds as (
    select
        suburb,
        min(year) as first_year,
        max(year) as last_year,
        sum(n) as n_sales
    from yearly
    group by suburb
    having count(*) >= 2
),
paired as (
    select
        b.suburb,
        b.first_year,
        b.last_year,
        b.n_sales,
        yf.median_price as first_median_price,
        yl.median_price as last_median_price
    from bounds b
    join yearly yf on yf.suburb = b.suburb and yf.year = b.first_year
    join yearly yl on yl.suburb = b.suburb and yl.year = b.last_year
)
select
    p.suburb,
    sp.postcode,
    p.first_year,
    p.last_year,
    round(p.first_median_price::numeric) as first_median_price,
    round(p.last_median_price::numeric) as last_median_price,
    round(((p.last_median_price - p.first_median_price) / p.first_median_price * 100)::numeric, 1)
        as sales_growth_pct,
    p.n_sales
from paired p
join {{ ref('int_suburb_postcode') }} sp using (suburb)
