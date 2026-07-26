FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Install dependencies first for layer caching. --extra ops adds boto3, so a
# deployment can enable the Tier-2 CloudWatch saturation pull (s32 W2) with an
# env var + IAM grant instead of a new image.
COPY pyproject.toml ./
RUN uv sync --no-dev --extra ops

COPY app ./app

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
