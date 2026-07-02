-- Singular test: mart_sales_summary must cover enough postcodes to answer a
-- "top growth suburbs" / "10-year growth" style question, not just be
-- non-empty. Returns a row (failing the test) if coverage is too thin. 10 is
-- a floor comfortably below both the sample fixture (~24 suburbs/25 postcodes
-- by design, see scripts/make_samples.py) and full data (500+) — high enough
-- to catch a broken join or filter collapsing the mart to a handful of rows.
select count(distinct postcode) as postcode_count
from {{ ref('mart_sales_summary') }}
having count(distinct postcode) < 10
