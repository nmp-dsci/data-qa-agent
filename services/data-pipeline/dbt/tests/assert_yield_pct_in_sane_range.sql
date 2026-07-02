-- Singular test: gross_yield_pct must be positive (guaranteed by positive
-- rent/price upstream) and below a generous circuit-breaker ceiling. Checked
-- against real full-data output: observed range is 0.74% to 35.66% across
-- 10k+ postcode/type/year rows (avg 3.6%, in line with typical AU residential
-- yields). 200% would only happen from a broken join or a rent/price unit
-- mismatch (e.g. weekly vs annual), not real market volatility.
select postcode, property_type, year, gross_yield_pct
from {{ ref('mart_property_yield') }}
where gross_yield_pct <= 0 or gross_yield_pct > 200
