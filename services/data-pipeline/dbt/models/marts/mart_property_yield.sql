{{
  config(
    materialized='table',
    post_hook="{{ apply_dataset_rls(['nsw_sales', 'nsw_rent']) }}"
  )
}}

-- Sales + rent summary building blocks pre-joined per postcode + SUBURB +
-- property_type + month. Spans both datasets, so RLS requires nsw_sales AND
-- nsw_rent. No precomputed gross_yield_pct — the agent computes yield (and any
-- other ratio) as (median_rent * 52 / median_price) * 100, or the
-- volume-weighted (total_weekly_rent/n_rented) / (total_sale_value/n_sold), at
-- whatever window the question needs.
--
-- Grain is suburb-level because price is: suburb comes from the sales side
-- (mart_sales_summary), which has a true per-suburb price. Rent has no suburb
-- (see mart_rent_summary), so each suburb in a postcode is joined to that
-- postcode's shared rent — i.e. the rent columns are a postcode-level figure
-- repeated across the postcode's suburbs. That's correct for a yield RATIO
-- (per-suburb price vs the postcode's rent), but it means the rent columns
-- must NOT be summed across suburbs of a postcode (they'd multiply). Sum rent
-- from mart_rent_summary instead when you need a rent total.
select
    s.postcode,
    s.suburb,
    s.property_type,
    s.month,
    s.total_sale_value,
    s.n_sold,
    s.median_price,
    r.total_weekly_rent,
    r.n_rented,
    r.median_rent
from {{ ref('mart_sales_summary') }} s
join {{ ref('mart_rent_summary') }} r
    on r.postcode = s.postcode and r.property_type = s.property_type and r.month = s.month
