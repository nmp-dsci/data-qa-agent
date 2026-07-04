-- Singular test: mart_rent_by_bedroom must break rent out across a real spread
-- of bedroom bands (not collapse to a single band from a broken derivation).
-- Floor of 3 is comfortably below both the sample fixture (4 bands: 0-3) and
-- full data (7: 0-4, 5+, unknown) — same "floor below both fixtures" rationale
-- as assert_sales_summary_has_coverage. Returns a row (failing) if too thin.
select count(distinct bedroom_band) as band_count
from {{ ref('mart_rent_by_bedroom') }}
having count(distinct bedroom_band) < 3
