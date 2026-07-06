-- Grain check for marts.property_rent. Returns 0 rows on pass.
select postcode, property_type, bedroom_band, month, count(*) as n
from {{ ref('mart_property_rent') }}
group by postcode, property_type, bedroom_band, month
having count(*) > 1
