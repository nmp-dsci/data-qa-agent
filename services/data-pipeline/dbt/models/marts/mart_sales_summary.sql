{{
  config(
    materialized='table',
    post_hook="{{ apply_dataset_rls('nsw_sales') }}"
  )
}}

-- Sale summary building block: total sale value, count of sales, and median
-- price per postcode + SUBURB + property_type + month (plus a blended 'ALL'
-- property_type row per postcode/suburb/month).
--
-- suburb is a real dimension here, taken straight from the sale records — NOT
-- a single dominant label borrowed per postcode. postcode <-> suburb is not
-- 1:1 (2076 = Wahroonga + Normanhurst + North Wahroonga), so aggregating by
-- suburb is the honest way to keep every locality: to get postcode-level
-- totals, SUM total_sale_value / n_sold across the suburbs of that postcode
-- (both are additive). We do NOT left-join a postcode->suburb bridge to attach
-- suburb, because that would fan a postcode's totals across its suburbs and
-- double-count on any postcode-level sum; grouping at the record grain avoids
-- that entirely.
--
-- Deliberately NOT a precomputed growth%: total_sale_value / n_sold (sum over
-- count) composes correctly across any re-aggregation window (quarter, year,
-- N-year, rolling average, or up to postcode level), so the agent computes
-- growth/rolling-average/yield itself from these building blocks. median_price
-- is kept for "typical price" questions and is more outlier-robust than the
-- mean, but it does NOT compose across re-aggregation (time or suburb) — use
-- total_sale_value/n_sold for anything that rolls up.
--
-- No minimum-count filter: every bucket with at least one sale is kept, so no
-- locality is dropped for being small (that was the whole point of preserving
-- suburb). n_sold is exposed so a consumer can filter to reliable buckets
-- (e.g. WHERE n_sold >= N) when a median or a single month needs to be
-- trustworthy — the mart keeps the data; the query decides the threshold.
with by_type as (
    select
        postcode,
        suburb,
        property_type,
        sale_month as month,
        sum(sale_price) as total_sale_value,
        count(*) as n_sold,
        percentile_cont(0.5) within group (order by sale_price) as median_price
    from {{ ref('stg_sales') }}
    group by postcode, suburb, property_type, sale_month
),
blended as (
    select
        postcode,
        suburb,
        'ALL' as property_type,
        sale_month as month,
        sum(sale_price) as total_sale_value,
        count(*) as n_sold,
        percentile_cont(0.5) within group (order by sale_price) as median_price
    from {{ ref('stg_sales') }}
    group by postcode, suburb, sale_month
)
select
    postcode,
    suburb,
    property_type,
    month,
    total_sale_value,
    n_sold,
    round(median_price::numeric) as median_price
from by_type
union all
select
    postcode,
    suburb,
    property_type,
    month,
    total_sale_value,
    n_sold,
    round(median_price::numeric) as median_price
from blended
