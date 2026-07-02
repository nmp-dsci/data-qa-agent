{{
  config(
    materialized='table',
    indexes=[
      {'columns': ['rent_id'], 'unique': True},
      {'columns': ['postcode', 'property_type', 'rent_month']},
    ],
    post_hook="{{ apply_dataset_rls('nsw_rent') }}"
  )
}}

-- Clean rental bond lodgements: derive lodgement date (year + month), keep
-- positive weekly rents, and derive house/unit so rent can be compared
-- like-for-like with sales. raw.rent only has 5 columns and all of them are
-- already used here — nothing else to widen (see stg_sales.sql for the
-- record of what was and wasn't brought in from raw.sales).
--
-- property_type: per docs/property_data/profile_rentboard.py, House + Townhouse
-- bonds are grouped as 'house'; Flat/Unit/Other as 'unit' (mirrors stg_sales so
-- the two datasets share a join key: property_type + month).
--
-- rent_id: raw.rent carries no id-like column at all, and two genuinely
-- distinct bonds can legitimately share every other column (same postcode,
-- type, bedrooms, rent, lodgement date) — a hash of the natural columns could
-- wrongly collapse two real rows into one, so row_number() over a fully
-- deterministic order is used instead: an arbitrary but stable-per-build key,
-- not a natural business key.
with src as (
    select
        lodgement_dt,
        postcode,
        property_type,
        bedrooms,
        weekly_rent
    from {{ source('raw', 'rent') }}
),
cleaned as (
    select
        lodgement_dt::date as rent_date,
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
)
select
    row_number() over (
        order by rent_date, postcode, property_type, bedrooms, weekly_rent
    ) as rent_id,
    rent_date,
    rent_year,
    date_trunc('month', rent_date)::date as rent_month,
    postcode,
    property_type_code,
    property_type,
    bedrooms,
    weekly_rent
from cleaned
