{{
  config(
    materialized='table',
    alias='property_rent',
    indexes=[
      {'columns': ['postcode', 'property_type', 'bedroom_band', 'month']},
    ],
    post_hook="{{ apply_dataset_rls('nsw_rent') }}"
  )
}}

-- One aggregate mart for the cleaned rent staging table.
--
-- Grain: postcode + property_type + bedroom_band + month. Rent has no suburb
-- in the raw source, so this mart stays at the honest postcode grain. There are
-- no precomputed ALL rows or derived growth metrics; consumers can re-aggregate
-- total_weekly_rent/n_rented to broader levels and compute derived metrics.
select
    postcode,
    property_type,
    bedroom_band,
    rent_month as month,
    sum(weekly_rent) as total_weekly_rent,
    count(*) as n_rented,
    round(avg(weekly_rent)::numeric) as avg_weekly_rent,
    round(percentile_cont(0.5) within group (order by weekly_rent)::numeric) as median_weekly_rent,
    min(weekly_rent) as min_weekly_rent,
    max(weekly_rent) as max_weekly_rent
from {{ ref('stg_rent') }}
group by postcode, property_type, bedroom_band, rent_month
