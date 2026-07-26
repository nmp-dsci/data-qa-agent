# Data pipeline image (dlt ingest + dbt build). Build from the REPO ROOT:
#   docker build -f services/data-pipeline/Dockerfile -t <acr>/data-pipeline:<tag> .
# Data is NOT baked in (the full CSVs are ~580MB) — it is mounted at /data.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install dependencies first for layer caching.
COPY services/data-pipeline/pyproject.toml ./
RUN uv sync --no-dev

COPY services/data-pipeline/ingest.py services/data-pipeline/run.py ./
COPY services/data-pipeline/dbt ./dbt
# The ops ingest writer (s32 W2): the pipeline records its own freshness so the
# /ops deck can show marts age without an AWS call. Dependency-free (urllib), and
# a no-op when OPS_INGEST_TOKEN is unset — which is the local default.
COPY scripts/ops_ingest.py ./scripts/ops_ingest.py

ENV PATH="/app/.venv/bin:$PATH"
ENV DATA_DIR=/data
CMD ["python", "run.py"]
