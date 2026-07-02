-- Singular test: mart_rent_growth must cover enough postcodes to answer a
-- "biggest rent increases" style question. See assert_sales_growth_has_coverage
-- for why 10 is the floor.
select count(distinct postcode) as postcode_count
from {{ ref('mart_rent_growth') }}
having count(distinct postcode) < 10
