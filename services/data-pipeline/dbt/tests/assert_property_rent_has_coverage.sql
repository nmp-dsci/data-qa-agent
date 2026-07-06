-- Singular test: marts.property_rent must cover enough postcodes to support
-- postcode-level rent analysis. Returns a row (failing) if too thin.
select count(distinct postcode) as postcode_count
from {{ ref('mart_property_rent') }}
having count(distinct postcode) < 10
