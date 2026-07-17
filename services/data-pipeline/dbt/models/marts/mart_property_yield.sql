{{
  config(
    materialized='table',
    alias='property_yield',
    indexes=[
      {'columns': ['postcode', 'property_type', 'year']},
    ],
    post_hook="{{ apply_dataset_rls('nsw_yield') }}"
  )
}}

-- Gross rental yield: sales JOIN rent at postcode x property_type x year, the
-- combined view that neither single-source mart can answer. Ported from the
-- ratio logic in docs/chronicle/property_yield_20241003.py.
--
-- Built from the record-grain staging tables (not the month-grain marts) so the
-- yearly rollup is honest. Keeps the ADDITIVE components (total_sale_value /
-- n_sold / total_weekly_rent / n_rented) as columns, so a consumer that
-- re-aggregates to SA3 or across property types can recompute the average legs
-- and the yield correctly — gross_yield_pct here is a derived ratio-of-averages,
-- NOT an average of per-row yields (which would be meaningless).
--
--   gross_yield_pct = 52 * avg_weekly_rent / avg_sale_price * 100
--                   = 52 * (total_weekly_rent/n_rented) / (total_sale_value/n_sold) * 100
--
-- Thin cells (one sale over one bond) produce absurd yields, so both legs are
-- floored at a minimum volume; assert_property_yield_range fails the build if a
-- surviving cell is still out of a sane band. Its own dataset (nsw_yield) with
-- its own RLS grant — a user needs the yield grant specifically, which sidesteps
-- cross-dataset grant semantics.
with sales as (
    select
        postcode,
        property_type,
        sale_year as year,
        sum(sale_price) as total_sale_value,
        count(*) as n_sold
    from {{ ref('stg_sales') }}
    group by postcode, property_type, sale_year
),
rent as (
    select
        postcode,
        property_type,
        rent_year as year,
        sum(weekly_rent) as total_weekly_rent,
        count(*) as n_rented
    from {{ ref('stg_rent') }}
    group by postcode, property_type, rent_year
)
select
    s.postcode,
    s.property_type,
    s.year,
    s.total_sale_value,
    s.n_sold,
    r.total_weekly_rent,
    r.n_rented,
    round((s.total_sale_value / s.n_sold)::numeric) as avg_sale_price,
    round((r.total_weekly_rent / r.n_rented)::numeric) as avg_weekly_rent,
    round(
        (52 * (r.total_weekly_rent / r.n_rented) / (s.total_sale_value / s.n_sold) * 100)::numeric,
        2
    ) as gross_yield_pct
from sales s
join rent r
    on s.postcode = r.postcode
    and s.property_type = r.property_type
    and s.year = r.year
where s.n_sold >= 5
  and r.n_rented >= 5
