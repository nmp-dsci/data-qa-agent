# DB migration/seed job image (Alembic). Build from the REPO ROOT so it can pull
# the canonical schema SQL (db/init) and the sample data:
#   docker build -f services/db-migrate/Dockerfile -t <acr>/db-migrate:<tag> .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install dependencies first for layer caching.
COPY services/db-migrate/pyproject.toml ./
RUN uv sync --no-dev

# Alembic project + seed logic.
COPY services/db-migrate/alembic.ini ./
COPY services/db-migrate/migrations ./migrations
COPY services/db-migrate/seed_data.py ./
COPY services/db-migrate/entrypoint.sh ./

# Canonical schema DDL applied by the 0001 baseline revision.
COPY db/init/01_schema.sql db/init/02_rls.sql db/init/03_seed.sql ./migrations/sql/

ENV PATH="/app/.venv/bin:$PATH"
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
