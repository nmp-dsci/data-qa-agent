{{
  config(
    materialized='table',
    alias='dim_postcode_geo',
    tags=['agent_queryable'],
    indexes=[
      {'columns': ['postcode'], 'unique': True},
    ],
    post_hook="{{ apply_dataset_rls_any(['nsw_sales', 'nsw_rent', 'nsw_yield']) }}"
  )
}}

-- Postcode -> ABS geography rollups (SA2 / SA3 / SA4 / Greater-Capital / state),
-- sourced from the committed postcode_geo seed (ABS 2016 postcode correspondences,
-- one row per postcode). This is the geo dimension the Explore profiler and the
-- agent roll a postcode up to a region by — the sales/rent/yield marts carry only
-- postcode, so "rent by SA3" joins through here.
--
-- Shared across every property dataset, so it uses apply_dataset_rls_any: readable
-- by a user holding ANY of the property grants, not all of them. One row per
-- postcode (unique), so joining it to a mart never fans out rows.
select
    postcode,
    sa2_name,
    sa3_name,
    sa4_name,
    gcc_name,
    state_name
from {{ ref('postcode_geo') }}
