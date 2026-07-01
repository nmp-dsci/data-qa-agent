-- Clean residential sales: normalise suburb/postcode, derive the sale year from
-- the messy YYYYMMDD(.0) contract date, and keep positive-price residence sales.
with src as (
    select
        property_id,
        locality,
        split_part(postcode, '.', 1) as postcode,
        split_part(contract_dt, '.', 1) as contract_ymd,
        sale_price,
        prop_nature
    from {{ source('raw', 'sales') }}
)
select
    property_id,
    initcap(locality) as suburb,
    postcode,
    left(contract_ymd, 4)::int as sale_year,
    sale_price::numeric as sale_price
from src
where prop_nature = 'R'
  and coalesce(locality, '') <> ''
  and sale_price ~ '^[0-9]+$'
  and sale_price::numeric > 0
  and contract_ymd ~ '^[0-9]{8}$'
  and left(contract_ymd, 4)::int between 2010 and 2024
