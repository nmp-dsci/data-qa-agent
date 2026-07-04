-- Singular test: (postcode, suburb, property_type, area_band, zoning, month)
-- must be unique in mart_sales_by_segment. area_band and zoning are part of the
-- grain (there is no 'ALL' area_band/zoning row). Returns 0 rows on pass.
select postcode, suburb, property_type, area_band, zoning, month, count(*)
from {{ ref('mart_sales_by_segment') }}
group by postcode, suburb, property_type, area_band, zoning, month
having count(*) > 1
