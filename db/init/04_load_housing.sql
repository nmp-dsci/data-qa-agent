-- data-qa-agent :: load the housing CSV (Phase 0 stand-in for the dlt + dbt
-- pipeline). data/incoming is mounted read-only at /data in the db container.
-- Real pipeline (dlt ingest -> dbt build) arrives in Phase 2b.

COPY raw.housing (id, suburb, property_type, price, bedrooms, bathrooms,
                  car_spaces, land_size_sqm, year_built, sale_date)
FROM '/data/incoming/housing.csv' WITH (FORMAT csv, HEADER true);

INSERT INTO marts.housing (id, dataset_id, suburb, property_type, price, bedrooms,
                           bathrooms, car_spaces, land_size_sqm, year_built, sale_date)
SELECT r.id, d.id, r.suburb, r.property_type, r.price, r.bedrooms, r.bathrooms,
       r.car_spaces, r.land_size_sqm, r.year_built, r.sale_date
FROM raw.housing r
CROSS JOIN app.datasets d
WHERE d.slug = 'housing';

UPDATE app.datasets
SET row_count = (SELECT count(*) FROM marts.housing)
WHERE slug = 'housing';

INSERT INTO app.schema_migrations (version)
VALUES ('0001_phase0_init')
ON CONFLICT (version) DO NOTHING;
