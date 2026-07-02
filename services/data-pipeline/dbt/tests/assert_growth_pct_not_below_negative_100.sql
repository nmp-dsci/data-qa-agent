-- Singular test: a growth percentage can never be < -100 (a price/rent
-- cannot fall by more than its full value). This is mathematically guaranteed
-- by the positive price/rent floors in stg_sales/stg_rent, so a failing row
-- here means the growth calc or a join upstream has broken, not a real
-- market move. Checked against real full-data output: observed range is
-- -94.4% to +437.7% (sales) and -26.7% to +164.7% (rent).
select 'sales' as mart, postcode, property_type, sales_growth_pct as growth_pct
from {{ ref('mart_sales_growth') }}
where sales_growth_pct < -100
union all
select 'rent', postcode, property_type, rent_growth_pct
from {{ ref('mart_rent_growth') }}
where rent_growth_pct < -100
