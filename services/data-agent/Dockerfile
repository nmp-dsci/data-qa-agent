FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync --no-dev --extra llm

# Pre-fetch the local embedding model so it's baked into the image instead of
# downloaded on first request. Calls the venv's python directly (not `uv run`,
# which would implicitly re-sync and pull the dev dependency group back in).
# Non-fatal: a transient network hiccup here should degrade to "slow first
# request", not a failed build.
RUN /app/.venv/bin/python -c \
    "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')" \
    || true

COPY agent ./agent

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8100
CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8100"]
