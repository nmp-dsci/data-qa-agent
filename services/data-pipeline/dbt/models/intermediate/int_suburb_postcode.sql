-- Map each suburb to its dominant postcode (most sales). Used to attach a suburb
-- to the postcode-keyed rent data so both marts share a `suburb` join key.
select suburb, postcode
from (
    select
        suburb,
        postcode,
        row_number() over (partition by suburb order by count(*) desc, postcode) as rn
    from {{ ref('stg_sales') }}
    group by suburb, postcode
) ranked
where rn = 1
