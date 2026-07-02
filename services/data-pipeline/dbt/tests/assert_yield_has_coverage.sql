-- Singular test: mart_property_yield must cover enough postcodes to answer a
-- "best suburbs for rental yield" style question. See
-- assert_sales_growth_has_coverage for why 10 is the floor.
select count(distinct postcode) as postcode_count
from {{ ref('mart_property_yield') }}
having count(distinct postcode) < 10
