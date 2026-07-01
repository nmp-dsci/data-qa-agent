-- Clean rental bond lodgements: derive lodgement year, keep positive weekly rents.
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
    upper(property_type) as property_type,
    case when bedrooms ~ '^[0-9]+$' then bedrooms::int end as bedrooms,
    weekly_rent::numeric as weekly_rent
from src
where weekly_rent ~ '^[0-9]+$'
  and weekly_rent::numeric > 0
  and lodgement_dt ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
  and coalesce(postcode, '') <> ''
  and left(lodgement_dt, 4)::int between 2010 and 2024
