-- Median sale price per postcode per property_type per year (years with enough
-- volume to trust). Also unions in an 'ALL' property_type row per postcode/year
-- (blended across house+unit) so questions that don't specify a type still get
-- a single answer — mirrors the wildcard "all" dimension used in
-- docs/property_data/profile_nswgov.py's cross-tab generator.
with by_type as (
    select
        postcode,
        property_type,
        sale_year as year,
        percentile_cont(0.5) within group (order by sale_price) as median_price,
        count(*) as n
    from {{ ref('stg_sales') }}
    group by postcode, property_type, sale_year
    having count(*) >= {{ var('min_sales_year') }}
),
blended as (
    select
        postcode,
        'ALL' as property_type,
        sale_year as year,
        percentile_cont(0.5) within group (order by sale_price) as median_price,
        count(*) as n
    from {{ ref('stg_sales') }}
    group by postcode, sale_year
    having count(*) >= {{ var('min_sales_year') }}
)
select * from by_type
union all
select * from blended
