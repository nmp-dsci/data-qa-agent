-- Median weekly rent per postcode per year (years with enough volume to trust).
select
    postcode,
    rent_year as year,
    percentile_cont(0.5) within group (order by weekly_rent) as median_rent,
    count(*) as n
from {{ ref('stg_rent') }}
group by postcode, rent_year
having count(*) >= {{ var('min_rent_year') }}
