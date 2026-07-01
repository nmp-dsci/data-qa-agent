-- Median sale price per suburb per year (years with enough volume to trust).
select
    suburb,
    sale_year as year,
    percentile_cont(0.5) within group (order by sale_price) as median_price,
    count(*) as n
from {{ ref('stg_sales') }}
group by suburb, sale_year
having count(*) >= {{ var('min_sales_year') }}
