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
- **Deploy (AWS):** merge to `main` runs `.github/workflows/deploy-aws.yml` — see `infra/terraform/README.md`
- **Architecture:** `AGENTS.md` + `.lavish/s00_data-qa-agent-architecture.html`

## Delegating to subagents

Don't do everything in the main thread. When a piece of work is separable, delegate it via the
Agent tool and pick the model/effort to match the task rather than defaulting to the main session's
setting:

- **Mechanical / low-risk** (lint fixes, formatting, renames, straightforward test-file scaffolding,
  fetching/summarizing a known file or command output) — delegate to a fast, cheap model
  (Haiku, or Sonnet at low effort). Don't burn Opus-tier reasoning on it.
- **Standard implementation** (a scoped feature, a bug fix with a clear repro, routine refactors) —
  Sonnet at medium/default effort is the right tier. This is most day-to-day work in this repo.
- **Architecture, security review, cross-cutting changes** (RLS/auth, the sandbox/egress boundary,
  the agent's system prompt or skill contracts, anything touching `units.py`/`units.ts` sync, schema
  migrations, or a design with real tradeoffs) — use Opus at high/xhigh effort, or spawn a
  `code-reviewer`-style agent for an independent second pass before merging.
- **Research/fan-out** (open-ended "where does X happen", multi-file surveys, triaging an unfamiliar
  failure) — fork or spawn a general-purpose agent so the raw search noise doesn't fill the main
  thread's context; report back a synthesis, not the transcript.

When spawning, set the Agent tool's `model` (and, for Workflow's `agent()`, `effort`) explicitly per
the guidance above instead of omitting it — the point is to actively choose the tier per task, not to
silently inherit the session default.
