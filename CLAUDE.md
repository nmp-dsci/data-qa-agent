# CLAUDE.md — data-qa-agent

> 📖 **Read [`AGENTS.md`](./AGENTS.md) first** — it is the source of truth for this project: architecture,
> the data agent, security model (auth + RLS), data model, conventions, and the phased build plan.
> To **run the app**, see [`README.md`](./README.md) (quick start, ports, project structure, troubleshooting).
> This file is just a pointer plus the quick reference.

An app that automates data science through a conversational data agent: users log in and ask questions in
natural language; an AI agent turns them into governed SQL over data they're authorized to see. A working
Phase 0 local slice is built (3 services + Postgres) — `make up`, then open http://localhost:5230.

## Quick reference

- **Run locally:** `make up` (see `README.md`) · **E2E test:** `make smoke`
- **Package manager:** `uv` — `uv sync`, `uv add`, `uv run`
- **Linting/formatting:** Ruff — `uv run ruff format . && uv run ruff check . --fix`
- **Type checking:** `uv run mypy` (strict)
- **Tests:** `uv run pytest -q`
- **Secrets:** store in `.env`, never commit (see `.env.example`)
- **Architecture:** `AGENTS.md` + `.lavish/data-qa-agent-architecture.html`
