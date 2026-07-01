{{
  config(
    materialized='table',
    post_hook="{{ apply_dataset_rls(['nsw_sales', 'nsw_rent']) }}"
  )
}}

-- Gross rental yield by postcode + property_type + year: (median weekly rent *
-- 52) / median sale price, as a percentage. Implements the ratio calculation in
-- docs/property_data/property_yield_20241003.py, joining sales and rent on
-- (postcode, property_type, year) — the correct grain per that script, since
-- rent has no suburb and postcode<->suburb is not 1:1 (see int_postcode_geo.sql).
-- One row per (postcode, property_type, year); property_type is 'house', 'unit',
-- or 'ALL' (blended). Spans both datasets, so RLS requires nsw_sales AND nsw_rent.
select
    g.suburb,
    s.postcode,
    s.property_type,
    s.year,
    s.median_price,
    r.median_rent,
    round((r.median_rent * 52 / s.median_price * 100)::numeric, 2) as gross_yield_pct,
    s.n as n_sales,
    r.n as n_bonds
from {{ ref('int_sales_yearly') }} s
join {{ ref('int_rent_yearly') }} r
    on r.postcode = s.postcode and r.property_type = s.property_type and r.year = s.year
join {{ ref('int_postcode_geo') }} g on g.postcode = s.postcode
