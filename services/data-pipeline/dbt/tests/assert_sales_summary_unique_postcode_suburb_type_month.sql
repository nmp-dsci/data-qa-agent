-- Singular test: (postcode, suburb, property_type, month) must be unique in
-- mart_sales_summary. suburb is part of the grain now (postcode <-> suburb is
-- not 1:1), so it must be in the key. Returns 0 rows on pass (dbt convention).
select postcode, suburb, property_type, month, count(*)
from {{ ref('mart_sales_summary') }}
group by postcode, suburb, property_type, month
having count(*) > 1
