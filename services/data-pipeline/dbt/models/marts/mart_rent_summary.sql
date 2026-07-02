{{
  config(
    materialized='table',
    post_hook="{{ apply_dataset_rls('nsw_rent') }}"
  )
}}

-- Rent summary building block: total weekly rent, count of bonds, and median
-- rent per postcode + property_type + month (plus a blended 'ALL'
-- property_type row per postcode/month).
--
-- No suburb column here, unlike mart_sales_summary: raw.rent has no locality
-- field at all (a bond records only postcode, type, bedrooms, rent, date), so
-- there is no honest per-suburb rent — a bond in postcode 2076 could be in
-- Wahroonga or Normanhurst and the source doesn't say. We deliberately do NOT
-- attach a suburb by left-joining a postcode->suburb bridge: that would copy a
-- postcode's rent onto every suburb it contains and double-count on any sum.
-- To answer "rent in <suburb>", resolve the suburb to its postcode(s) via
-- int_postcode_geo (or mart_sales_summary) and query rent by postcode.
--
-- Same building-block design as mart_sales_summary: sum/count, no precomputed
-- growth% or ratio; no minimum-count filter (n_rented is exposed so the query
-- can choose a reliability threshold).
select
    postcode,
    property_type,
    rent_month as month,
    sum(weekly_rent) as total_weekly_rent,
    count(*) as n_rented,
    round(percentile_cont(0.5) within group (order by weekly_rent)::numeric) as median_rent
from {{ ref('stg_rent') }}
group by postcode, property_type, rent_month
union all
select
    postcode,
    'ALL' as property_type,
    rent_month as month,
    sum(weekly_rent) as total_weekly_rent,
    count(*) as n_rented,
    round(percentile_cont(0.5) within group (order by weekly_rent)::numeric) as median_rent
from {{ ref('stg_rent') }}
group by postcode, rent_month
