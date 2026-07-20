# data-qa-agent

An app that automates data science through a conversational **data agent**: users log in, ask questions in
natural language, and an AI agent turns them into governed SQL over data they're authorized to see — then
answers with the result.

The product is branded **Data Pilot** in the UI; the repository and services keep the `data-qa-agent` name.

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

Ask e.g. *"What are the top growth suburbs for sale price and rent?"* — the agent derives growth from the
sales and rent aggregate marts. Sign in as `user2` and ask the same thing: you'll get zero rows, because Row-Level
Security isolates them.

### The data

Two real NSW datasets (place the CSVs in `data/`, they are gitignored — too big to commit):

- `data/nswgov_df.csv` — NSW Government property **sales** (~516 MB) → `marts.property_sales`
- `data/rentboard_df.csv` — NSW Rental Bond Board **rent** (~63 MB) → `marts.property_rent`

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

- **frontend** — login + chat UI (responsive desktop/mobile layout, light/dark/system theme toggle), fires
  product-analytics events, includes an admin dashboard and an admin-only Golden Examples authoring page.
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

1. Sign in → the client holds a signed JWT (minted by the local dev-auth stub, or a Google-issued ID token
   with real Google Sign-in).
2. Frontend calls `POST /ask` with the bearer token.
3. Backend sets `app.current_user_id` on the DB session → **RLS scopes every query to that user**.
4. Backend delegates to the data-agent, which runs a governed `SELECT` (read-only role, SELECT-only
   allowlist, single statement, row cap) — still under RLS.
5. The answer + generated SQL + rows stream back to the chat UI.

## Ports

| Service | URL | Notes |
|---------|-----|-------|
| Frontend | http://localhost:5230 | React + Vite dev server |
| Backend API | http://localhost:8000 | `/health`, `/auth/config`, `/auth/dev-login`, `/me`, `/ask`, `/events`, `/admin/*`, `/explore/*` |
| Data agent | http://localhost:8100 | `/health`, `/agent/config`, `/agent/ask(/stream)`, `/agent/sql(/assist)`, `/agent/title`, `/agent/analysis*`, `/agent/skills*`, `/agent/schema` |
| Postgres | `localhost:5434` | user `postgres` / `postgres`, db `dataqa` (5432/5433 were in use) |

## Project structure

```
services/backend-api/   FastAPI: dev-auth, RLS context, /ask, /events, admin + /admin/eval-goldens endpoints
services/data-agent/    NL→SQL stub + pluggable LLM path; read-only SQL under RLS with guardrails; eval graders
services/data-pipeline/ dlt ingestion + dbt project (staging → marts, tests, RLS post-hooks)
services/db-migrate/    Alembic migrations (the `migrate` job; runs local + cloud)
frontend/               React + Vite: login (dev stub or Google Sign-in) + chat + Explore + golden authoring + event tracking
db/init/                canonical schema/RLS/seed SQL applied by the 0001 Alembic baseline
config/                 datasets.yaml (registry), users.seed.yaml (dev users)
data/                   full NSW CSVs (gitignored) + data/samples/ (small committed samples)
evals/                  journeys.yaml — user-journey evals (grows every phase)
scripts/                make_samples.py, smoke_test.py, build_poa_paths.py (Explore choropleth paths,
                        see scripts/build_topojson.md), explore_parity.py + AWS deploy scripts
                        (aws_build_push, run_job, deploy_frontend, cloud_smoke)
docs/chronicle/         vendored legacy NSW profiling tool, kept as the Explore reference (see its README)
infra/terraform/        AWS deployment (live) — see infra/terraform/README.md; infra/ Bicep = Azure reference
docker-compose.yml      the local dev stack;  Makefile has the shortcuts
```

## Data pipeline (dlt + dbt)

`services/data-pipeline/` is the `pipeline` job. `run.py` runs **dlt** (CSV → `raw.property_sales` / `raw.property_rent`) then
`dbt build` over `services/data-pipeline/dbt/`:

- `staging.property_sales` / `staging.property_rent` clean the raw rows; `int_postcode_geo` keeps the
  suburb↔postcode bridge for rent lookups.
- `marts.property_sales` and `marts.property_rent` are the two aggregate marts, one per staging table. They
  keep cleaned attributes plus additive metrics so the agent can re-aggregate and derive growth/yield later.
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

## Explore

The **Explore** tab lets any signed-in user browse the property/postcode marts directly — filter, aggregate,
and profile cohorts — without going through the chat agent. It covers three governed datasets: `nsw_sales`,
`nsw_rent`, and `nsw_yield` (gross rental yield, sales joined to rent by postcode/property_type/month — its
own dataset since it needs both grants). A NSW postcode choropleth renders from a pre-built paths file
(`frontend/public/geo/poa_nsw.paths.json`, regenerated with `scripts/build_poa_paths.py` — see
`scripts/build_topojson.md`) rather than a runtime geo-projection library. Reads run under the same RLS as
everywhere else and are audited into `query_runs` with `source = 'explore'`, so caps and audit trails cover
it too. Every chart, in Explore or chat, deep-links to the SQL editor with the query that produced it.

Explore's UI is a modern port of a legacy static NSW profiling tool, vendored for reference at
`docs/chronicle/` (see its README to run it or restore its gitignored data files).

## Admin Dashboard

Sign in as `admin` and use the **Admin** button to inspect the live events feed, users, datasets, and audited
agent query runs. Each answered question writes a `query_runs` row with the user, dataset, SQL, row count,
latency, and engine.

## Golden Examples (the eval loop)

Admins also get a **Golden Examples** tab for authoring *golden answers* — the 100/100 benchmarks the eval
loop scores the agent against — stage by stage (① SQL extract → ② sandbox analysis objects, built from the
tested skill library → ③ presentation report), starting from an agent-drafted first pass. Goldens live on
`app.eval_cases` (CRUD under `/admin/eval-goldens`; the backend proxies draft/build actions to the
data-agent's `/agent/analysis*` and `/agent/skills*` helpers). Deterministic graders
(`services/data-agent/agent/eval_graders.py`) compare a run's extracted values, sandbox metrics, and report
shape against a `ready` golden; every `/ask` is stamped with an `agent_versions` build fingerprint, and
batch scores land in `eval_runs`/`eval_results`.

A chat answer can skip straight to a draft golden: admins see a **"★ save as golden"** chip on any answered
chat result, which copies the already-captured question/SQL/sandbox script/report into a new draft (no
agent re-run) and opens it in the editor. Inside the editor, the primary way to add a stage-② object is the
**"New object with AI"** panel — describe it in one sentence and it's built and placed onto the report
automatically (the structured form is still there as a manual fallback). Ordinal columns like `area_band`
and `bedroom_band` render in their natural order rather than alphabetically; curators can tweak the order
per dataset from a data-knowledge panel in the Sandbox tab (backed by `app.dataset_ordinals`), or override
one chart's x-axis order manually in the report editor.

## Authentication (dev stub → Google Sign-in)

Auth runs in one of two modes, chosen at runtime — the frontend reads `GET /auth/config` and adapts, so
**flipping to real auth needs no rebuild**:

- **`dev` (default)** — a local dev-auth stub. The login screen shows the three seeded users; the backend
  mints a signed HS256 token. Everything runs offline.
- **`google`** — real **Google Sign-in** (OIDC). The frontend renders the official Google Identity Services
  button, which hands back a Google-signed RS256 ID token; the backend validates it against Google's public
  **JWKS** (no client secret needed), then **just-in-time provisions** the user into `app.users` keyed by
  their Google `sub`, so RLS and the admin role stay driven by our own database. Emails listed in
  `ADMIN_EMAILS` map to the admin role; everyone else is a `user`.

To switch, set `AUTH_MODE=google`, `GOOGLE_CLIENT_ID`, and `ADMIN_EMAILS` in `.env` (see `.env.example`),
then restart backend-api. The OAuth client must be a **Web** client with the frontend origin
(http://localhost:5230 locally) as an authorized JavaScript origin; until then, `dev` mode is the working
local experience.

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

New conversations also get a short (3–5 word) sidebar title summarising the first question, generated by a
small title agent kept isolated on the data-agent (`POST /agent/title`) and called from a background task —
so titling never adds latency to the answer and can never break it. Without a provider key it falls back to
an offline heuristic. Retitle pre-existing conversations with
`docker compose exec backend-api python -m app.backfill_titles` (`--all` / `--dry-run`).

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

## Deploy to AWS (live)

The app is deployed to AWS (s12) with Terraform under [`infra/terraform/`](./infra/terraform/README.md):
App Runner runs backend-api + data-agent, ECS Fargate one-shot jobs run the same `migrate`/`pipeline`
images as local (the pipeline streams the full CSVs from S3), Aurora Serverless v2 (scale-to-zero) is the
database, and the frontend is a static Vite build in S3 behind CloudFront. Merging to `main` is the
push-button deploy — `.github/workflows/deploy-aws.yml` builds/pushes images, applies Terraform, runs
migrations, deploys the frontend, and smoke-tests the live URL (`scripts/cloud_smoke.sh`); auth is GitHub
OIDC, no stored keys. Cheap hardening ships with it: role-level statement timeouts (migration 0018), tiered
per-user daily AI caps (see below), and CloudWatch billing/5xx alarms → SNS email. See
`infra/terraform/README.md` for the runbook. The earlier Azure Bicep scaffold under
[`infra/`](./infra/README.md) stays as a reference and is not deployed.

### Daily AI usage caps

In the cloud, each user gets a daily budget of LLM-backed calls — `/ask`, `/ask/stream`, and the SQL
editor's AI assist share one counter (resets midnight UTC; exceeding it returns **429**): free **5/day**,
paid (plan `plus`/`pro`) **10/day**, admins uncapped. `ASK_DAILY_LIMIT_FREE` / `ASK_DAILY_LIMIT_PAID`
tune the limits (0 = off); the local compose stack sets both to 0 so repeated `make smoke` runs never 429.

## Tooling & conventions

- **Package manager:** `uv` · **Lint/format:** Ruff · **Types:** mypy (strict) · **Tests:** pytest + smoke
- **Secrets:** `.env` (never committed) locally; AWS Secrets Manager in the cloud. See `AGENTS.md` for the
  full conventions.
