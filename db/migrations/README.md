# DB Migrations

Phase 0 still uses the Postgres entrypoint SQL files in `db/init/` for local bootstrapping, but the schema now
records applied versions in `app.schema_migrations`.

Current version:

- `0001_phase0_init` — app schemas, roles, RLS policies, seed users, housing load, and SQL audit tables.

Follow-up migrations should be additive SQL files or Alembic revisions that insert their version into
`app.schema_migrations` in the same transaction as the schema change.
