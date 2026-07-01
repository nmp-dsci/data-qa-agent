-- Median weekly rent per postcode per property_type per year (years with enough
-- volume to trust), plus a blended 'ALL' property_type row per postcode/year —
-- see int_sales_yearly.sql for why.
with by_type as (
    select
        postcode,
        property_type,
        rent_year as year,
        percentile_cont(0.5) within group (order by weekly_rent) as median_rent,
        count(*) as n
    from {{ ref('stg_rent') }}
    group by postcode, property_type, rent_year
    having count(*) >= {{ var('min_rent_year') }}
),
blended as (
    select
        postcode,
        'ALL' as property_type,
        rent_year as year,
        percentile_cont(0.5) within group (order by weekly_rent) as median_rent,
        count(*) as n
    from {{ ref('stg_rent') }}
    group by postcode, rent_year
    having count(*) >= {{ var('min_rent_year') }}
)
select * from by_type
union all
select * from blended
