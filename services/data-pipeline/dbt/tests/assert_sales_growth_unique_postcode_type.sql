-- Singular test: (postcode, property_type) must be unique in mart_sales_growth.
-- Returns 0 rows on pass (dbt convention).
select postcode, property_type, count(*)
from {{ ref('mart_sales_growth') }}
group by postcode, property_type
having count(*) > 1
