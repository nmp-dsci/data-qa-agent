{{
  config(
    materialized='table',
    tags=['agent_queryable'],
    post_hook="{{ apply_dataset_rls('nsw_sales') }}"
  )
}}

-- Postcode <-> suburb bridge: EVERY (postcode, suburb) pair seen in the sales
-- records, with how many sales carry that pairing. This deliberately does NOT
-- collapse to one dominant suburb per postcode (the old behaviour) — postcode
-- <-> suburb is not 1:1 (postcode 2076 alone spans Wahroonga, Normanhurst and
-- North Wahroonga), and picking only the dominant suburb silently deleted the
-- others: a query for "Normanhurst" found nothing because the whole postcode
-- was relabelled "Wahroonga". Locality is a real dimension in the source, so
-- every value survives here.
--
-- The sales/rent marts no longer join this to borrow a single label — sales
-- carries its true suburb from the record grain. This bridge stays as the
-- resolver for the one case that can't: rent. raw.property_rent has no locality column
-- at all, so "rent in Normanhurst" is answered by resolving the suburb to its
-- postcode(s) here, then querying rent by postcode. RLS-scoped to nsw_sales
-- (it's derived from sales).
--
-- n_sales lets a consumer still identify the dominant suburb when it wants one
-- (order by n_sales desc), without that choice being baked in and lossy here.
select
    postcode,
    suburb,
    count(*) as n_sales
from {{ ref('stg_sales') }}
group by postcode, suburb
