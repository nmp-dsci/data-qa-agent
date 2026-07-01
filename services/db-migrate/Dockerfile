# DB migration/seed job image for Azure Postgres.
# Build from the REPO ROOT so it can pull db/init + data:
#   docker build -f services/db-migrate/Dockerfile -t <acr>/db-migrate:<tag> .
FROM postgres:16-alpine

WORKDIR /app
COPY db/init/01_schema.sql db/init/02_rls.sql db/init/03_seed.sql ./
COPY services/db-migrate/load_marts.sql ./
COPY services/db-migrate/entrypoint.sh ./
COPY data/incoming/housing.csv ./housing.csv

RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
