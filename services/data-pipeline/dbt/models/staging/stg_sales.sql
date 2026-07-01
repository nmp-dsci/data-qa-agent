-- Clean residential sales: normalise suburb/postcode, derive the sale year from
-- the messy YYYYMMDD(.0) contract date, derive house/unit, and drop non-market
-- prices (see the two notes below).
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
-- is a continuum), but combined with min_sales_year in dbt_project.yml it
-- keeps single non-market transfers from dominating a thin bucket's median.
with src as (
    select
        property_id,
        locality,
        split_part(postcode, '.', 1) as postcode,
        split_part(contract_dt, '.', 1) as contract_ymd,
        sale_price,
        prop_purpose,
        strata_no
    from {{ source('raw', 'sales') }}
)
select
    property_id,
    initcap(locality) as suburb,
    postcode,
    case when coalesce(strata_no, '') = '' then 'house' else 'unit' end as property_type,
    left(contract_ymd, 4)::int as sale_year,
    sale_price::numeric as sale_price
from src
where prop_purpose = 'RESIDENCE'
  and coalesce(locality, '') <> ''
  and sale_price ~ '^[0-9]+$'
  and sale_price::numeric between 10000 and 8000000  -- upper cap matches property_yield_20241003.py
  and contract_ymd ~ '^[0-9]{8}$'
  and left(contract_ymd, 4)::int between 2010 and 2024
