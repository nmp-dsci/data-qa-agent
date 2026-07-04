{{
  config(
    materialized='table',
    indexes=[
      {'columns': ['postcode', 'property_type', 'bedroom_band', 'month']},
    ],
    post_hook="{{ apply_dataset_rls('nsw_rent') }}"
  )
}}

-- Rent summary broken out by BEDROOM band — the same building block as
-- mart_rent_summary (sum/count/median per postcode + property_type + month), but
-- with bedroom_band added to the grain so "rent by bedroom" questions don't have
-- to drop to the ~3M-row staging table. bedroom_band is '0'..'4', '5+' or
-- 'unknown' (see stg_rent); property_type is 'house', 'unit', or 'ALL' (blended).
--
-- Deliberately a SEPARATE mart, not an extra column on mart_rent_summary:
-- mart_rent_summary keeps its (postcode, property_type, month) grain and the
-- `property_type = 'ALL'` idiom the agent/yield mart rely on. Here bedroom_band
-- is a first-class part of the grain (always a specific band — there is NO
-- 'ALL' bedroom row), so summing across bedroom_band double-counts: for an
-- all-bedroom figure use mart_rent_summary, not sum() over this table.
--
-- Same building-block rules as mart_rent_summary: no precomputed growth%, every
-- bucket kept, n_rented exposed so a query can pick its own reliability floor.
select
    postcode,
    case when grouping(property_type) = 1 then 'ALL' else property_type end as property_type,
    bedroom_band,
    rent_month as month,
    sum(weekly_rent) as total_weekly_rent,
    count(*) as n_rented,
    round(percentile_cont(0.5) within group (order by weekly_rent)::numeric) as median_rent
from {{ ref('stg_rent') }}
group by grouping sets (
    (postcode, property_type, bedroom_band, rent_month),
    (postcode, bedroom_band, rent_month)
)
