-- Singular test: mart_sales_by_segment must cover enough postcodes to answer a
-- "price by zone / lot size" style question, not just be non-empty. See
-- assert_sales_summary_has_coverage for why 10 is the floor. Returns a row
-- (failing) if coverage is too thin.
select count(distinct postcode) as postcode_count
from {{ ref('mart_sales_by_segment') }}
having count(distinct postcode) < 10
