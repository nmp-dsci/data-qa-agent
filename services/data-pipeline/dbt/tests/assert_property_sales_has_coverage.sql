-- Singular test: marts.property_sales must cover enough postcodes to support
-- postcode/suburb-level analysis. Returns a row (failing) if too thin.
select count(distinct postcode) as postcode_count
from {{ ref('mart_property_sales') }}
having count(distinct postcode) < 10
