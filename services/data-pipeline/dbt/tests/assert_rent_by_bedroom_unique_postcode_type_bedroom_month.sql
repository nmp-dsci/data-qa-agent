-- Singular test: (postcode, property_type, bedroom_band, month) must be unique
-- in mart_rent_by_bedroom. bedroom_band is part of the grain (there is no 'ALL'
-- bedroom row). Returns 0 rows on pass (dbt convention).
select postcode, property_type, bedroom_band, month, count(*)
from {{ ref('mart_rent_by_bedroom') }}
group by postcode, property_type, bedroom_band, month
having count(*) > 1
