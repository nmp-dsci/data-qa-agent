# data-qa-agent

An app that automates data science through a conversational **data agent**: users log in, ask questions in
natural language, and an AI agent turns them into governed SQL over data they're authorized to see — then
answers with the result.

- 📐 **Design & architecture:** [`AGENTS.md`](./AGENTS.md) (source of truth) and the visual review at
  `.lavish/s00_data-qa-agent-architecture.html`.
- 🤖 **For AI assistants:** [`CLAUDE.md`](./CLAUDE.md) → points here and to `AGENTS.md`.

## Quick start (fully local, no cloud)

Requires Docker. One command boots the whole stack — Postgres+pgvector, the Alembic migration job, the
dlt+dbt pipeline, backend-api, data-agent, frontend:

```bash
make up       # build + start everything (migrate + pipeline run first, then the services start)
```

On startup two one-shot jobs run in order: **`migrate`** (`alembic upgrade head` — schema + RLS + seed) then
**`pipeline`** (dlt ingests the CSVs into `raw`, dbt builds the growth marts). By default the pipeline uses the
small committed **sample** so `make up` is fast; load the full real datasets with `make pipeline-full`.

Then open **http://localhost:5230** and sign in as a test user:

| User   | Role  | Sees |
|--------|-------|------|
| admin  | admin | all data |
| user1  | user  | the `nsw_sales` + `nsw_rent` datasets |
| user2  | user  | **nothing** — demonstrates row-level isolation |

Ask e.g. *"What are the top growth suburbs for sale price and rent?"* — the agent JOINs the sales and rent
growth marts on `suburb`. Sign in as `user2` and ask the same thing: you'll get zero rows, because Row-Level
Security isolates them.

### The data

Two real NSW datasets (place the CSVs in `data/`, they are gitignored — too big to commit):

- `data/nswgov_df.csv` — NSW Government property **sales** (~516 MB) → `marts.mart_sales_growth`
- `data/rentboard_df.csv` — NSW Rental Bond Board **rent** (~63 MB) → `marts.mart_rent_growth`

Small committed **samples** live in `data/samples/` (regenerate from the full files with `make samples`); they
keep `make up` and CI fast while preserving suburbs present in both datasets across the growth window.

```bash
make smoke    # end-to-end test: login -> ask -> response, query audit, and RLS isolation
uv run pytest -q  # unit tests (guardrails/NL->SQL) + journey evals (skip if stack down)
make logs     # tail service logs
make down     # stop the stack
make reset    # stop AND wipe the db volume (re-seeds + reloads on next `make up`)
```

## Architecture at a glance

Three services + one database, matching the locked design (see `AGENTS.md` for the full picture):

```
   data-pipeline (dlt + dbt)  ──build──►  marts.*
        (raw → staging → marts)               ▲
frontend (React+Vite)  →  backend-api (FastAPI)  →  data-agent (NL→SQL / DeepSeek)
      :5230                     :8000                     :8100
                                   │                         │
                                   └──────► Postgres + pgvector (RLS) ◄──────┘
                                                  :5434
```

- **frontend** — login + chat UI, fires product-analytics events, includes an admin dashboard.
- **backend-api** — validates the JWT, sets the per-request RLS context, orchestrates the agent, records
  conversations/messages/events.
- **data-pipeline** — dlt ingests the CSVs into `raw`; dbt transforms `raw → staging → marts` (tests + docs),
  building the two suburb-keyed growth marts with RLS applied by post-hooks.
- **data-agent** — turns the question into a single read-only `SELECT` (JOINing the marts on `suburb` for the
  combined view), runs it under RLS, phrases the answer, optionally renders a chart. Offline stub by default;
  DeepSeek (or Claude) when a key is set, grounded in the dbt manifest, personalized by pgvector memory, traced
  with Logfire (Decision G).
- **Postgres** — one DB, schemas `app` / `raw` / `staging` / `marts`; RLS enforces who sees which rows.

### How a question flows

1. Sign in → backend issues a signed JWT (dev-auth stub locally; Microsoft Entra in production).
2. Frontend calls `POST /ask` with the bearer token.
3. Backend sets `app.current_user_id` on the DB session → **RLS scopes every query to that user**.
4. Backend delegates to the data-agent, which runs a governed `SELECT` (read-only role, SELECT-only
   allowlist, single statement, row cap) — still under RLS.
5. The answer + generated SQL + rows stream back to the chat UI.

## Ports

| Service | URL | Notes |
|---------|-----|-------|
| Frontend | http://localhost:5230 | React + Vite dev server |
| Backend API | http://localhost:8000 | `/health`, `/auth/config`, `/auth/dev-login`, `/me`, `/ask`, `/events`, `/admin/*` |
| Data agent | http://localhost:8100 | `/health`, `/agent/ask` |
| Postgres | `localhost:5434` | user `postgres` / `postgres`, db `dataqa` (5432/5433 were in use) |

## Project structure

```
services/backend-api/   FastAPI: dev-auth, RLS context, /ask, /events, admin endpoints
services/data-agent/    NL→SQL stub + pluggable LLM path; read-only SQL under RLS with guardrails
services/data-pipeline/ dlt ingestion + dbt project (staging → marts, tests, RLS post-hooks)
services/db-migrate/    Alembic migrations (the `migrate` job; runs local + cloud)
frontend/               React + Vite: login (dev stub or MSAL) + chat + event tracking
db/init/                canonical schema/RLS/seed SQL applied by the 0001 Alembic baseline
config/                 datasets.yaml (registry), users.seed.yaml (dev users)
data/                   full NSW CSVs (gitignored) + data/samples/ (small committed samples)
evals/                  journeys.yaml — user-journey evals (grows every phase)
scripts/                make_samples.py, smoke_test.py
docker-compose.yml      the local dev stack;  Makefile has the shortcuts
```

## Data pipeline (dlt + dbt)

`services/data-pipeline/` is the `pipeline` job. `run.py` runs **dlt** (CSV → `raw.sales` / `raw.rent`) then
`dbt build` over `services/data-pipeline/dbt/`:

- `stg_sales` / `stg_rent` clean the raw rows; `int_*` models compute per-year medians and a
  suburb→dominant-postcode map.
- `mart_sales_growth` and `mart_rent_growth` are **one row per `suburb`** so the agent can JOIN them; each is
  scoped to its dataset (`nsw_sales` / `nsw_rent`) by an RLS **post-hook**.
- `dbt docs generate` writes the manifest the agent reads (`get_schema()`), grounding the LLM in the real marts.

Run on the sample with `make pipeline`, on the full data with `make pipeline-full`. dbt tests run as part of
`dbt build` — structural (`not_null`, uniqueness in `dbt/tests/assert_*_unique_*.sql`) and use-case sanity
checks (`dbt/tests/assert_*_has_coverage.sql`, `assert_growth_pct_*`, `assert_yield_pct_*`) that assert each
mart actually has enough postcodes and sane values to answer the questions it's meant for — a build fails if a
mart can't support its use case, not just if it's malformed.

**Reviewing raw → staging → marts:** run `make pipeline` (or `-full`) then `make pipeline-docs` to serve the
dbt docs UI at http://localhost:8180 — lineage graph, every model's SQL, and column descriptions (the same
text `get_schema()` feeds the agent) for `raw` sources through `staging`/intermediate to `marts`. To inspect
actual rows/counts at any layer, connect to Postgres directly (`localhost:5434`, schemas `raw`/`staging`/`marts`
— see Ports below).

## Admin Dashboard

Sign in as `admin` and use the **Admin** button to inspect the live events feed, users, datasets, and audited
agent query runs. Each answered question writes a `query_runs` row with the user, dataset, SQL, row count,
latency, and engine.

## Authentication (dev stub → Microsoft Entra External ID)

Auth runs in one of two modes, chosen at runtime — the frontend reads `GET /auth/config` and adapts, so
**flipping to real auth needs no rebuild**:

- **`dev` (default)** — a local dev-auth stub. The login screen shows the three seeded users; the backend
  mints a signed HS256 token. Everything runs offline.
- **`entra`** — real **Microsoft Entra External ID** (OIDC). The frontend signs in via MSAL
  (`@azure/msal-browser`); the backend validates the RS256 token against the tenant's public **JWKS**
  (no client secret needed), then **just-in-time provisions** the user into `app.users` keyed by their Entra
  `oid`, so RLS and the admin role stay driven by our own database. An app role (default value `admin`) in the
  token maps to the admin role.

To switch, set `AUTH_MODE=entra` and the `ENTRA_*` values in `.env` (see `.env.example`), then restart
backend-api. A real Entra External ID tenant + two app registrations (SPA + API) are required for live login;
until then, `dev` mode is the working local experience.

The `/me` endpoint returns the current user's profile in both modes.

## Using the real LLM agent instead of the offline stub

The agent answers **offline by default** via a deterministic NL→SQL stub, so the demo runs with no API key.
To use the real agent, put a provider key in `.env` (see `.env.example`) and rebuild data-agent — `make up`
already installs the data-agent's `llm` extra. The provider sits behind an abstraction (Decision G), selected
by `LLM_PROVIDER`:

- **`deepseek` (default)** — set `DEEPSEEK_API_KEY`.
- **`anthropic`** — set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`.

With a real provider, the agent also gets a `make_chart` tool (renders a Vega-Lite chart in the chat UI when
useful) and per-user memory: it recalls relevant past preferences (pgvector cosine search over
`app.user_memories`, RLS-scoped — a user's memory is isolated like their data) at the start of every question,
and calls `remember` when you state an explicit preference (e.g. "I only care about units, not houses").

Every agent run is traced with **Logfire** — tool calls, model requests, and (with `capture_all=True`) the raw
HTTP payloads sent to the provider. Set `LOGFIRE_TOKEN` in `.env` to ship traces to Logfire Cloud; leave it
empty to trace locally with no extra configuration.

## Troubleshooting

- **Port already in use** — the dev DB uses host port **5434** (5432/5433 were taken by other local
  containers). If 5230/8000/8100 clash, change the left-hand side of the `ports:` mapping in
  `docker-compose.yml`.
- **Empty marts / no data** — the `pipeline` job builds the marts. Re-run it with `make pipeline` (sample) or
  `make pipeline-full` (real data), or `make reset` then `make up` for a clean slate (wipes the volume so
  migrations + pipeline re-run).
- **Frontend can't reach the API** — CORS allows `http://localhost:5230`; if you change the frontend port,
  update `cors_origins` in `services/backend-api/app/config.py` and rebuild backend-api.

## Deploy to Azure (dev)

Infra-as-code (Bicep) + a GitHub Actions deploy are scaffolded under [`infra/`](./infra/README.md).
`dev` is the same logical environment whether it runs locally or in Azure — only *where config comes from*
changes (`.env` locally vs Key Vault in the cloud). `staging`/`prod` are the same template with a different
`env`. See `infra/README.md` for prerequisites (OIDC, GitHub vars/secrets) and the two-phase deploy. The
`db-migrate` Container Apps job runs the same Alembic migrations as local (`alembic upgrade head`).

## Tooling & conventions

- **Package manager:** `uv` · **Lint/format:** Ruff · **Types:** mypy (strict) · **Tests:** pytest + smoke
- **Secrets:** `.env` (never committed) locally; Key Vault in Azure. See `AGENTS.md` for the full conventions.
