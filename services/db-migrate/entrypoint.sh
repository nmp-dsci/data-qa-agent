#!/bin/sh
# DB migration + seed job (local dev and Azure). Applies Alembic migrations
# (schema/RLS/seed) then loads the housing data and, when APP_USER_PW/AGENT_RO_PW
# are set, rotates the role passwords. Idempotent — safe to re-run: already-applied
# revisions are skipped and the data load no-ops when marts.housing is populated.
set -eu

: "${ADMIN_DATABASE_URL:?ADMIN_DATABASE_URL is required}"

echo "==> alembic upgrade head"
alembic upgrade head

echo "==> seeding data + rotating role passwords"
python seed_data.py

echo "==> Migration + seed complete."
