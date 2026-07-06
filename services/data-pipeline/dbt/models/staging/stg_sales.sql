{{
  config(
    materialized='table',
    alias='property_sales',
    indexes=[
      {'columns': ['sale_id'], 'unique': True},
      {'columns': ['postcode', 'property_type', 'sale_month']},
    ],
    post_hook="{{ apply_dataset_rls('nsw_sales') }}"
  )
}}

-- Clean residential sales: normalise suburb/postcode, derive the sale date
-- (year + month) from the messy YYYYMMDD(.0) contract date, derive house/unit,
-- and drop non-market prices (see the two notes below). Staging is deliberately
-- kept wide and record-grain — it's the clean, verified mirror of raw.property_sales,
-- not a curated subset; only columns with unverified semantics (sale_interest,
-- sale_counter, prop_nature, component_cd, sale_cd, record_type) or that
-- duplicate something handled elsewhere (district_code) are left out.
--
-- property_type: per docs/property_data/profile_nswgov.py, a strata number means
-- the sale is a strata (unit-titled) property; no strata number means a house.
-- NOTE: dlt loads blank CSV cells as '' (not SQL NULL), so the check is on empty
-- string, not IS NULL — the pandas `.isnull()` equivalent for a text column here.
--
-- price floor: NSW sale records include non-arms-length transfers (family /
-- deceased-estate transfers recorded at nominal consideration) with no clean
-- price cliff separating them from genuine cheap sales — e.g. one postcode had
-- 17 same-year "sales" from $200 to $140,000. $10,000 is a conventional floor
-- for Australian property data; it won't catch every non-market row (the noise
-- is a continuum), but it keeps single nominal transfers out of the summary
-- marts. The marts no longer drop thin buckets, so they expose n_sold instead
-- for callers to weight/filter a median they don't fully trust.
--
-- sale_id: property_id alone isn't unique per row — split/fractional-interest
-- settlements share a property_id and contract date (see the price-floor note
-- above). They usually sell at different fractional prices, but not always
-- (e.g. an equal-share split can record the same price twice) — confirmed
-- against the sample data, where a naive hash of property_id/date/price
-- collided. row_number() over a fully deterministic order guarantees
-- uniqueness unconditionally instead: an arbitrary but stable-per-build
-- surrogate key, not a natural business key (same reasoning as stg_rent).
with src as (
    select
        property_id,
        locality,
        split_part(postcode, '.', 1) as postcode,
        split_part(contract_dt, '.', 1) as contract_ymd,
        sale_price,
        prop_purpose,
        strata_no,
        area_sqm,
        area_type,
        zoning,
        house_no,
        street_name,
        unit_no,
        prop_name
    from {{ source('raw', 'property_sales') }}
),
cleaned as (
    select
        property_id,
        initcap(locality) as suburb,
        postcode,
        case when coalesce(strata_no, '') = '' then 'house' else 'unit' end as property_type,
        to_date(contract_ymd, 'YYYYMMDD') as sale_date,
        left(contract_ymd, 4)::int as sale_year,
        sale_price::numeric as sale_price,
        -- area standardised to square metres: source area_type 'H' is hectares
        -- (1 ha = 10,000 sqm), 'M' is already square metres. Any other/blank unit
        -- is untrustworthy (blank-area_type rows carry a ~0 area anyway), so area
        -- drops to NULL — otherwise a 10 ha rural parcel (area_sqm 10, type H)
        -- would band as '<400'. area_band below buckets this standardised value.
        case
            when area_sqm !~ '^[0-9]+(\.[0-9]+)?$' then null
            when upper(nullif(area_type, '')) = 'H' then round(area_sqm::numeric * 10000)
            when upper(nullif(area_type, '')) = 'M' then round(area_sqm::numeric)
        end as area_sqm,
        upper(nullif(area_type, '')) as area_type,
        nullif(zoning, '') as zoning,
        nullif(house_no, '') as house_no,
        initcap(nullif(street_name, '')) as street_name,
        nullif(unit_no, '') as unit_no,
        initcap(nullif(prop_name, '')) as prop_name
    from src
    where prop_purpose = 'RESIDENCE'
      and coalesce(locality, '') <> ''
      and sale_price ~ '^[0-9]+$'
      and sale_price::numeric between 10000 and 8000000  -- upper cap matches property_yield_20241003.py
      and contract_ymd ~ '^[0-9]{8}$'
      and left(contract_ymd, 4)::int >= 2010  -- lower bound only, no upper cap: the latest data always flows through
)
select
    row_number() over (
        order by property_id, sale_date, sale_price, property_type
    ) as sale_id,
    property_id,
    suburb,
    postcode,
    property_type,
    sale_date,
    sale_year,
    date_trunc('month', sale_date)::date as sale_month,
    sale_price,
    area_sqm,
    -- Bands over the standardised (square-metre) area, so a hectare-typed rural
    -- parcel sorts by its true size. 5000+ splits genuine large/rural land off
    -- the old catch-all '1000+' now that hectare rows are converted, not raw.
    case
        when area_sqm is null then null
        when area_sqm < 400 then '<400'
        when area_sqm < 700 then '400-700'
        when area_sqm < 1000 then '700-1000'
        when area_sqm < 5000 then '1000-5000'
        else '5000+'
    end as area_band,
    area_type,
    zoning,
    house_no,
    street_name,
    unit_no,
    prop_name
from cleaned
