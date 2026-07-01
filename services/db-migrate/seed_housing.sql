-- Build marts.housing from the freshly-\copied raw.housing and update the
-- dataset registry row count. Runs after the CSV load in seed_data.py.
-- (This is a Phase-0 stand-in for the dlt + dbt pipeline arriving in Phase 2b.)

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
