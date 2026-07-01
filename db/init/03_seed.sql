-- data-qa-agent :: seed users, dataset registry, and access grants.
-- Mirrors config/users.seed.yaml and config/datasets.yaml. Runs as superuser
-- (bypasses RLS), so inserts are unrestricted here.

INSERT INTO app.users (username, email, display_name, role) VALUES
    ('admin', 'admin@example.com', 'Admin User', 'admin'),
    ('user1', 'user1@example.com', 'User One',  'user'),
    ('user2', 'user2@example.com', 'User Two',  'user');

INSERT INTO app.datasets (slug, name, description, status) VALUES
    ('housing', 'Housing sales',
     'Residential property sales in inner-Melbourne suburbs: price, bedrooms, bathrooms, car spaces, land size, property type, year built and sale date.',
     'ready');

-- Grant housing access to user1 only (admin sees all via role; user2 excluded).
INSERT INTO app.dataset_access (dataset_id, user_id, access)
SELECT d.id, u.id, 'read'
FROM app.datasets d, app.users u
WHERE d.slug = 'housing' AND u.username = 'user1';
