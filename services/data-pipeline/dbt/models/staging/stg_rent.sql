-- Clean rental bond lodgements: derive lodgement year, keep positive weekly rents,
-- and derive house/unit so rent can be compared like-for-like with sales.
--
-- property_type: per docs/property_data/profile_rentboard.py, House + Townhouse
-- bonds are grouped as 'house'; Flat/Unit/Other as 'unit' (mirrors stg_sales so
-- the two marts share a join key).
with src as (
    select
        lodgement_dt,
        postcode,
        property_type,
        bedrooms,
        weekly_rent
    from {{ source('raw', 'rent') }}
)
select
    left(lodgement_dt, 4)::int as rent_year,
    postcode,
    upper(property_type) as property_type_code,
    case when upper(property_type) in ('H', 'T') then 'house' else 'unit' end as property_type,
    case when bedrooms ~ '^[0-9]+$' then bedrooms::int end as bedrooms,
    weekly_rent::numeric as weekly_rent
from src
where weekly_rent ~ '^[0-9]+$'
  and weekly_rent::numeric > 0
  and lodgement_dt ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
  and coalesce(postcode, '') <> ''
  and left(lodgement_dt, 4)::int between 2010 and 2024
