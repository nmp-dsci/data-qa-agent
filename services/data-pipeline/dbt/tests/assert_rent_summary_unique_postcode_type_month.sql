-- Singular test: (postcode, property_type, month) must be unique in
-- mart_rent_summary. Returns 0 rows on pass (dbt convention).
select postcode, property_type, month, count(*)
from {{ ref('mart_rent_summary') }}
group by postcode, property_type, month
having count(*) > 1
