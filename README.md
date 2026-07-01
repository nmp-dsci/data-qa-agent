# data-qa-agent

An app that automates data science through a conversational **data agent**: users log in, ask questions in
natural language, and an AI agent turns them into governed SQL over data they're authorized to see — then
answers with the result.

- 📐 **Design & architecture:** [`AGENTS.md`](./AGENTS.md) (source of truth) and the visual review at
  `.lavish/data-qa-agent-architecture.html`.
- 🤖 **For AI assistants:** [`CLAUDE.md`](./CLAUDE.md) → points here and to `AGENTS.md`.

## Quick start (fully local, no cloud)

Requires Docker. One command boots the whole stack — Postgres+pgvector, backend-api, data-agent, frontend:

```bash
make data     # generate the sample housing.csv (committed already; re-run to regenerate)
make up       # build + start everything (first run pulls images / installs deps)
```

Then open **http://localhost:5230** and sign in as a test user:

| User   | Role  | Sees |
|--------|-------|------|
| admin  | admin | all data |
| user1  | user  | the housing dataset |
| user2  | user  | **nothing** — demonstrates row-level isolation |

Ask e.g. *"What is the average sale price by suburb?"* or *"What are the 5 most expensive properties?"*
Sign in as `user2` and ask the same thing — you'll get zero rows, because Row-Level Security isolates them.

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
frontend (React+Vite)  →  backend-api (FastAPI)  →  data-agent (NL→SQL)
      :5230                     :8000                     :8100
                                   │                         │
                                   └──────► Postgres + pgvector (RLS) ◄──────┘
                                                  :5434
```

- **frontend** — login + chat UI, fires product-analytics events, includes an admin dashboard.
- **backend-api** — validates the JWT, sets the per-request RLS context, orchestrates the agent, records
  conversations/messages/events.
- **data-agent** — turns the question into a single read-only `SELECT`, runs it under RLS, phrases the answer.
  Offline by default; uses Claude when a key is set (provider is abstracted — Decision G).
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
services/data-agent/    NL→SQL stub + Claude path; read-only SQL under RLS with guardrails
frontend/               React + Vite: login (dev stub or MSAL) + chat + event tracking
db/init/                schema + RLS + roles + seed + housing load (run on first `make up`)
config/                 datasets.yaml (registry), users.seed.yaml (dev users)
data/incoming/          housing.csv (generate with scripts/generate_housing.py)
evals/                  journeys.yaml — user-journey evals (grows every phase)
scripts/                generate_housing.py, smoke_test.py
docker-compose.yml      the local dev stack;  Makefile has the shortcuts
```

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

## Using Claude instead of the offline agent

The agent answers **offline by default** via a deterministic NL→SQL stub, so the demo runs with no API key.
To use Claude, put an `ANTHROPIC_API_KEY` in `.env` (see `.env.example`) and install the data-agent's `llm`
extra. The provider sits behind an abstraction, so this is a config change (Decision G).

## Troubleshooting

- **Port already in use** — the dev DB uses host port **5434** (5432/5433 were taken by other local
  containers). If 5230/8000/8100 clash, change the left-hand side of the `ports:` mapping in
  `docker-compose.yml`.
- **DB didn't re-seed** — init SQL only runs on a fresh volume. Run `make reset` then `make up`.
- **Frontend can't reach the API** — CORS allows `http://localhost:5230`; if you change the frontend port,
  update `cors_origins` in `services/backend-api/app/config.py` and rebuild backend-api.

## Deploy to Azure (dev)

Infra-as-code (Bicep) + a GitHub Actions deploy are scaffolded under [`infra/`](./infra/README.md).
`dev` is the same logical environment whether it runs locally or in Azure — only *where config comes from*
changes (`.env` locally vs Key Vault in the cloud). `staging`/`prod` are the same template with a different
`env`. See `infra/README.md` for prerequisites (OIDC, GitHub vars/secrets), the two-phase deploy, and the
one known gap (the DB migration image).

## Tooling & conventions

- **Package manager:** `uv` · **Lint/format:** Ruff · **Types:** mypy (strict) · **Tests:** pytest + smoke
- **Secrets:** `.env` (never committed) locally; Key Vault in Azure. See `AGENTS.md` for the full conventions.
