{{
  config(
    materialized='table',
    post_hook="{{ apply_dataset_rls('nsw_rent') }}"
  )
}}

-- Weekly-rent growth by postcode, attached to a suburb via the dominant-postcode
-- map so it shares the `suburb` join key with mart_sales_growth. One row per suburb.
with yearly as (
    select * from {{ ref('int_rent_yearly') }}
),
bounds as (
    select
        postcode,
        min(year) as first_year,
        max(year) as last_year,
        sum(n) as n_bonds
    from yearly
    group by postcode
    having count(*) >= 2
),
paired as (
    select
        b.postcode,
        b.first_year,
        b.last_year,
        b.n_bonds,
        yf.median_rent as first_median_rent,
        yl.median_rent as last_median_rent
    from bounds b
    join yearly yf on yf.postcode = b.postcode and yf.year = b.first_year
    join yearly yl on yl.postcode = b.postcode and yl.year = b.last_year
)
select
    sp.suburb,
    p.postcode,
    p.first_year,
    p.last_year,
    round(p.first_median_rent::numeric) as first_median_rent,
    round(p.last_median_rent::numeric) as last_median_rent,
    round(((p.last_median_rent - p.first_median_rent) / p.first_median_rent * 100)::numeric, 1)
        as rent_growth_pct,
    p.n_bonds
from {{ ref('int_suburb_postcode') }} sp
join paired p on p.postcode = sp.postcode
