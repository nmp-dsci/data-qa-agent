-- Cloud load step: raw.housing is populated by \copy (client-side) in the
-- entrypoint; this builds the marts table and updates the registry count.
-- (Local dev does the equivalent via db/init/04_load_housing.sql.)

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
