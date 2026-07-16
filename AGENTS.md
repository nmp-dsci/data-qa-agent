# AGENTS.md — data-qa-agent

Comprehensive guide for AI assistants and developers working in this repo. `CLAUDE.md` points here;
this file is the source of truth for architecture, conventions, and workflow.
**To run the app** (quick start, ports, project structure, troubleshooting) see [`README.md`](./README.md).

---

## What this project is

An end-to-end web app that **automates data science through a conversational data agent**. Users log in,
ask questions in natural language, and an AI agent turns them into governed SQL/analysis over data they're
authorized to see, returning insights and charts.

The goal is a **v1 end-to-end system that is built to evolve** — a learning-oriented, iteratively improved
reference implementation, not a throwaway prototype.

The product's UI display name is **Data Pilot**; the repo and services keep the `data-qa-agent` name.

Current branch: `init-ds-app`. The full architecture proposal lives in
`.lavish/s00_data-qa-agent-architecture.html` (open with `npx -y lavish-axi .lavish/s00_data-qa-agent-architecture.html`).

---

## Locked decisions (v1)

Confirmed via the Lavish architecture review — these drive the build:

| # | Decision | Choice |
|---|----------|--------|
| A | RLS visibility | **Isolation by default**; `admin` role sees across users |
| B | Compute | **Azure Container Apps** (serverless, scale-to-zero) — superseded in s12: shipped on **AWS App Runner** (see Phase 4 + Environments) |
| C | Service granularity | **3 services**: frontend, backend-api, data-agent |
| D | Identity | **Google Sign-in** (OIDC) — ID tokens verified server-side against Google's JWKS (s11) |
| E | Starting point | **Phase 0 local scaffold** first |
| F | Agent framework | **Pydantic AI** (native tool-calling loop) |
| G | Model provider | **Abstracted** — default Claude, DeepSeek as a config-swappable cost option |
| H | Agent memory | **Postgres + pgvector** now, isolated by RLS |
| I | Data pipeline | **dbt-core** (transforms) + **dlt** (CSV→Postgres ingestion) |

## Target architecture (v1)

Microservices on **Azure Container Apps**, private-by-default behind one ingress. Secrets and identity never
live in code. *(As deployed in s12 the same shape runs on AWS — App Runner, ECS jobs, Aurora Serverless v2,
Secrets Manager, S3+CloudFront — via `infra/terraform/`; this table remains the cloud-neutral design.)*

| Service | Tech | Azure resource | Owns |
|---------|------|----------------|------|
| **frontend** | React + Vite (TypeScript), Google Identity Services | Container App (or Static Web App) | Chat UI, sign-in, event tracking, admin dashboard, charts |
| **backend-api** | FastAPI, SQLAlchemy, asyncpg | Container App (internal ingress) | JWT validation, RLS context, `/ask` + `/events`, orchestration |
| **data-agent** | Pydantic AI, pluggable LLM, Logfire | Container App (no public ingress) | NL→SQL, analysis tools, memory, structured answers, guardrails |
| **data-pipeline** | dbt-core (Postgres) + dlt | Container Apps Job (scheduled/triggered) | CSV→raw ingest, raw→marts transforms, tests, docs/manifest the agent reads |
| **database** | PostgreSQL 16 + RLS + pgvector | PostgreSQL Flexible Server | Source of truth, per-user isolation, agent memory |
| **identity** | OIDC / OAuth2 | Google Sign-in (external IdP, cloud-neutral) | Login, MFA, token issuance |
| **secrets** | Managed Identity | Key Vault | DB creds, model API keys |
| **delivery** | Bicep + GitHub Actions | Container Registry | Build, push, deploy, IaC |

### Request flow (one question, end to end)

1. User signs in with Google → frontend receives a signed ID token (JWT).
2. Frontend calls `backend-api` with a `Bearer` token.
3. API verifies the token against Google's public JWKS and reads `sub` + verified email (the
   `ADMIN_EMAILS` allowlist decides the admin role).
4. API opens a transaction and sets the RLS context: `SET LOCAL app.current_user_id = '<sub>'`.
5. API delegates the question to `data-agent`.
6. Agent plans, calls `run_sql` (read-only, under RLS) and analysis tools, reasons over results with Claude.
7. Agent returns a typed answer + chart spec; API streams it to the frontend.
8. Every step is traced in Logfire.

---

### Run fully locally

**Built and working (Phase 0 slice + Phase 1 auth + Phase 2 migrations + Phase 2b pipeline + Phase 3 agent +
Phase 3b tracking/admin).**
`make up` boots
the whole app on `localhost` with no Azure: Postgres+pgvector, a one-shot **Alembic migration job**, the
**dlt+dbt pipeline job**, backend-api, data-agent, and frontend (see README for details). `migrate` runs
`alembic upgrade head` (schema + RLS + seed) then `pipeline` builds the growth marts from the committed sample;
the services wait for both. `make smoke` runs the end-to-end test (login → ask top growth suburbs → response,
SQL audit trail, RLS isolation of user2); `uv run pytest` also runs the `evals/journeys.yaml` suite.

- **Migrations (Phase 2):** Alembic is the single source of truth (`services/db-migrate/`). The same
  `alembic upgrade head` runs locally and as the Azure Container Apps job. Migrations run as a privileged
  connection so tables are owned by the admin role — which is what makes RLS apply to `app_user`/`agent_ro`.

- **Auth (Phase 1; real sign-in in s11):** two runtime-selected modes — `dev` (default) mints a signed HS256
  token for `admin`/`user1`/`user2`; `google` validates real **Google Sign-in** RS256 ID tokens against
  Google's public JWKS (`app/auth.py`) and just-in-time provisions users into `app.users` by their Google
  `sub`, with the `ADMIN_EMAILS` allowlist mapping emails to the admin role. The frontend reads
  `GET /auth/config` and renders the Google Identity Services button in `google` mode — flipping needs
  **no rebuild**, only `AUTH_MODE=google` + `GOOGLE_CLIENT_ID` + `ADMIN_EMAILS`. A Google OAuth **Web**
  client with the frontend origin authorized is needed for real login.
  Protected `/me` returns the current user in both modes.
- **Agent (Phase 3):** answers offline via a deterministic NL→SQL stub (`agent/nl2sql.py`) when no provider
  key is set; otherwise the real Pydantic AI agent (`agent/llm_agent.py`) runs — DeepSeek by default
  (`DEEPSEEK_API_KEY`), or Claude via `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` — provider is abstracted
  (Decision G, `agent/provider.py`). Tools: `run_sql`, `make_chart` (Vega-Lite), `remember`; recall is
  programmatic (pgvector cosine search over `app.user_memories`, RLS-scoped) seeded into the system prompt
  every turn. Traced with Logfire (`LOGFIRE_TOKEN` optional — local-only tracing without it).
- **Pipeline (Phase 2b):** the `pipeline` job (`services/data-pipeline/`) runs **dlt** (CSVs → `raw`) then
  **dbt build** (`raw → staging → marts`, tests + docs). The two datasets `nsw_sales` / `nsw_rent` build
  `marts.mart_sales_growth` / `marts.mart_rent_growth`, each one row per `suburb` and RLS-scoped by a dbt
  post-hook, so the agent JOINs them on `suburb` for the "top growth suburbs" view. Runs on the small committed
  sample by default (`data/samples/`); `make pipeline-full` loads the full CSVs (`data/*.csv`, gitignored).
  The agent reads the dbt manifest (`get_schema()`) to ground the LLM. Secrets come from `.env`, not Key Vault.
  dbt tests run as part of `build`: structural tests (`not_null`, uniqueness) plus use-case sanity tests
  (`dbt/tests/assert_*_has_coverage.sql`, `assert_growth_pct_*`, `assert_yield_pct_*`) that fail the pipeline if
  a mart can't actually support the question type it exists for (too few postcodes, growth/yield out of a sane
  range). Each mart's `_marts.yml` description states the question types it answers, verified by those tests —
  the same text `get_schema()` grounds the agent in, so agent capability and tested capability can't drift
  apart. Review raw → staging → marts with `make pipeline-docs` (dbt docs UI, lineage + column docs at
  `:8180`) or by querying Postgres directly (`raw`/`staging`/`marts` schemas).
- **Note:** the dev DB publishes host port **5434** (5432/5433 were taken by other local containers); internal
  networking still uses `db:5432`.

### Repo layout (as built)

```
services/backend-api/   FastAPI: dev-auth + Google ID-token validation, RLS context, /ask, /events, admin
services/data-agent/    NL→SQL stub + Claude path, read-only SQL under RLS with guardrails
services/data-pipeline/ dlt ingestion + dbt project (staging → marts, tests, RLS post-hooks)
services/db-migrate/    Alembic migrations (the `migrate` job; runs local + cloud)
frontend/               React + Vite: login (dev stub or Google Sign-in) + chat + event tracking
db/init/                canonical schema/RLS/seed SQL applied by the 0001 Alembic baseline
data/samples/           small committed NSW sample CSVs (full data is gitignored)
evals/                  journeys.yaml — user-journey evals (auth + RLS + growth; grows every phase)
db/init/                schema + RLS + roles + seed + housing load (run on first `make up`)
config/                 datasets.yaml, users.seed.yaml
data/incoming/          housing.csv (generate with scripts/generate_housing.py)
scripts/                generate_housing.py, smoke_test.py
```

### Environments

v1 runs entirely as **`dev`**, but everything is env-parameterized so `staging`/`prod` are added later without a
rewrite. `APP_ENV` selects config via pydantic-settings (`.env` now; `.env.staging`/`.env.prod` later). Azure
resources carry an env suffix (`dataqa-dev-*` → `dataqa-staging-*`/`dataqa-prod-*`) from one IaC module invoked
per env. **Databases are split per environment, never shared** — one Postgres server for `dev` now, separate
servers later; schemas stay identical within each. CI deploys `dev` on merge; promotion to staging/prod comes
later. Note: the dbt `staging` *schema* is a data-modeling layer, unrelated to a `staging` *deployment env*.

**dev local vs dev cloud** are the *same* environment (`APP_ENV=dev`), not different env values — the
difference is the **deployment target** and where config is sourced: `.env`/compose locally vs service env
vars + a secrets store in the cloud (`DB_SSL=require`, secrets by reference). The **live deployment is AWS**
(s12): Terraform in [`infra/terraform/`](./infra/terraform/README.md) provisions App Runner services, ECS
one-shot jobs (migrate/pipeline), Aurora Serverless v2, Secrets Manager, and the S3+CloudFront frontend;
`.github/workflows/deploy-aws.yml` is the push-button deploy on merge to `main`. The Azure Bicep scaffold
in [`infra/`](./infra/README.md) stays as a reference.

### Platform notes (portability)

- **One Postgres** Flexible Server / one database with schemas `app` · `raw` · `staging` · `marts`;
  `agent_memory` is a `pgvector` table in that same DB, not a separate server.
- **One image per service** — v1 is 3 service images + 1 pipeline job image.
- **Cloud portability:** app code is portable; use **Terraform** (not Bicep) if you may move clouds.
  Container Apps ↔ Cloud Run ↔ App Runner/Fargate; Flexible Server ↔ Cloud SQL ↔ RDS; Key Vault ↔
  Secret Manager ↔ Secrets Manager. Identity (Google Sign-in) is an external IdP, already cloud-neutral.
- **LLM portability** comes from the model abstraction (Decision G), not a cloud LLM service. Reach Claude via
  the direct Anthropic API (cloud-neutral), Bedrock, or Vertex. Avoid coupling to Azure AI Foundry.
- **DB tools:** in-process typed tools (asyncpg/SQLAlchemy) for v1; MCP Postgres only later if the tool must
  be shared out-of-process.
- **API-first / multi-surface:** backend-api + data-agent are headless (`/ask` behind a JWT), so future
  surfaces (Android via native Google Sign-in OIDC+PKCE, a Slack bot via Bolt) are new clients reusing the same agent,
  RLS, and memory. The only per-surface work is mapping that surface's identity to a `users` row.

## Security model — three stacked layers

1. **AuthN** — Google issues an ID token (JWT; the dev stub mints one locally); the API verifies
   signature/claims on every request.
2. **AuthZ** — FastAPI dependencies gate endpoints by role (e.g. `analyst` vs `admin`).
3. **Row-Level Security** — Postgres policies filter rows by `app.current_user_id`, enforced by the database
   itself so isolation holds even if app code has a bug.

```sql
ALTER TABLE insights ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON insights
  USING (owner_id = current_setting('app.current_user_id')::uuid);
```

- Set the session variable with `SET LOCAL` **inside the request transaction** so pooled connections never
  leak context between users.
- The **agent connects as a read-only DB role** and stays under the same RLS — it can never see or write rows
  the user couldn't.
- In the cloud (s12) the agent's App Runner URL is public with the backend as its only intended caller: when
  `AGENT_SHARED_TOKEN` is set, agent middleware rejects any request (except `/health`) without a matching
  `X-Agent-Token` header; the backend sends it on every agent call. Empty = open (local compose).
- Role-level `statement_timeout`s (migration 0018: `app_user`/`agent_ro` 15s, `admin_ro` 30s) are a
  database-side backstop against runaway queries on every code path, independent of app-level guards.

---

## The data agent

Pydantic AI agent that receives the question + schema, plans, and calls tools.

**Tools (v1):** `get_schema()`, `run_sql(query)` (read-only, row-capped), `make_chart(spec)`
(model supplies mark/encoding only; `data.values` is spliced in server-side from the `run_sql`
result — the model can't fabricate chart numbers), `remember(fact)` (writes to `user_memories`).
`recall` is programmatic rather than a tool — the agent's system prompt is seeded with the
current user's relevant memories (pgvector cosine search, distance-thresholded) before every run,
so personalization doesn't depend on the model remembering to call a tool. `profile_column(table,
col)` is **deferred** — not yet implemented.

**Guardrails (non-negotiable):** read-only DB role · RLS always applies · `SELECT`-only allowlist with parse
validation and statement timeout · token + row caps to bound cost and blast radius.

A second, deliberately isolated micro-agent lives beside it: `agent/titles.py` (`POST /agent/title`)
summarises a conversation's first question into a 3–5 word sidebar title. The backend calls it best-effort
from a background task after the first answer (and `app/backfill_titles.py` reuses it to retitle old
conversations), so titling can never slow down or break answering; without a provider key it falls back to
an offline heuristic.

When building or changing the agent, consult the `ai:building-pydantic-ai-agents` skill and instrument with
the `logfire` skills.

---

## Data model (Postgres)

All capabilities live in one Postgres, all under RLS.

| Table | Group | Purpose | RLS |
|-------|-------|---------|-----|
| `users` | Identity | Local mirror of signed-in users (Google or dev-seeded) + role (`admin`/`user`) | self; admin sees all |
| `datasets` | Datasets | Registry of ingested datasets the agent can answer over | readable if access granted |
| `dataset_access` | Datasets | Which users/roles may query which dataset | self; admin manages |
| `conversations` | Q&A | A user's chat sessions | owner; admin sees all |
| `messages` | Q&A | Turns: question, answer, generated SQL, tokens, latency | via conversation owner |
| `query_runs` | Q&A | Audit of every SQL the agent executed (guardrail trail) | via owner; admin audits |
| `user_memories` | Memory | Learned per-user preferences + `pgvector` embedding | owner only |
| `events` | Analytics | Frontend + backend event stream for the admin dashboard | insert own; admin reads all |
| `eval_cases` | Evals | Golden answers — feedback-promoted or hand-authored stages (`golden_sql`, `golden_sandbox`, `golden_objects`, `golden_report`) | admin/CI-curated; no RLS |
| `agent_versions` | Evals | Fingerprint of the agent build (provider, model, prompt/skills hashes); stamps every `query_runs` row | admin/CI-curated; no RLS |
| `eval_runs` / `eval_results` | Evals | Batch grading: one row per pack run + per-case pillar scores (G1–G4), linked back to `query_runs` | admin/CI-curated; no RLS |
| `marts.*` (e.g. `housing`) | Domain | dbt-built, documented tables questions run against | via `dataset_access` |

## Datasets, config & the CSV drop-folder

File-driven so datasets are added by editing config, not code:

```
data/incoming/         # drop CSVs here (e.g. housing.csv)
config/datasets.yaml   # dataset registry: slug, csv path, description, access
config/users.seed.yaml # dev seed users: admin, user1, user2
evals/journeys.yaml    # user-journey tests
```

Flow: pipeline reads `datasets.yaml` → dlt ingests each CSV → `raw` → dbt → `marts`; a row is upserted into
`datasets`, and `access` populates `dataset_access` so RLS enforces who can query it. App config
(DB URL, model keys, provider) is one typed **pydantic-settings** `Settings` object reading `.env` locally
and Key Vault in Azure. Seed users: `admin` (sees all), `user1` (housing access), `user2` (no housing access —
demonstrates isolation).

## Product analytics & admin dashboard

Frontend fires an event at each journey step → `POST /events` → `events` table. Event types:
`login_screen_view`, `login_success`/`login_failure`, `home_view`, `question_submitted`, `agent_started`,
`agent_answered`/`agent_error`. An **admin-only** dashboard (role-gated) shows a live events feed (filterable
by event type and user), the users table (role, last active — derived from `MAX(events.created_at)`), the
datasets table (row counts, access — count of `dataset_access` grants), and Q&A/agent metrics from
`query_runs` (latency, row counts, generated SQL, input/output token counts from the LLM path's
`run.usage()` — null for the offline stub). Same stream feeds Logfire.

## Evaluation & user-journey tests

`evals/journeys.yaml` defines journeys (`as_user`, `question`, `expect`) that a **pytest** harness runs against
the real `/ask` flow, scored with **pydantic_evals**. Journeys double as RLS isolation tests (user2 must never
see user1's rows). Extend by adding YAML; runs in CI and blocks deploy on failure.

The **eval loop** (s14–s18) builds on this with **golden examples**: the admin-only **Golden Examples** tab
authors a golden answer stage by stage — ① SQL extract, ② sandbox analysis (named presentation objects built
from the tested skill library), ③ the presentation report — starting from an agent-drafted first pass.
Goldens are stored on `app.eval_cases` (CRUD via the backend's `/admin/eval-goldens` endpoints, which proxy
draft/build actions to the data-agent's `/agent/analysis*` and `/agent/skills*` helpers; the object-type
picker is generated from the report-engine registry so it can't drift from what the renderer supports).
Deterministic graders (`agent/eval_graders.py`) score G1 extraction values, G2 sandbox metrics, and the
structural half of G3 presentation against a `ready` golden — the LLM insight half of G3 is a judge, not
code. Every `/ask` is stamped with an `agent_versions` build fingerprint, and batch scores land in
`eval_runs`/`eval_results` (migrations 0019–0024).

---

## Conventions

### Python (backend-api, data-agent)

- Use `uv` for all dependency operations: `uv sync`, `uv add`, `uv run` — never raw `pip`.
- Format + lint with Ruff: `uv run ruff format . && uv run ruff check . --fix`.
- Type-check with `uv run mypy` (strict).
- Tests in `tests/` as `test_*.py`; run `uv run pytest -q`.
- `snake_case` for files/functions, `CamelCase` for classes.
- Never hardcode secrets — read from environment / Key Vault, e.g. `os.environ["ANTHROPIC_API_KEY"]`.

### TypeScript (frontend)

- ESM modules, TypeScript throughout.
- Keep auth/token logic in a dedicated module; components stay presentational.
- Styling is hand-rolled CSS with design tokens in `frontend/src/styles.css` — **no CSS framework or component
  library** (no Tailwind). Fonts are self-hosted via `@fontsource`.

### Secrets & config

- Local: `.env` (never commit — see `.env.example`).
- Azure: Key Vault via Managed Identity, injected as Container App secrets.

### Models

- Default to the latest capable Claude models for the agent (`claude-opus-4-8`), with a cheaper fallback
  (`claude-sonnet-4-6`) for routine queries. See the `claude-api` skill before changing model config.

---

## Build plan (iterative, local-first)

| Phase | Deliverable | Status |
|-------|-------------|--------|
| **0 · Scaffold** | uv monorepo (api + agent), FastAPI hello, React Vite, Postgres via docker-compose — all on localhost | ✅ done |
| **1 · Auth** | Google ID-token validation + JIT provisioning, Google Sign-in (dev stub fallback), protected `/me`, `/auth/config`, journey evals; 3 seeded users | ✅ done (real Google Sign-in shipped in s11) |
| **2 · Data + RLS** | Schema + Alembic migrations (all tables above), RLS policies, session-variable middleware, isolation tests | ✅ done |
| **2b · Pipeline** | dlt CSV→raw; dbt raw→staging→marts with tests/docs; suburb-keyed growth marts; `datasets`/`dataset_access` populated | ✅ done |
| **3 · Agent** | Pydantic AI agent, read-only role, `run_sql`/`make_chart`/`recall`/`remember`, pgvector memory, Logfire, streaming `/ask` | ✅ done (DeepSeek default, Claude via `LLM_PROVIDER=anthropic`, pgvector memory, Logfire; streaming `/ask` deferred — HTTP contract stays request/response, Logfire gives step tracing instead) |
| **3b · Tracking + admin** | Event taxonomy + `POST /events`, `events` table, admin-only dashboard (feed, users, datasets, metrics) | ✅ done |
| **4 · Cloud** | Bicep: Container Apps env + job, ACR, PostgreSQL Flexible (+pgvector), Key Vault, managed identity | ✅ done — shipped on **AWS** instead (s12, `infra/terraform/`): App Runner + ECS jobs, Aurora Serverless v2, ECR, Secrets Manager, S3+CloudFront frontend; the Azure Bicep stays a reference |
| **5 · CI/CD** | GitHub Actions: build/push, Ruff/mypy/pytest + **journey evals** (pydantic_evals), deploy on merge to `main` | ✅ done (`ci.yml` PR gate; `deploy-aws.yml` push-button deploy on merge via OIDC + cloud smoke test) |
| **6 · Harden** | Front Door + WAF, rate limits, statement timeouts, LLM cost guards, dashboards | ⏳ partial (s12 cheap hardening: role-level statement timeouts, tiered daily ask caps, agent shared token, CloudWatch billing/5xx alarms; WAF + custom domain deferred) |

Evaluation (`evals/journeys.yaml`) is introduced in Phase 1 and grows every phase. Each phase ships something
runnable. Pause after each so the changes can be learned before extending.

---

## Known risks to keep in mind

- **NL→SQL safety** — mitigated by read-only role + RLS + `SELECT`-only allowlist + timeouts + row caps;
  worth a dedicated test pass.
- **LLM cost drift** — mitigated (s12): tiered per-user daily caps on LLM-backed calls (`/ask`, `/ask/stream`
  and the SQL editor's AI assist share one counter — `app/limits.py`): free 5/day, paid 10/day, admins
  uncapped, 0 = off; disabled in local compose. A cheap-model fallback remains available via Decision G.
- **RLS + pooling** — always `SET LOCAL` per transaction (see security model).

---

## Working in this repo

- This subdirectory is its own git repo. Commit/push only when asked; branch off `main` first.
- The parent `/git` workspace has its own `CLAUDE.md` — this file takes precedence for anything in
  `data-qa-agent/`.
