
**Read [`AGENTS.md`](AGENTS.md) before working in this project.** It covers project layout, architecture, common commands, environment variables, evaluation outputs, and testing.

## Quick reference

- **Package manager:** `uv` — use `uv sync`, `uv add`, `uv run`
- **Linting/formatting:** Ruff — `uv run ruff format . && uv run ruff check . --fix`
- **Tests:** `uv run pytest -q` (no API keys needed — tests use dry-run / mocked mode)
- **Pipeline:** raw files → Postgres (dbt) → LangGraph Q&A agent → web app → LangSmith tracing
- **API keys:** `DEEPSEEK_API_KEY` (LLM), plus `DATABASE_URL`, `LANGSMITH_API_KEY`, `SUPABASE_*` — store in `.env`, never commit
