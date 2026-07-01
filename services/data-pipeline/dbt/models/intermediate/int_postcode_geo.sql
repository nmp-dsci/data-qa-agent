-- Dominant suburb name per postcode (most sales), used as a friendly label.
-- Per docs/property_data/property_yield_20241003.py, postcode — not suburb — is
-- the correct join key between sales and rent (rent has no suburb at all, and
-- postcode<->suburb is not 1:1 in this data: many postcodes span >1 suburb and
-- vice versa). Suburb is attached to marts as a label only, never as the join key.
select postcode, suburb
from (
    select
        postcode,
        suburb,
        row_number() over (partition by postcode order by count(*) desc, suburb) as rn
    from {{ ref('stg_sales') }}
    group by postcode, suburb
) ranked
where rn = 1
