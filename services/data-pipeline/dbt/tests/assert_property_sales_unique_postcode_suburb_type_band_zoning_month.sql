-- Grain check for marts.property_sales. Returns 0 rows on pass.
select postcode, suburb, property_type, area_band, zoning, month, count(*) as n
from {{ ref('mart_property_sales') }}
group by postcode, suburb, property_type, area_band, zoning, month
having count(*) > 1
