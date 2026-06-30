# data-qa-agent

An app that serves data science insights through question and answer.

> Blank slate — starting fresh on the `init-ds-app` branch.
> The previous S&P 500 ELT pipeline lives on `main`.

## Status

Greenfield. Architecture and stack to be designed.

## Tooling

- **Package manager:** `uv` — `uv sync`, `uv add`, `uv run`
- **Lint/format:** Ruff — `uv run ruff format . && uv run ruff check . --fix`
- **Type check:** `uv run mypy`
- **Tests:** `uv run pytest -q`
