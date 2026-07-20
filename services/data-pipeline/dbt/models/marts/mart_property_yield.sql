{{
  config(
    materialized='table',
    alias='property_yield',
    indexes=[
      {'columns': ['postcode', 'property_type', 'month']},
    ],
    post_hook="{{ apply_dataset_rls('nsw_yield') }}"
  )
}}

-- Gross rental yield: sales JOIN rent at postcode x property_type x MONTH — the
-- combined view neither single-source mart can answer, at the same month grain
-- (first-of-month) as marts.property_sales and marts.property_rent. Ported from
-- the ratio logic in docs/chronicle/property_yield_20241003.py.
--
-- Grain is month, not year: the two source marts are month-grain, and a yearly
-- rollup discarded every finer period irrecoverably. Built from the record-grain
-- staging tables and kept as the ADDITIVE components (total_sale_value / n_sold /
-- total_weekly_rent / n_rented), so a consumer that re-aggregates to quarter,
-- year, SA3 or across property types recomputes the average legs and the yield by
-- SUMMING the legs first — gross_yield_pct here is a derived ratio-of-averages,
-- NOT an average of per-row (or per-month) yields (which would be meaningless).
--
--   gross_yield_pct = 52 * avg_weekly_rent / avg_sale_price * 100
--                   = 52 * (total_weekly_rent/n_rented) / (total_sale_value/n_sold) * 100
--
-- Thin cells (a couple of sales over a couple of bonds) produce absurd yields, so
-- each leg is floored at a minimum volume WITHIN THE MONTH; assert_property_yield_range
-- fails the build if a surviving cell is still out of a sane band. Monthly grain is
-- inherently thinner than yearly, so only postcodes with genuine monthly volume
-- clear the floor — for a steadier read, roll the additive legs up to a wider
-- window. Its own dataset (nsw_yield) with its own RLS grant — a user needs the
-- yield grant specifically, which sidesteps cross-dataset grant semantics.
with sales as (
    select
        postcode,
        property_type,
        sale_month as month,
        sum(sale_price) as total_sale_value,
        count(*) as n_sold
    from {{ ref('stg_sales') }}
    group by postcode, property_type, sale_month
),
rent as (
    select
        postcode,
        property_type,
        rent_month as month,
        sum(weekly_rent) as total_weekly_rent,
        count(*) as n_rented
    from {{ ref('stg_rent') }}
    group by postcode, property_type, rent_month
)
select
    s.postcode,
    s.property_type,
    s.month,
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
    and s.month = r.month
where s.n_sold >= 5
  and r.n_rented >= 5
