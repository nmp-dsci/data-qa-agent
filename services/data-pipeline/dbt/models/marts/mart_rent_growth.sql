{{
  config(
    materialized='table',
    post_hook="{{ apply_dataset_rls('nsw_rent') }}"
  )
}}

-- Weekly-rent growth by postcode + property_type: median rent in the first vs
-- last year that had enough bonds, over the available window. One row per
-- (postcode, property_type), where property_type is 'house', 'unit', or 'ALL'
-- (blended). suburb is a friendly label, not the join key — see
-- int_postcode_geo.sql.
with yearly as (
    select * from {{ ref('int_rent_yearly') }}
),
bounds as (
    select
        postcode,
        property_type,
        min(year) as first_year,
        max(year) as last_year,
        sum(n) as n_bonds
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
        b.n_bonds,
        yf.median_rent as first_median_rent,
        yl.median_rent as last_median_rent
    from bounds b
    join yearly yf on yf.postcode = b.postcode and yf.property_type = b.property_type
        and yf.year = b.first_year
    join yearly yl on yl.postcode = b.postcode and yl.property_type = b.property_type
        and yl.year = b.last_year
)
select
    g.suburb,
    p.postcode,
    p.property_type,
    p.first_year,
    p.last_year,
    round(p.first_median_rent::numeric) as first_median_rent,
    round(p.last_median_rent::numeric) as last_median_rent,
    round(((p.last_median_rent - p.first_median_rent) / p.first_median_rent * 100)::numeric, 1)
        as rent_growth_pct,
    p.n_bonds
from paired p
join {{ ref('int_postcode_geo') }} g using (postcode)
