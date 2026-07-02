-- Singular test: (postcode, suburb, property_type, month) must be unique in
-- mart_property_yield. suburb comes from the sales side (rent has no locality),
-- so it's part of the grain. Returns 0 rows on pass (dbt convention).
select postcode, suburb, property_type, month, count(*)
from {{ ref('mart_property_yield') }}
group by postcode, suburb, property_type, month
having count(*) > 1
