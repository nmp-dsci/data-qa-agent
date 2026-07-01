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

Current branch: `init-ds-app`. The full architecture proposal lives in
`.lavish/data-qa-agent-architecture.html` (open with `npx -y lavish-axi .lavish/data-qa-agent-architecture.html`).

---

## Locked decisions (v1)

Confirmed via the Lavish architecture review — these drive the build:

| # | Decision | Choice |
|---|----------|--------|
| A | RLS visibility | **Isolation by default**; `admin` role sees across users |
| B | Compute | **Azure Container Apps** (serverless, scale-to-zero) |
| C | Service granularity | **3 services**: frontend, backend-api, data-agent |
| D | Identity | **Microsoft Entra External ID** (OIDC) |
| E | Starting point | **Phase 0 local scaffold** first |
| F | Agent framework | **Pydantic AI** (native tool-calling loop) |
| G | Model provider | **Abstracted** — default Claude, DeepSeek as a config-swappable cost option |
| H | Agent memory | **Postgres + pgvector** now, isolated by RLS |
| I | Data pipeline | **dbt-core** (transforms) + **dlt** (CSV→Postgres ingestion) |

## Target architecture (v1)

Microservices on **Azure Container Apps**, private-by-default behind one ingress. Secrets and identity never
live in code.

| Service | Tech | Azure resource | Owns |
|---------|------|----------------|------|
| **frontend** | React + Vite (TypeScript), MSAL | Container App (or Static Web App) | Chat UI, auth redirect, event tracking, admin dashboard, charts |
| **backend-api** | FastAPI, SQLAlchemy, asyncpg | Container App (internal ingress) | JWT validation, RLS context, `/ask` + `/events`, orchestration |
| **data-agent** | Pydantic AI, pluggable LLM, Logfire | Container App (no public ingress) | NL→SQL, analysis tools, memory, structured answers, guardrails |
| **data-pipeline** | dbt-core (Postgres) + dlt | Container Apps Job (scheduled/triggered) | CSV→raw ingest, raw→marts transforms, tests, docs/manifest the agent reads |
| **database** | PostgreSQL 16 + RLS + pgvector | PostgreSQL Flexible Server | Source of truth, per-user isolation, agent memory |
| **identity** | OIDC / OAuth2 | Microsoft Entra External ID | Login, MFA, token issuance |
| **secrets** | Managed Identity | Key Vault | DB creds, model API keys |
| **delivery** | Bicep + GitHub Actions | Container Registry | Build, push, deploy, IaC |

### Request flow (one question, end to end)

1. User logs in via Entra External ID → frontend receives a signed JWT.
2. Frontend calls `backend-api` with a `Bearer` token.
3. API verifies the token against Entra's JWKS and reads `sub` + roles.
4. API opens a transaction and sets the RLS context: `SET LOCAL app.current_user_id = '<sub>'`.
5. API delegates the question to `data-agent`.
6. Agent plans, calls `run_sql` (read-only, under RLS) and analysis tools, reasons over results with Claude.
7. Agent returns a typed answer + chart spec; API streams it to the frontend.
8. Every step is traced in Logfire.

---

### Run fully locally

**Built and working (Phase 0 slice + Phase 1 auth + Phase 2 migrations).** `make up` boots the whole app on
`localhost` with no Azure: Postgres+pgvector, a one-shot **Alembic migration job**, backend-api, data-agent,
and frontend (see README for details). The `migrate` service runs `alembic upgrade head` (schema + RLS + seed,
the `0001_phase0_init` baseline of `db/init/*.sql`) then loads `data/incoming/housing.csv`; the services wait
for it. `make smoke` runs the end-to-end test (login → ask → response, SQL audit trail, and RLS isolation of
user2); `uv run pytest` also runs the `evals/journeys.yaml` user-journey suite against a running stack.

- **Migrations (Phase 2):** Alembic is the single source of truth (`services/db-migrate/`). The same
  `alembic upgrade head` runs locally and as the Azure Container Apps job. Migrations run as a privileged
  connection so tables are owned by the admin role — which is what makes RLS apply to `app_user`/`agent_ro`.

- **Auth (Phase 1):** two runtime-selected modes — `dev` (default) mints a signed HS256 token for
  `admin`/`user1`/`user2`; `entra` validates real **Microsoft Entra External ID** RS256 tokens against the
  tenant JWKS (`app/auth.py`) and just-in-time provisions users into `app.users` by `oid`. The frontend reads
  `GET /auth/config` and uses MSAL (`@azure/msal-browser`) in `entra` mode — flipping needs **no rebuild**, only
  `AUTH_MODE=entra` + `ENTRA_*` config. A live tenant + SPA/API app registrations are needed for real login.
  Protected `/me` returns the current user in both modes.
- **Agent:** answers offline via a deterministic NL→SQL stub (`agent/nl2sql.py`); set `ANTHROPIC_API_KEY`
  (+ `--extra llm`) to use Claude (`agent/claude_agent.py`) — provider is abstracted (Decision G).
- **Pipeline:** the `migrate` job's `seed_data.py` loads the CSV into `raw.housing` and builds `marts.housing`
  (a Phase-0 stand-in). The real **dlt + dbt** pipeline is Phase 2b. Secrets come from `.env`, not Key Vault.
- **Note:** the dev DB publishes host port **5434** (5432/5433 were taken by other local containers); internal
  networking still uses `db:5432`.

### Repo layout (as built)

```
services/backend-api/   FastAPI: dev-auth + Entra JWT validation, RLS context, /ask, /events, admin
services/data-agent/    NL→SQL stub + Claude path, read-only SQL under RLS with guardrails
services/db-migrate/    Alembic migrations + data seed (the `migrate` job; runs local + cloud)
frontend/               React + Vite: login (dev stub or MSAL) + chat + event tracking
db/init/                canonical schema/RLS/seed SQL applied by the 0001 Alembic baseline
evals/                  journeys.yaml — user-journey evals (auth + RLS now; grows every phase)
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
difference is the **deployment target** and where config is sourced: `.env`/compose locally vs Container Apps
env + Key Vault in Azure (`DB_SSL=require`, secrets by reference, `AUTH_MODE=dev`→`entra` later). Infra-as-code
and the deploy workflow live in [`infra/`](./infra/README.md); see its README for the local↔cloud config map.

### Platform notes (portability)

- **One Postgres** Flexible Server / one database with schemas `app` · `raw` · `staging` · `marts`;
  `agent_memory` is a `pgvector` table in that same DB, not a separate server.
- **One image per service** — v1 is 3 service images + 1 pipeline job image.
- **Cloud portability:** app code is portable; use **Terraform** (not Bicep) if you may move clouds.
  Container Apps ↔ Cloud Run ↔ App Runner/Fargate; Flexible Server ↔ Cloud SQL ↔ RDS; Entra ↔ Cognito ↔
  Identity Platform; Key Vault ↔ Secret Manager ↔ Secrets Manager.
- **LLM portability** comes from the model abstraction (Decision G), not a cloud LLM service. Reach Claude via
  the direct Anthropic API (cloud-neutral), Bedrock, or Vertex. Avoid coupling to Azure AI Foundry.
- **DB tools:** in-process typed tools (asyncpg/SQLAlchemy) for v1; MCP Postgres only later if the tool must
  be shared out-of-process.
- **API-first / multi-surface:** backend-api + data-agent are headless (`/ask` behind a JWT), so future
  surfaces (Android via Entra native OIDC+PKCE, a Slack bot via Bolt) are new clients reusing the same agent,
  RLS, and memory. The only per-surface work is mapping that surface's identity to a `users` row.

## Security model — three stacked layers

1. **AuthN** — Entra issues a JWT; the API verifies signature/claims on every request.
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

---

## The data agent

Pydantic AI agent that receives the question + schema, plans, and calls tools.

**Tools (v1):** `get_schema()`, `run_sql(query)` (read-only, row-capped), `make_chart(spec)`,
`profile_column(table, col)`.

**Guardrails (non-negotiable):** read-only DB role · RLS always applies · `SELECT`-only allowlist with parse
validation and statement timeout · token + row caps to bound cost and blast radius.

When building or changing the agent, consult the `ai:building-pydantic-ai-agents` skill and instrument with
the `logfire` skills.

---

## Data model (Postgres)

All capabilities live in one Postgres, all under RLS.

| Table | Group | Purpose | RLS |
|-------|-------|---------|-----|
| `users` | Identity | Local mirror of Entra users + role (`admin`/`user`) | self; admin sees all |
| `datasets` | Datasets | Registry of ingested datasets the agent can answer over | readable if access granted |
| `dataset_access` | Datasets | Which users/roles may query which dataset | self; admin manages |
| `conversations` | Q&A | A user's chat sessions | owner; admin sees all |
| `messages` | Q&A | Turns: question, answer, generated SQL, tokens, latency | via conversation owner |
| `query_runs` | Q&A | Audit of every SQL the agent executed (guardrail trail) | via owner; admin audits |
| `user_memories` | Memory | Learned per-user preferences + `pgvector` embedding | owner only |
| `events` | Analytics | Frontend + backend event stream for the admin dashboard | insert own; admin reads all |
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
`agent_answered`/`agent_error`. An **admin-only** dashboard (role-gated) shows a live events feed, the users
table, the datasets table, and Q&A/agent metrics from `query_runs` (latency, row counts, generated SQL).
Same stream feeds Logfire.

## Evaluation & user-journey tests

`evals/journeys.yaml` defines journeys (`as_user`, `question`, `expect`) that a **pytest** harness runs against
the real `/ask` flow, scored with **pydantic_evals**. Journeys double as RLS isolation tests (user2 must never
see user1's rows). Extend by adding YAML; runs in CI and blocks deploy on failure.

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
| **1 · Auth** | Entra JWT validation + JIT provisioning, MSAL login (dev stub fallback), protected `/me`, `/auth/config`, journey evals; 3 seeded users | ✅ done (live tenant pending) |
| **2 · Data + RLS** | Schema + Alembic migrations (all tables above), RLS policies, session-variable middleware, isolation tests | ✅ done |
| **2b · Pipeline** | `data/incoming` + `config/datasets.yaml`; dlt CSV→raw; dbt raw→marts with tests/docs; `datasets`/`dataset_access` populated | ⬜ todo |
| **3 · Agent** | Pydantic AI agent, read-only role, `run_sql`/`make_chart`/`recall`/`remember`, pgvector memory, Logfire, streaming `/ask` | ⏳ partial (SQL stub + guardrails) |
| **3b · Tracking + admin** | Event taxonomy + `POST /events`, `events` table, admin-only dashboard (feed, users, datasets, metrics) | ⏳ partial |
| **4 · Azure** | Bicep: Container Apps env + job, ACR, PostgreSQL Flexible (+pgvector), Key Vault, managed identity | ⬜ scaffolded |
| **5 · CI/CD** | GitHub Actions: build/push, Ruff/mypy/pytest + **journey evals** (pydantic_evals), deploy on merge to `main` | ⏳ partial |
| **6 · Harden** | Front Door + WAF, rate limits, statement timeouts, LLM cost guards, dashboards | ⬜ todo |

Evaluation (`evals/journeys.yaml`) is introduced in Phase 1 and grows every phase. Each phase ships something
runnable. Pause after each so the changes can be learned before extending.

---

## Known risks to keep in mind

- **NL→SQL safety** — mitigated by read-only role + RLS + `SELECT`-only allowlist + timeouts + row caps;
  worth a dedicated test pass.
- **LLM cost drift** — enforce per-user/day token caps and a cheap-model fallback.
- **RLS + pooling** — always `SET LOCAL` per transaction (see security model).

---

## Working in this repo

- This subdirectory is its own git repo. Commit/push only when asked; branch off `main` first.
- The parent `/git` workspace has its own `CLAUDE.md` — this file takes precedence for anything in
  `data-qa-agent/`.
