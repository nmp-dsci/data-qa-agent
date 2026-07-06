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

# Node runs the hardened Pyodide/WASM sandbox (restructure Phase B). bookworm
# ships Node 18.20, which satisfies pyodide's engines (>=18). npm pulls in the
# host's only dependency (pyodide) below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/*

COPY agent ./agent
# The Insight Playbook the agent greps at runtime (search_knowledge/
# read_knowledge). Without it, knowledge_version() is "none" and every
# search returns "No knowledge pages are available" — the agent silently
# loses all its domain/analysis/presentation guidance.
COPY knowledge ./knowledge

# Install the Pyodide host deps and warm the pandas/numpy wheel cache into
# node_modules so run_analysis runs fully offline (the container blocks network
# at run time). Same pattern as the fastembed pre-fetch above; the warmup is
# non-fatal so a transient CDN hiccup degrades to a slow first sandbox run.
RUN cd agent/sandbox \
    && npm ci --omit=dev \
    && (node warmup.mjs || echo "[build] pyodide warmup skipped; first run will fetch wheels")

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8100
CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8100"]
