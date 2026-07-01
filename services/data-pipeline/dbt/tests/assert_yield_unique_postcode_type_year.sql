-- Singular test: (postcode, property_type, year) must be unique in mart_property_yield.
-- Returns 0 rows on pass (dbt convention).
select postcode, property_type, year, count(*)
from {{ ref('mart_property_yield') }}
group by postcode, property_type, year
having count(*) > 1
