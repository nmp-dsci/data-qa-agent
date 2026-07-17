-- Singular test: marts.property_yield must cover enough postcodes to support
-- postcode-level yield analysis. The join + minimum-volume floor thins the data,
-- so the threshold is lower than the single-source marts. Returns a row
-- (failing) if too thin.
select count(distinct postcode) as postcode_count
from {{ ref('mart_property_yield') }}
having count(distinct postcode) < 5
