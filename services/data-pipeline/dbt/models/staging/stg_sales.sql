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

-- Local, RLS-scoped copy of propertyiq's clean record-grain table. The cleaning
-- itself (RESIDENCE filter, $10k-$8M price band, hectare->sqm, house/unit from
-- strata_no, area bands) lives in propertyiq_getdata/dbt/models/staging/ --
-- this model only materialises the foreign table locally so the app's RLS
-- policy can attach (policies cannot be put on foreign tables) and record-level
-- queries stay local. Column list is explicit so a new upstream column is a
-- deliberate change here, not a silent one.
select
    sale_id,
    property_id,
    suburb,
    postcode,
    property_type,
    sale_date,
    sale_year,
    sale_month,
    sale_price,
    area_sqm,
    area_band,
    area_type,
    zoning,
    house_no,
    street_name,
    unit_no,
    prop_name
from {{ source('propertyiq_staging', 'property_sales') }}
