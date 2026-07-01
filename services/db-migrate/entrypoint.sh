#!/bin/sh
# Cloud DB migration + seed for Azure Postgres (Phase 0 stand-in for Alembic).
# Applies schema + RLS + seed, rotates the app role passwords to the Key Vault
# values, and loads the housing data. Fresh installs run in one transaction;
# already-applied installs still rotate role passwords.
set -eu

: "${ADMIN_DATABASE_URL:?ADMIN_DATABASE_URL is required}"
: "${APP_USER_PW:?APP_USER_PW is required}"
: "${AGENT_RO_PW:?AGENT_RO_PW is required}"
export PGSSLMODE="${PGSSLMODE:-prefer}"

DB="$ADMIN_DATABASE_URL"

has_migrations=$(psql "$DB" -tAc "SELECT to_regclass('app.schema_migrations')" 2>/dev/null || true)
if [ "$has_migrations" = "app.schema_migrations" ]; then
  applied=$(psql "$DB" -tAc "SELECT EXISTS (SELECT 1 FROM app.schema_migrations WHERE version = '0001_phase0_init')" 2>/dev/null || echo "f")
  if [ "$applied" = "t" ]; then
    echo "Schema version 0001_phase0_init already applied — rotating role passwords only."
    psql "$DB" -v ON_ERROR_STOP=1 -q -v apw="$APP_USER_PW" -v gpw="$AGENT_RO_PW" <<'SQL'
ALTER ROLE app_user PASSWORD :'apw';
ALTER ROLE agent_ro PASSWORD :'gpw';
SQL
    exit 0
  fi
fi

echo "==> Applying schema version 0001_phase0_init + RLS + seed + data (single transaction)"
{
  printf 'BEGIN;\n'
  cat /app/01_schema.sql /app/02_rls.sql /app/03_seed.sql
  printf "ALTER ROLE app_user PASSWORD :'apw';\n"
  printf "ALTER ROLE agent_ro PASSWORD :'gpw';\n"
  printf '%s\n' "\copy raw.housing(id,suburb,property_type,price,bedrooms,bathrooms,car_spaces,land_size_sqm,year_built,sale_date) FROM '/app/housing.csv' CSV HEADER"
  cat /app/load_marts.sql
  printf 'COMMIT;\n'
} | psql "$DB" -v ON_ERROR_STOP=1 -q -v apw="$APP_USER_PW" -v gpw="$AGENT_RO_PW"

echo "==> Migration complete."
