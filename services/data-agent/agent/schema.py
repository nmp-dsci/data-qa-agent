from __future__ import annotations

# In Phase 2b this is read from the dbt manifest/catalog. For Phase 0 it is a
# hand-written description of the one mart the agent answers over.
SCHEMA_DOC = """\
Table marts.housing — residential property sales (one row per sale).
Columns:
  suburb (text)        — suburb name, e.g. Fitzroy, Carlton, Northcote
  property_type (text) — House, Townhouse, Apartment, Unit
  price (integer)      — sale price in AUD
  bedrooms (integer)
  bathrooms (integer)
  car_spaces (integer)
  land_size_sqm (integer)
  year_built (integer)
  sale_date (date)
Rules: SELECT only. Row-Level Security limits rows to datasets the user may access.
"""

SUBURBS = [
    "Fitzroy",
    "Carlton",
    "Brunswick",
    "Richmond",
    "St Kilda",
    "Footscray",
    "Coburg",
    "Preston",
    "Northcote",
    "Yarraville",
]
PROPERTY_TYPES = ["House", "Townhouse", "Apartment", "Unit"]
