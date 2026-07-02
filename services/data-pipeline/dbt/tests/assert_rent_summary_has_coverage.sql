-- Singular test: mart_rent_summary must cover enough postcodes to answer a
-- "biggest rent increases" style question. See
-- assert_sales_summary_has_coverage for why 10 is the floor.
select count(distinct postcode) as postcode_count
from {{ ref('mart_rent_summary') }}
having count(distinct postcode) < 10
