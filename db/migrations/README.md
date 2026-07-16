# DB Migrations

Migrations are managed by **Alembic**, run by the `db-migrate` job in
[`services/db-migrate/`](../../services/db-migrate/). The same `alembic upgrade head`
runs locally (the `migrate` compose service) and in Azure (the Container Apps job) — there is no
Postgres auto-init anymore.

Migrations run as a **privileged connection** (`ADMIN_DATABASE_URL`) so they can create extensions, roles,
and schemas, and so tables are owned by the admin role — which is what makes RLS apply to the non-owner
`app_user` / `agent_ro` roles.

## Current revisions

- `0001_phase0_init` — baseline. Applies the canonical DDL in [`db/init/`](.) (`01_schema.sql`,
  `02_rls.sql`, `03_seed.sql`): app schemas, roles, tables, helper functions, RLS policies, and seed
  users/datasets. Housing **data** is loaded separately by `seed_data.py` (it is pipeline output, not schema).

Later revisions live in `services/db-migrate/migrations/versions/` (one file per change; each docstring
explains it), currently through `0024_backfill_removed_templates`. The eval loop (s14–s18) added
`0019`–`0024`: golden-answer columns + `golden_objects` on `eval_cases`, the `agent_versions` registry,
the `eval_runs`/`eval_results` score store, and the column-only page-template re-seed + backfill.

The `db/init/*.sql` files remain the single source of truth for the baseline schema; the `0001` revision
executes them, and they are baked into the migration image.

## Adding a migration

Author a new revision under `services/db-migrate/migrations/versions/` (e.g. `0002_*.py`) using `op.*` or
`op.execute(...)`. Run it with `make migrate` (local) or let the deploy job apply it. Prefer additive,
reversible changes.
