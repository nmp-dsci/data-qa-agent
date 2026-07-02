-- Singular test: gross yield, computed the way the agent computes it —
-- (median_rent * 52 / median_price) * 100 — must be positive and below a
-- generous circuit-breaker ceiling. mart_property_yield stores no yield column
-- (the agent computes it at query time), but this verifies the building blocks
-- produce a sane ratio. A breach at this scale means a broken join or a
-- rent/price unit mismatch (e.g. weekly vs annual), not real market volatility.
--
-- Gated on adequate coverage (n_sold >= 5 AND n_rented >= 5): the marts now
-- keep every bucket including tiny ones (no locality is dropped for being
-- small — see mart_sales_summary.sql), and a thin bucket — e.g. a single
-- sale at the $10k non-market price floor against normal rent — legitimately
-- produces an implausible yield without any bug. That's expected thin-data
-- noise the caller filters via n_sold/n_rented, not a pipeline fault, so the
-- circuit breaker only asserts sanity where there's enough data to trust the
-- ratio. Checked against real full-data output: the well-covered range is well
-- within 0-200%.
select
    postcode,
    suburb,
    property_type,
    month,
    n_sold,
    n_rented,
    round((median_rent * 52 / median_price * 100)::numeric, 2) as gross_yield_pct
from {{ ref('mart_property_yield') }}
where median_price > 0
  and n_sold >= 5
  and n_rented >= 5
  and (
    (median_rent * 52 / median_price * 100) <= 0
    or (median_rent * 52 / median_price * 100) > 200
  )
