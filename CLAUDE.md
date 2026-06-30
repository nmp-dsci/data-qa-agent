# CLAUDE.md — data-qa-agent

Greenfield project: an app that serves data science insights through question and answer.
Starting fresh on the `init-ds-app` branch. Architecture and stack are not yet decided.

## Quick reference

- **Package manager:** `uv` — use `uv sync`, `uv add`, `uv run`
- **Linting/formatting:** Ruff — `uv run ruff format . && uv run ruff check . --fix`
- **Type checking:** `uv run mypy` (strict)
- **Tests:** `uv run pytest -q`
- **Secrets:** store in `.env`, never commit (see `.env.example`)
