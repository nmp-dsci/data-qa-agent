# Data QA Agent

A data agent that answers stakeholder questions about business data, surfaces
insights, and can turn recurring questions into scheduled reports.

> **Status:** Scaffolding. We are standing up skeletons for all four pipeline
> stages, then deepening each. Expect parts of the layout below to be aspirational
> until the matching code lands.

---

## Pipeline

1. **Ingest & transform** — raw files (CSV / Excel / JSON) → Postgres, modeled with dbt.
2. **Agent** — a LangGraph Q&A agent that answers user questions over the warehouse.
3. **Web app** — user login (Supabase Auth) and a chat UI for asking questions.
4. **Observability** — full run tracing and monitoring via LangSmith.

---

## Tech stack

| Concern         | Choice                                  |
|-----------------|-----------------------------------------|
| Language        | Python 3.10+                            |
| Package manager | `uv` (never raw `pip`)                  |
| Lint / format   | Ruff                                    |
| Type checking   | mypy (strict)                          |
| Tests           | pytest                                  |
| Warehouse       | Postgres (local, via Docker for dev)    |
| Transform       | dbt                                     |
| Agent framework | LangGraph                               |
| LLM provider    | DeepSeek (`DEEPSEEK_API_KEY`)           |
| Auth            | Supabase Auth                           |
| Observability   | LangSmith                               |
| Web app         | **TODO** — framework not yet chosen     |

---

## Repo layout

```
data-qa-agent/
├── src/data_qa_agent/
│   ├── ingest/        # load raw CSV/Excel/JSON into Postgres staging
│   ├── agent/         # LangGraph graph, nodes, tools, prompts
│   ├── db/            # connection, queries, schema helpers
│   └── config.py      # settings / env loading
├── dbt/               # dbt project: models, seeds, tests
├── web/               # web app (stack TBD) — UI + auth
├── data/raw/          # sample/raw input files (gitignored if large)
├── tests/             # pytest: test_*.py
├── docker-compose.yml # local Postgres
└── pyproject.toml
```

Put new Python modules under `src/data_qa_agent/` in the submodule matching their
pipeline stage. dbt models go in `dbt/`, web code in `web/`.

---

## Common commands

```bash
uv sync                                   # install deps
uv run ruff format . && uv run ruff check . --fix   # format + lint
uv run mypy src                           # type check
uv run pytest -q                          # run tests

docker compose up -d                      # start local Postgres
cd dbt && uv run dbt run                  # build models
cd dbt && uv run dbt test                 # dbt data tests
```

*(Commands above assume the corresponding scaffolding exists; add them as each
stage lands.)*

---

## Environment variables

Store in `.env` at the project root — **never commit**.

```
DEEPSEEK_API_KEY=...        # LLM provider
DATABASE_URL=...            # local Postgres connection string
LANGSMITH_API_KEY=...       # tracing
LANGSMITH_PROJECT=data-qa-agent
SUPABASE_URL=...            # auth
SUPABASE_ANON_KEY=...
```

---

## Conventions

- snake_case for files/functions, CamelCase for classes.
- Never hardcode secrets — read from `os.environ` / a settings loader.
- Tests in `tests/` named `test_*.py`; run without API keys where possible (use
  dry-run / mocked LLM and DB fixtures).
- Conventional-style commits (`feat:`, `fix:`, `chore:`).

---

## Open decisions (TODO)

- **Web framework** for step 3 (FastAPI+React vs. Next.js vs. Streamlit).
- Whether scheduled reports run via cron, a worker, or LangGraph's scheduling.
