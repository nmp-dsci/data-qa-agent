{{
  config(
    materialized='table',
    alias='property_rent',
    indexes=[
      {'columns': ['rent_id'], 'unique': True},
      {'columns': ['postcode', 'property_type', 'rent_month']},
    ],
    post_hook="{{ apply_dataset_rls('nsw_rent') }}"
  )
}}

-- Local, RLS-scoped copy of propertyiq's clean record-grain table. The cleaning
-- itself (positive rents, H/T -> house else unit, bedroom bands) lives in propertyiq_getdata/dbt/models/staging/ --
-- this model only materialises the foreign table locally so the app's RLS
-- policy can attach (policies cannot be put on foreign tables) and record-level
-- queries stay local. Column list is explicit so a new upstream column is a
-- deliberate change here, not a silent one.
select
    rent_id,
    rent_date,
    rent_year,
    rent_month,
    postcode,
    property_type_code,
    property_type,
    bedrooms,
    bedroom_band,
    weekly_rent
from {{ source('propertyiq_staging', 'property_rent') }}
