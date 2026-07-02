#!/bin/sh
# DB migration job (local dev and Azure). Applies Alembic migrations (schema +
# RLS + seed) and, when APP_USER_PW/AGENT_RO_PW are set, rotates the role
# passwords. Idempotent — safe to re-run. Dataset data is loaded by the pipeline.
set -eu

: "${ADMIN_DATABASE_URL:?ADMIN_DATABASE_URL is required}"

echo "==> alembic upgrade head"
alembic upgrade head

echo "==> rotating role passwords (if provided)"
python seed_data.py

echo "==> Migration complete."
