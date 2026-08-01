FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

COPY pyproject.toml ./
RUN uv sync --no-dev

COPY mcp_server ./mcp_server

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8200
CMD ["python", "-m", "mcp_server.main"]
