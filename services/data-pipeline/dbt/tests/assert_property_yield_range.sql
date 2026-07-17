-- Singular test: every surviving yield cell must fall in a sane band. Australian
-- residential gross yields sit roughly 1%-12%; anything outside 0.3%-25% signals
-- a thin-cell artefact or a units/join bug (e.g. weekly rent read as annual, or a
-- nominal transfer that slipped the price floor). Returns the offending rows
-- (failing) if any land outside the band.
select postcode, property_type, year, gross_yield_pct
from {{ ref('mart_property_yield') }}
where gross_yield_pct < 0.3
   or gross_yield_pct > 25
