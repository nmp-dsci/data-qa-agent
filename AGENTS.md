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
services/backend-api/   FastAPI: dev-auth + Google ID-token validation, RLS context, /ask, /events, admin, explore
services/data-agent/    NL→SQL stub + Claude path, read-only SQL under RLS with guardrails, Explore grounding
services/data-pipeline/ dlt ingestion + dbt project (staging → marts, tests, RLS post-hooks)
services/db-migrate/    Alembic migrations (the `migrate` job; runs local + cloud)
services/mcp-server/    Standalone MCP front door (s35 rung 3) — no DB credentials, calls backend-api
                        over HTTP with its own service key; `make mcp-test`/`make mcp-smoke`
frontend/               React + Vite: login (dev stub or Google Sign-in) + chat + Explore tab + event tracking
frontend/public/geo/    pre-built choropleth paths (poa_nsw.paths.json — see scripts/build_topojson.md)
db/init/                canonical schema/RLS/seed SQL applied by the 0001 Alembic baseline
data/samples/           small committed NSW sample CSVs (full data is gitignored)
evals/                  journeys.yaml — user-journey evals (auth + RLS + growth; grows every phase)
db/init/                schema + RLS + roles + seed + housing load (run on first `make up`)
config/                 datasets.yaml, users.seed.yaml
data/incoming/          housing.csv (generate with scripts/generate_housing.py)
docs/chronicle/         vendored legacy NSW profiling tool (Explore reference — see its README)
scripts/                generate_housing.py, smoke_test.py, build_poa_paths.py, explore_parity.py
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
   signature/claims on every request. In `AUTH_MODE=dev` only, `POST /auth/dev-login` also sets the same
   token as an httpOnly, `SameSite=Lax` cookie (`dp_session`) so a page reload survives without dropping the
   session; `get_current_user` checks the `Authorization` header first and only falls back to the cookie
   when no header is sent, so scripts/CI/smoke tests are unaffected. `POST /auth/logout` clears it. Google
   mode and production are untouched — frontend and backend sit on different registrable domains there
   (CloudFront vs App Runner), so a cross-site cookie would need `SameSite=None`, a separate decision.
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

### Non-UI surfaces and service accounts (s35)

Three front doors besides the web UI — a webhook, a Slack slash command, and a standalone MCP server —
all reach the *same* pipeline. `routers/ask.run_question()` is the single implementation of the daily cap,
RLS scoping, degraded-mode fallback and the audit write; the adapters authenticate and deliver, and
reimplement none of it. Four front doors, one security surface. `data-agent` is untouched by all of this.

- A **service account is a user** (`auth_provider='service'`, migration 0032), so grants, RLS, conversations
  and `query_runs` work unchanged. Keys are `dpk_<key_id>_<secret>`: only a SHA-256 of the secret is stored,
  and the full key exists exactly once, in the create response. `surface` pins a key to one door.
- Service accounts are never `role='admin'`. Usage is tiered via `plan='service'` instead, so a leaked key
  cannot read across users. `app.service_accounts` is granted to `app_user` only — **never `agent_ro`**, since
  the agent runs model-authored SQL.
- **The access boundary for Slack is channel membership, and that is a deliberate trade, not an oversight.**
  v1 answers as a bot identity with its own dataset grants: everyone in the channel sees the same scope, so
  grant a Slack account only what everyone in that channel should see. Per-user delegation (an act-as header
  resolved against a default-deny mapping table) is the documented upgrade path, unbuilt until something
  needs it. Accountability is kept even though authorisation is shared: the asking Slack user, their name
  and channel are recorded against every run.
- Slack authenticates differently from the other two: it signs its own requests, so `X-Slack-Signature`
  (verified over the **raw** body, 5-minute replay window) proves the *request*, while the service account
  decides what may be *seen*. An unset `SLACK_SIGNING_SECRET` closes the endpoint with a 404 rather than
  weakening it.
- **Key management (rung 4)** is an admin-only **Settings → Service accounts** panel
  (`frontend/src/features/settings/SettingsPage.tsx`) over `/admin/service-accounts*`: mint a key for a
  surface with a dataset grant, revoke one, see `last_used_at`. The reveal-once view is styled as a warning,
  not a success state, since the raw key exists exactly once in the mint response and can never be
  re-displayed — only `key_id` is listed afterward. The dataset picker reads the Explore dataset catalogue
  (`GET /explore/datasets`), not the admin's own `dataset_access` rows — an admin reads across users by role
  and has none of their own, so sourcing from `getMyAccess` would leave the picker empty.
- The MCP surface is **mounted on backend-api at `/mcp`** (s36; s35 shipped it as a separate container).
  Its tools call the same handlers the UI calls — `run_question`, `run_sql`, `schema_catalog` — so there is
  still exactly one implementation of the guard, the cap and the audit write. Two consequences of the move,
  both deliberate: it no longer has the standalone server's "holds no database credentials" isolation, and
  in exchange the **client** must now present a `dpk_` key pinned to `surface='mcp'` (`ServiceKeyGate`,
  raw ASGI, in front of the JSON-RPC transport) where the standalone server accepted any client that could
  reach its port. Auth moved forward; isolation moved back.
- The MCP transport is mounted **stateless** so each JSON-RPC call runs in its own request task — that is
  what makes the per-request identity `ContextVar` correct. A session-mode transport would run tool calls in
  the task that opened the session, i.e. under the wrong identity. The tools fail closed rather than fall
  back to a surface lookup if that context is ever missing.
- Because the key gate sits in front of the transport, the SDK's DNS-rebinding host allowlist is
  defence-in-depth, not the primary control, and is **off unless `MCP_ALLOWED_HOSTS` is set**. Requiring it
  meant a deployment could not work until a second apply taught it its own hostname (App Runner assigns that
  at create time and a service cannot reference its own `service_url`).
- MCP's auth tier is a service key, which does not meet the OAuth-2.1-with-scopes bar for enterprise MCP;
  that gap is deliberate and staged, not closed.
- `POST /sql` is capped for `plan='service'` only (`check_daily_query_cap`, s36). A governed SELECT spends no
  tokens, so human editor use stays uncapped — but a machine key can loop, and the MCP surface hands
  `run_governed_query` straight to a model. It is a rate bound; the guard applies to every statement anyway.

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

## Explore (s19+s20)

A tab (`frontend/src/features/explore/`) for browsing the property/postcode marts directly — filters,
aggregation, a cohort profiler, and a NSW postcode choropleth — without going through the chat agent.
`backend-api`'s `app/explore/` owns it: `manifest.py` declares three governed datasets (`nsw_sales`,
`nsw_rent`, `nsw_yield`, backed by `marts.property_sales` / `marts.property_rent` / `marts.property_yield`),
`service.py` + `engine.py` build and run manifest-checked aggregate/profile SQL (only allow-listed
identifiers reach SQL; user input is bound parameters only), and `routers/explore.py` exposes
`GET /explore/datasets`, `GET /explore/typeahead`, `POST /explore/aggregate`, `POST /explore/profile`, and
`POST /explore/ask` (NL-assisted filter setup via `nl_setup.py`). Reads run under the same RLS connection as
everywhere else and are audited into `app.query_runs` with `source = 'explore'` (migration 0026), so Explore
usage shows up in the same audit trail as chat and the SQL editor — and never counts against the daily LLM
caps.

The data-agent mirrors this capability rather than duplicating it: `agent/tools_explore.py` grounds the LLM
with the same three dataset slugs + backing tables plus the "profile comparison" pattern (Target cohort vs
Comparison cohort, rank segment deltas) so "what drove X" questions get answered in chat the same way the
Explore Profile tool answers them. `tests/test_explore_agent_sync.py` asserts the agent's mirror never drifts
from the backend manifest.

**Per-cohort profiling.** Target and Comparison each carry their own `CohortBody.dataset`/`.metric` (falling
back to the top-level `ProfileBody.dataset`/`.metric` for older callers), so a cohort pair can compare across
datasets entirely — e.g. Sold volume (`nsw_sales`) against Bond volume (`nsw_rent`) for the same postcode.
`POST /explore/profile` also takes a `calculation`: `raw` (plain values), `pct_total` (each side's value as %
of that side's own unfiltered grand total), or `growth` (target vs comparison % change) — purely a
display/derivation choice, it never changes which rows are fetched. The per-predictor segment-delta pipeline
(rank leaderboards, choropleth) only runs when both cohorts resolve to the **same** metric name, since a
segment-by-segment delta between two different measures isn't meaningful.

**Dimension picker tri-state.** `frontend/src/features/explore/MultiSelect.tsx`'s selection is `null` (no
filter — every row matches), `[]` (explicitly zero values chosen — matches nothing), or a populated array,
rather than collapsing "nothing ticked" and "no filter" into one state; the picker adds "all"/"none" bulk
actions and a live "3 of 12"-style summary. Postcode filters (`controls.tsx`/`lib/format.ts`) display and
search with a `POA:` prefix (`formatPoa`/`isPoaDimension`/`stripPoa`) to read as ABS Postal Area codes rather
than bare digits, without changing the underlying stored/filtered value.

**Chart-object unification (s20):** Explore, chat, and Golden Examples now render page objects through one
shared contract (`frontend/src/report-engine/PageLayout.tsx` + `registry.ts`) instead of divergent chart
code paths. `DataTable` was promoted to a first-class, agent-emittable chart object (migration 0027); the
NSW postcode choropleth (`ui/charts/Choropleth.tsx`, pre-built paths from `scripts/build_poa_paths.py` —
see `scripts/build_topojson.md`) is deliberately **Explore-only** and not agent-emittable. Every chart on
every surface deep-links to the SQL editor via a `data.sql` field (`ui/charts/sqlLink.tsx`). An object-type
parity gate (`services/data-agent/tests/test_registry_sync.py`) cross-checks the agent's `ObjectType`, the
frontend's `PageObjectType`/`ObjectBody`/registry, and seeded chart migrations so a type added to only one
of them fails CI instead of silently drifting.

The legacy static NSW profiling tool this feature replaces is vendored for reference at `docs/chronicle/`
(see its README) — its heavy data files (`datafeed/`, the reduced POA geojson) are gitignored, with restore
instructions there.

---

## The Flight Deck brand (s25)

The frontend — login through every authenticated surface — reads as a glass cockpit. The kit
(`frontend/src/ui/flightdeck.tsx`) is five primitives every surface consumes instead of re-rolling markup:
`PlaneGlyph` (the mark, `ui/icons.tsx`), `FlightPath` (route + waypoints + an optional flying Sortie),
`HudBox` (a corner-ticked readout frame), `Annunciator`/`Annunciators` (status lamps for guarantees like
RLS/AUDIT and live states like PASS/FAIL), and `InstrumentLabel` (the mono-caps voice). Instrument type is
reserved for labels, telemetry, and section markers — enforced by convention, not code — and aviation words
never reach interactive copy ("Run query", never "Take off").

`--hud` is a semantic token in `styles.css` that aliases the `--good` pigment, reserved for live telemetry
only (HUD readouts, the lit flight path, "on" annunciators); anything gated on request success/failure keeps
using `--good`/`--bad` directly so the two can be retinted independently later. The Settings appearance
control reads **NIGHT / DAY / AUTO** instead of Dark/Light/System — labels only, the underlying `ThemePref`
values, `<html data-theme>` resolution, and OS tracking are unchanged.

`Login.tsx` replaced the static split screen with a departure-profile climb-out driving a walkthrough of
product micro-mocks (a full-width diagonal sweep was mocked but dropped — it flew waypoints behind the story
cards); the chat hero's flight-plan strip is a single column rather than the mocked four-across row, since
four columns at the hero's 620px cap leave no room for the question text. The rebrand shipped as one PR
across all eight app surfaces (chat, SQL editor, Explore, Goldens, Evaluations, Admin, Settings, login) plus
the day theme and an a11y/contrast pass, so `main` was never left half-branded. (The walkthrough itself was
dropped in s33 — see below.)

The login's Playwright visual baselines force `reducedMotion: "reduce"`: `animations: "disabled"` stops
CSS/SMIL but not JS timers, which mattered while the s25 walkthrough's auto-advance was still driving the
screenshot; the night-flight canopy (s33) has its own motion gate instead (see below).

**Card/airway legibility (s26, superseded by s33).** E2e testing on the s25 PR flagged that "traffic behind
a windshield" only read in the left panel and the gutters — the story column's `.walk-prop` cards were too
opaque for the airways to show through it. Fixed two ways: inactive cards go more glass (`--panel` 58%→34%)
while the active card (`.walk-prop.lit`, which carries the step's dense micro-mock) stays solid so it
doesn't lose contrast, and the `.air-2`/`.air-3` aircraft + route opacities are raised, since card opacity
alone couldn't fix visibility once the aircraft crossed behind the column — both stay subordinate to the
hero Sortie. Verified by measurement rather than by eye: a probe hides card content to sample the true
composited backdrop and checks it against the live `--text`/`--muted` tokens across viewports and themes,
and a second probe checks row-luminance variation over the busiest card to confirm the airway is
perceptible, not merely present. The `.walk-prop` cards and airway aircraft themselves are gone with the
s25 walkthrough (s33) — this note is kept for the measurement technique, not the current markup.

### The night-flight canopy (s33)

The brand's scene is now **one canvas component**, `frontend/src/ui/Canopy.tsx`: a seeded starfield, a gold
horizon curved with the planet, and a neon grid terrain flowing toward the viewer. It runs at two
intensities from the same renderer — `variant="login"` (full strength, full DPR) and `variant="ambient"`
(~40% opacity, half speed, capped at 1× resolution and ~30fps) — and the ambient layer is rendered **behind
every app screen**, replacing the old static `body::before`/`::after` gradient wash. Signing in no longer
changes sky. Three rules the code depends on:

- The ambient `<Canopy>` is a **sibling of `.app`, never a child** — `.app`'s `view-in` keyframe animates a
  transform, which would make it the containing block for a `position: fixed` descendant for the length of
  the animation and jolt the whole sky. `.app` carries `position: relative; z-index: 1` to sit above it.
- Motion stops under `prefers-reduced-motion` **or** the Settings *Ambient motion* switch
  (`lib/motion.ts`, a `useSyncExternalStore` pref mirroring `theme.ts`); a single frame is painted instead,
  so the still is designed rather than blank. The loop also stops while the tab is hidden.
- Report pages are excluded by construction: `downloadSvgAsPng` serialises the `<svg>` alone onto a solid
  `--panel` fill, so nothing outside a chart can reach an exported PNG.

The mark moved with it — `BrandMark` (and its hand-synced twin `public/favicon.svg`) is the scene reduced to
a 64-unit tile: stars over a curved horizon over a vanishing-point grid, replacing the s17 airliner, which
said "aviation" but not "data". Strokes are deliberately heavy because the mark ships at 24px in the mobile
bar. Tagline: **"Cleared for insight."**

The login itself dropped the s25 walkthrough carousel: a first-time visitor was shown a quarter of the value
proposition at a time on a 4s timer. Everything is visible at once now — a 2×2 benefit grid with micro-sketches
of the real surfaces, a strip of real example questions, and an instrument cluster of *outcome* metrics
(≈2 min / 0 lines of SQL / 100% row-level scoped / every chart opens its query). Those are properties of the
product that hold on any warehouse, deliberately **not** deployment counters, which would be a lie on a fresh
install. Preserved contracts: the dev profile buttons' exact text (`e2e/helpers.ts` clicks them by name) and
the "Data Pilot" heading the visual/a11y specs wait on.

### The shell kit (s33)

`frontend/src/components/kit/` is the shared layer the app was missing — thin, opinionated wrappers over the
shadcn primitives, so a control looks and behaves the same everywhere:

- **`KitSelect`** — every native `<select>` in the app is gone. Radix forbids `value=""`, so an `EMPTY`
  sentinel maps the "all" filters through without changing caller state.
- **`KitMultiSelect`** — Popover + Command with a tick per row; replaces `<select multiple>`, whose
  cmd-click model nobody discovers. Selection order is preserved (composite keys depend on it).
- **`KitEmpty`** — one designed empty state: what would live here, how to fill it, and the control that does.

**E2E contract:** a Radix select has no `.value` and its options are labelled, not valued, so both kit
selects stamp `data-value` on the trigger and on every option. `expect(x).toHaveAttribute("data-value", …)`
replaces `toHaveValue()`, and `e2e/helpers.ts::pickOption` replaces `selectOption()` — specs keep addressing
the same values, and human labels stay free to change. (`KitMultiSelect` options use `data-option-value`:
cmdk owns `data-value` on its items.)

The desktop rail expands 56→196px on hover/`focus-within` as an **overlay** (`.rail` keeps its 56px
footprint; `.rail-inner` grows over the content) so a pointer passing through can never reflow the view.
Items stay `role="tab"` with an `aria-label`, which wins over the now-visible text, so `getByRole("tab", …)`
is unaffected. Glyphs are `lucide-react` at 20/1.8 (Evaluations finally has its own — it reused the Goldens
reticle for a year), and "Ops" was renamed **"Operations"** across the rail, command palette and e2e.

**Aurora cold-start login (s29).** Aurora Serverless v2 at min 0 ACU auto-pauses after an idle hour; a
resume took ~30s in an observed prod session, and every request that landed in that window used to hang
(asyncpg's default 60s connect timeout) with no feedback, so users answered the silence by signing in
4-5 times. Fixed end to end: `db.py` bounds the connect phase to 5s so a stuck resume fails fast instead of
hanging; `services/backend-api/app/waking.py::is_db_waking` classifies that failure (and SQLSTATE class 08 /
`57P03`) from the exception chain, careful to exclude `57014 query_canceled` (a real bug) and DNS/httpx
errors (unrelated outbound calls); the global error handler in `main.py` turns a classified failure into a
retryable `503 {"detail": "db_warming"}` instead of the default 500. `GET /health/db` is an unauthenticated
wake probe the login card fires on mount (`wakeDb()` in `frontend/src/lib/api.ts`) so Aurora starts resuming
while the user is still in the Google sign-in dance, front-loading most of the wait; it only touches the
database for requests carrying `X-Client-Channel: web` (a fence against generic pollers defeating
auto-pause) and coalesces repeated probes within 5s to one cached result. `apiFetch` retries any `db_warming`
503 transport-side (up to `WARMING_MAX_MS` = 75s, every `WARMING_RETRY_MS` = 4s) so mid-session calls ride
out a wake behind their surface's existing pending UI; `/me` opts out of that generic retry because the
login flow (`frontend/src/lib/auth.ts::exchangeCredential`) owns its own retry loop so it can narrate
progress ("Waking warehouse · Ns") on the card via the `signing`/`LoginStatus` state in `Login.tsx`.

---

## Data model (Postgres)

All capabilities live in one Postgres, all under RLS.

| Table | Group | Purpose | RLS |
|-------|-------|---------|-----|
| `users` | Identity | Local mirror of signed-in users (Google, dev-seeded, or `auth_provider='service'`) + role (`admin`/`user`) + `plan` (incl. `service`) | self; admin sees all |
| `service_accounts` | Identity | Machine identities for non-UI surfaces (s35) — `key_id`/`key_hash` (SHA-256, secret shown once), `surface`, `revoked_at`; FK to a `users` row | granted to `app_user` only, **never `agent_ro`** |
| `datasets` | Datasets | Registry of ingested datasets the agent can answer over | readable if access granted |
| `dataset_access` | Datasets | Which users/roles may query which dataset | self; admin manages |
| `dataset_ordinals` | Datasets | Curator-editable ordinal band order per `(dataset, column)` (e.g. `area_band`) so ordinal chart axes sort naturally, not alphabetically | admin/CI-curated; no RLS |
| `conversations` | Q&A | A user's chat sessions | owner; admin sees all |
| `messages` | Q&A | Turns: question, answer, generated SQL, tokens, latency | via conversation owner |
| `query_runs` | Q&A | Audit of every SQL executed (`source` = `agent` / `sql_editor` / `explore`); s32 adds the cache-token split, priced `cost_usd`, `degraded`, `attempts`, `ttfp_ms` and the `otel_trace_id` Logfire deep-link | via owner; admin audits |
| `user_memories` | Memory | Learned per-user preferences + `pgvector` embedding | owner only |
| `events` | Analytics | Frontend + backend event stream for the admin dashboard | insert own; admin reads all |
| `eval_cases` | Evals | Golden answers — feedback-promoted or hand-authored stages (`golden_sql`, `golden_sandbox`, `golden_objects`, `golden_report`) | admin/CI-curated; no RLS |
| `agent_versions` | Evals | Fingerprint of the agent build (provider, model, prompt/skills hashes); stamps every `query_runs` row | admin/CI-curated; no RLS |
| `eval_runs` / `eval_results` | Evals | Batch grading: one row per pack run + per-case pillar scores (G1–G4), linked back to `query_runs` | admin/CI-curated; no RLS |
| `ops_rollup` | Ops | Pre-aggregated deck metrics, one row per window (24h/7d/28d) — the only thing `/admin/ops/summary` reads (decision Q3) | admin/CI-curated; no RLS |
| `load_tests` | Ops | k6 results: scenario, VUs, rps, p50/p95/p99, error rate | admin/CI-curated; no RLS |
| `security_runs` | Ops | Red-team / injection pack results with per-category pass rates | admin/CI-curated; no RLS |
| `deploy_events` | Ops | Every deploy: sha, actor, duration, smoke result, `deployed`/`rolled_back`/`failed` | admin/CI-curated; no RLS |
| `pipeline_runs` | Ops | Marts freshness + dbt pass/total per pipeline run — the data-staleness signal | admin/CI-curated; no RLS |
| `judge_samples` | Ops | Advisory insight scores over sampled live asks (FK `query_runs`) | admin/CI-curated; no RLS |
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
`datasets`, and `access` populates `dataset_access` so RLS enforces who can query it. The `nsw_yield` dataset
(`marts.property_yield`, sales JOINed to rent by postcode/property_type/month, plus the `dim_postcode_geo`
region-rollup mart) is registered directly by migration 0025 instead of the pipeline's dataset upsert, since
it derives from the other two marts rather than its own CSV. App config
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
Deterministic graders (`agent/eval_graders.py`) score G1 extraction values and the structural half of G3
presentation against a `ready` golden — the LLM insight half of G3 is a judge, not code. G2 preparation
(did the sandbox build the golden's objects) and G4 ops (turns, latency, tokens) are scored directly by the
runner; a case passes when G1 is at or above threshold and the G3 report shape is well-formed — G3 insight
is scored and recorded but does not gate on its own (s24 M2, below).

**Version control (s24 M1).** Goldens live in the database *and* in the repo: `make eval-export` serialises
`app.eval_cases` to `evals/cases/<dataset>.yaml` and `make eval-import` seeds any environment from it, so a
golden authored in dev or promoted in prod is reviewable in a PR and visible to CI. The repo is the source
of truth; the DB is a working surface. `golden_data` is treated as *derived* — the pack keeps only a digest,
since G1 regrades against what `golden_sql` returns at eval time. Every `/ask` is stamped with an
`agent_versions` build fingerprint — a composed hash of provider + model + `prompt_hash` + `skills_hash` +
`knowledge_version` (`agent/version.py`, served at `GET /agent/version`) — so a base-vs-experiment
comparison can prove exactly one lever moved. Runs predating M1 carry a null stamp and are not backfilled.
Batch scores land in `eval_runs`/`eval_results` (migrations 0019–0024, extended by 0029; the pack's
per-golden `grader` spec — which `kind` of comparison G1 dispatches on — is migration 0030). Since a golden can
be promoted from a real prod chat answer, `scripts/eval_pack.py export` redacts it on the way into the repo
(decision D-2): `as_user` is remapped to a seeded test identity and embedded row data is capped to its
leading rows (originally with a digest stub kept for the remainder — replaced in s28, below, because the
frontend renders those fields), so the pack can never become a back door around RLS. The Golden
tab's dataset picker now reads the dataset registry instead of a hardcoded `["nsw_sales", "nsw_rent"]`
literal, which had silently locked `nsw_yield` out of golden authoring since migration 0025.

**Scored runner + judge (s24 M2).** `make eval` (`scripts/eval_run.py`) drives the golden pack against the
running agent, works down to a single case (`CASE=`), and calls the data-agent's `POST /agent/eval/grade`
to score G1/G2/G3-structural plus the G3 insight judge. The judge (`agent/eval_judge.py`) grades a frozen,
hashed rubric (`judge_prompt_hash`) and refuses to grade a model of its own family — with DeepSeek
answering, only an Anthropic key can judge — recording a `skipped` verdict rather than fabricating a score
when no cross-family judge key is configured. Insight is scored and reported but does not gate a case on
its own; a case passes on G1 + G3-structural.

**Regression gate + pack lint (s24 M3).** `make eval-compare A=<run> B=<run>` (`scripts/eval_compare.py`,
also served at `GET /admin/eval-runs/{id}`) is the base-vs-experiment gate: it blocks on **any** case
flipping pass → fail, regardless of what the headline averages do, and refuses to compare runs graded
against different pack versions. `tests/test_eval_pack.py` is a separate, zero-LLM-cost CI job (the golden
pack gate in `.github/workflows/ci.yml`) that lints the pack itself — unique case keys, dispatchable grader
specs, no real user ids or unredacted data — and blocks every merge, unlike the scored gate which needs a
live agent and API keys and stays a manual/CD step.

**Evaluations tab (s24 M4).** An admin-only, read-only tab (`frontend/src/features/evals/EvalsPage.tsx`,
backed by `services/backend-api/app/routers/evals.py`) shows base-vs-experiment runs, the gate verdict, and
per-case scores linked to the `query_runs` trace that produced them. `eval_runs`/`eval_results` are written
only by the offline runner script, never by the API, so a score can never be produced by clicking something
in the UI.

**Diagnosis (s24 M6).** `make eval-diagnose` (`scripts/eval_diagnose.py`) reads a scored run's traces and
proposes one-lever hypotheses for the next cycle. It is read-only by design (decision D-3) — write access
(e.g. auto-editing knowledge/prompt files) is explicitly deferred, not implemented.

Three improvement cycles run against the live DeepSeek agent and the curated goldens are written up in
`docs/evals/cycle-001.md`–`cycle-003.md`: two accepted by the gate, one deliberately rejected because it hit
its own stated cost target but broke an accuracy case — the core proof that the gate blocks on regressions
rather than trading them off against an average.

Three more ways to seed and refine a golden (s21–s23): an admin chat answer can skip stage ① entirely — a
"★ save as golden" chip in the chat result (shown whenever the answer has an audited `run_id`) calls
`POST /admin/eval-goldens/from-run` to copy the question/SQL/sandbox script/report already captured on
`query_runs`/`messages` into a new draft (idempotent per run) and opens it straight in the editor, no agent
re-run. Inside the editor, stage-② presentation objects were originally authored via a NL-first
**"New object with AI"** panel (one sentence auto-derived a name/type and auto-placed the built object onto
the report); that panel has since been dropped in favour of the deterministic structured builder (s28,
below). Ordinal dimensions (`area_band`,
`bedroom_band`, …) render in their natural order instead of alphabetically — `agent/ordinals.py` is the
registry (a code-level `BAND_ORDERS` seed plus a curator-editable override), consulted by the chart lift on
every surface; curators edit the override per `(dataset, column)` in `app.dataset_ordinals` (migration 0028)
via a data-knowledge panel in the Sandbox tab, picked up on the next Run, with a manual "sort x-axis"
control in the report editor for columns the registry doesn't cover.

**Readable sandbox failures (s26).** Model-written analysis code that raises inside the sandbox used to
surface its raw `traceback.format_exc()` verbatim — including into the Golden Sandbox builder's status
line, where the success message belongs. `agent/sandbox/errors.py`'s `explain_sandbox_error` reduces a
traceback to its final exception line plus a targeted hint for the failure modes that come from a house
skill's return shape rather than a typo (`skills.latest_value` always returns a dict; `skills.growth_rate`
returns one once `group_col` is passed; `skills.top_growth` returns a DataFrame that must not be indexed
like a mapping). It's wired into both `object_codegen`'s correction loop (only two passes, so the first
piece of feedback has to carry the real cause) and every `/agent/analysis*` endpoint's `error` field, so
the UI can never show a bare traceback.

**Question tiers (s27).** Every golden carries a `tier` (`app.eval_cases.tier`, free-text; the Golden tab
picker and the pack lint both accept `T1`–`T7`). A tier classifies *what kind of question* the golden is, so
the pack's coverage can be read at a glance and `make eval TIER=T3` / the Evaluations tab can break scores
down by difficulty. The ladder:

| Tier | Kind | What it tests |
|------|------|---------------|
| **T1** | Lookup | one value at the latest month (`scalar` grader) |
| **T2** | Trend | a time series and its variants — trend, ranking, comparison, segmentation (`series`/`ranked_set`/`row_set`) |
| **T3** | Multi-mart | a join across datasets (sales ⨝ rent, rent ⨝ yield, …) |
| **T4** | Ambiguous | an underspecified ask the agent must scope and state its assumptions for (no fixed grader) |
| **T5** | Adversarial | a data boundary or trap — out-of-coverage dates, thin cells, forecast requests — where the right answer names the limit instead of fabricating |
| **T6** | Geo roll-up | postcode rolled up to SA3/SA4/GCC via `marts.dim_postcode_geo` (weight by the count leg, never average the averages) |
| **T7** | Recommendation | a composite "where to buy next & why" verdict combining several measures into a justified shortlist |

The s27 coverage pack seeds 10 draft goldens per mart against this ladder (one per direction, T2 carrying the
four time-series shapes). Drafts (`authoring_status='draft'`) are skipped by `make eval` unless
`INCLUDE_DRAFTS=1` is passed (the runner's `--include-drafts`; naming one directly via `CASE=` also runs
it), so an un-curated question is never scored against empty ground truth.

**Crash-proof object rendering + schema-driven builder (s28).** A rendering exception in any single report
object used to unmount the whole SPA — opening a golden whose stored `golden_objects` carried a non-array
`rows` (the pack exporter's old `{_truncated,…}`/`{_omitted,…}` digest stubs) white-screened the entire
Golden tab. Every object now renders inside `frontend/src/report-engine/ChartErrorBoundary.tsx`, wrapped
once in `PageLayout.tsx`'s `ObjectBody` so every consumer (chat, Explore, Goldens, the Template Studio
preview) is covered: a failed object degrades to a fallback card, the rest of the report renders, and a
data-derived `resetKey` un-fails the boundary when the object is rebuilt. Chart renderers also coerce
`rows` through `asRows` (`ui/charts/tokens.ts`) so an unusable shape renders empty rather than throwing.
The exporter side of the same bug is fixed too: `scripts/eval_pack.py` no longer wraps oversized rendered
fields in digest envelopes — `golden_report`/`golden_objects` lists are truncated to a plain head (still
lists, so they stay renderable), with ground-truth drift tracked by `golden_data_sha` alone.

The Sandbox tab's structured builder is now schema-driven end-to-end: its x/dimension, optional 2nd
dimension, group and measure-source fields are dropdowns bound to the dataset's typed vocabulary from the
Explore manifest (`GET /explore/datasets`) — mart-backed dimensions only (geo rollups and computed dims
need a JOIN the builder can't emit) and additive metrics only (every path sums the source through the
window dedup, and summing a stored average is silently wrong; non-additive figures are recomposed as
num/den weighted averages instead). Each measure is a base aggregation plus an optional derived
augmentation over the window (share / growth / latest / …, see "Metric = aggregation + a derived
augmentation (s31)" below) — share is deliberately *within-series*, so each series sums to 100% across the
x-axis (the "mix" reading) — and a 2nd dimension synthesises a composite x-axis (e.g. `bedroom_band ×
property_type` joined into one nominal label). `agent/object_builder.py` guards what the form can't: column
identifiers are validated, non-additive sources rejected from any summing path, `filter` fragments refused
if they smuggle statement separators or nested SELECTs, and `extract_grain` shares `_grain_with_chart_cols`
with the codegen's `_bar_grain` so the rewritten extract and the snippet's window-dedup grain can never
drift (trend/kpi keep the typed grain, defensively including `group` when set, since `trend_series`'s
`group_col` must always be present in the extract even for a spec authored before the frontend enforced
that invariant). A bad spec surfaces as `invalid spec: …` from `/agent/analysis/build-object`, never a
traceback.

**Grader-spec editor — promote draft → ready from the Golden tab.** A golden's `grader` (jsonb, migration
0030) is what the eval runner scores against (`scripts/eval_run.py` → `POST /agent/eval/grade`), but until
now it could only be written by hand-editing the YAML pack, so a curator could never make a draft
*scoreable* from the UI — the one gap that kept the Golden tab from closing the loop. The `◆ GRADER` panel
(`frontend/src/features/goldens/GraderEditor.tsx`, logic in `graderSpec.ts`) turns that jsonb into
grain-driven dropdowns: pick a `kind` (`scalar`/`row_set`/`ranked_set`/`series` — the tier suggests one),
its key column(s) (one → `key`, many → the composite `key: "_key"` + `key_fields`, mirroring the builder's
composite x-axis), a `value`, `tolerance_pct`/`k`, an optional `aggregate` (`sum`, or `ratio` with
numerator/denominator so a weighted average is graded, never an average-of-averages), and the
`expected_objects` the report must contain. A deterministic, no-LLM `graderIssue()` renders `✓ ready to
promote` / `✗ <reason>` and gates both the **Promote to ready** button and the header status dropdown's
`ready` option — its rules mirror the CI pack-lint (`tests/test_eval_pack.py`: dispatchable kind, required
fields per kind, and every named column present in the ① SQL extract — a grader that names columns but
hasn't had the SQL run yet, or was edited after it ran, blocks with "run ▶ Run SQL" rather than silently
passing), so the UI blocks exactly what CI would reject. The `grader` column is now wired through `GoldenIn`/`GoldenPatch` + `_FULL_COLS`/`_JSONB_COLS`
(create/update/get round-trip it), the list carries a `grader_kind` badge, and `frontend/e2e/grader.spec.ts`
asserts the real composite-ratio-series grader decodes on load and that a fresh golden can't promote without
a kind.

**Builder live preview + edit-in-place + filter preservation (s30).** Four connected refinements to the
object builder / report editor: (1) **Filter preservation** — building an object could silently drop the
question's own filter, because `canonical_extract_sql` replaced the WHERE with the builder's `filter` field
(or best-effort per-column lifted it). An object is a summary of the *same* governed rows the question
scoped, so `original_where()` now lifts the base extract's full WHERE verbatim and always preserves it —
scanning at paren-depth 0 so a subquery/CTE's own WHERE is skipped in favour of the outer query's — and the
`filter` field is only ANDed on top (an object narrows, never widens). (2) **Live preview** — whenever the
structured builder's config is green, a debounced deterministic build (minus placement) renders the actual
chart via `ObjectBody` at the top of the builder, so what you see is what Build will drop in. (3)
**Edit-in-place** — the per-object edit panel's plain-English "describe this object's data" LLM box is gone;
a chart card's **◆ edit in Structured Builder** button seeds the builder from the object's stored
`SandboxObjectSpec` (`builderFromSpec`, the inverse of `specFromBuilder`) and scrolls to it, so the preview
becomes the object's chart and the options below are how you change it — Build replaces it in place by
`element_id`. A chart with **no stored spec** (a drafted base-report chart like `report:chart`, whose
`golden_objects` is empty and whose element_id isn't `obj:<slug>`) still edits in place: `editObjectInBuilder`
sets `previewEditId` and seeds valid dataset defaults + the object's type/name (`builderFromObject` — the
rendered encodings can't be trusted as extract columns, so it doesn't reconstruct them), and Build (name
falling back to the id when blank) replaces that object rather than orphaning a new one. (4)
**Move to any page + column** — the edit panel gained a page picker beside the column picker (`moveTo(pi,
ci)`), so a card relocates across pages without dragging. (5) **Dataset from the SQL, not the tag** — the
builder derives its dataset (vocabulary, defaults, and build target) from the ① SQL extract's `FROM` table
(`datasetFromSql`, mirroring the backend mart tables), so a golden mis-tagged `nsw_sales` whose SQL reads
`marts.property_rent` still opens with rent grains/metrics instead of sales ones. The filter is shown as two
lines: line 1 is the golden's own WHERE, carried from the SQL and always kept (read-only); line 2 is the
builder's `filter` field, an additional predicate ANDed on top — `BuilderFilter.tsx` renders that second line
as the same dimension chips (domain dropdowns with distribution bars, typeahead) the Explore/Profile cohorts
use, rather than a raw SQL box, so both tabs read identically. `filterSql.ts` translates between the chips and
the SQL fragment the object builder expects; a predicate the chips can't express (a range, an OR, a
comparison) keeps the raw-SQL escape hatch instead of being silently dropped, since dropping one would widen
the object's rows.

**Metric = aggregation + a derived augmentation (s31).** A bar/line measure is now a **base aggregation**
(`sum`/`mean` of one column, or a weighted-average `num`/`den`) plus an optional **derive** that augments it
over the window — the two were previously conflated in one `how` dropdown. The metric row reads
`name = agg of column · window · derive`. `agent/object_builder.py::_measure_block` builds the monthly
additive components once, then the `derive` reduces them to one value per key: `share` (% of total within
the series), `growth` (**period-over-period** — the recent window vs the prior window, replacing the old
first-vs-last-month math), `latest`, `rolling` (window mean), `index` (=100 at the window's start),
`cumulative` (running total), `rank` (within the series), `yoy` (vs 12 months prior). A ratio metric
(`avg_sale_price`, `avg_weekly_rent`, …) exposes its two additive legs as `Metric.num`/`.den` in the manifest
and over `GET /explore/datasets` (null for a non-ratio metric, e.g. `gross_yield_pct`), so a consumer that
must re-aggregate at its own grain — the object builder's `wavg` base above — recomposes the weighted average
instead of summing the pre-computed ratio, which is silently wrong. `share`/`cumulative`
need a `sum` base and the time derives need `month` in the grain — both rejected at codegen, and mirrored in
the frontend `buildabilityIssue` so the green check gates them. Old goldens stored the augmentation as `how`
(share/growth/latest, sum base); `_measure`/`aggOf`/`deriveOf` map it forward so they keep working, and the
`none`/`sum`/`mean`/`wavg` paths are byte-identical to before so existing objects don't shift. The derive
dropdown only shows for the bar family (compare/breakdown/table).

A trend/line chart's x-axis is no longer assumed to be a date: `trend_series`/`trend_chart`
(`agent/skills/analysis.py`/`agent/skills/charts.py`) take a `date_axis`/`x_type` param so an ordinal category
(e.g. `bedroom_band`) can drive the x-axis instead of always parsing dates, and `show_actual` independently
toggles whether the faint unsmoothed line renders under the rolling average.

**Value units travel with the number, not the name (s34).** A bare number is ambiguous (82 could be $82, 82
bonds, or 82%), and every renderer used to guess the unit from the *column name* via regex — which broke on
any derived metric, since a trend's value column is literally called `value` and a YoY on `avg_weekly_rent`
is a percentage even though the name still says rent. Units now travel with the number end-to-end instead:
`agent/units.py` (Python) resolves a unit — `currency` | `number` | `percent` — from the manifest's per-metric
`fmt` first, then a derive suffix on the label, then whole-word name matching, falling back to `number` (never
`currency`, since guessing dollars on an unknown name is the worse failure). `unit_for_measure()` composes a
built measure's base aggregation with its `derive`: `growth`/`yoy`/`share` are always percent regardless of
the base metric, and a `wavg` is currency only when the denominator is a count, not another dollar value.
`frontend/src/ui/charts/units.ts` is a mirrored copy for the frontend (currency/general JS can't import the
Python module), checked byte-for-byte against `agent/units.py` by
`services/data-agent/tests/test_registry_sync.py`; `tests/test_explore_agent_sync.py` separately asserts both
copies agree with the manifest's `fmt`. Chart skills (trend/bars/combo/dual-axis/distribution/profile) now
take explicit unit params and stamp them onto the Vega-Lite encoding via `object_builder`, which threads them
through to every rendered object type — including the pivot table, which computes its per-column format at
**runtime** because cross-tab column names are data (e.g. `bonds - 2077`) and the same metric can appear
twice under two different derives, a case no name can resolve (`_dedup_pivot_labels` disambiguates those
same-label-different-derive collisions before they hit `pivot_table`, appending the derive and, if still
colliding, a counter). Frontend chart components (`Trend`/`Bars`/`Combo`/`DataTable`/`KPITile`) read the
declared unit and only fall back to name-guessing for objects saved before units existed. Building the YoY
test case also surfaced a real pre-existing defect: a 12-month YoY trend rendered with zero points, because
the chart window is the latest N months and a 12-month YoY has no prior-year data inside that window —
`_SERIES_LOOKBACK` (`agent/object_builder.py`) widens the fetch by the derive's own lookback (12 months for
`yoy`, 1 for `growth`) so the comparison data is actually in range.

**Pivot cross-tab builder.** A new `pivot` object type (`agent/object_builder.py::_pivot_code`, rendered as an
ordinary `table` `PageObject`) puts one dimension's *values* across the columns instead of down the rows:
`dimension` gives the row columns and `pivot_column` the dimension whose values become column groups — e.g.
rows `bedroom_band, property_type` and `pivot_column = postcode` reads as one row per dwelling with a metric
block per postcode, the shape a long table can't give (comparing two postcodes in a long table means scanning
up and down instead of across). `pivot_measures` (each a base + optional derive, same shape as a bar/line
measure) go through the same `_measure_block` the charts use, so a ratio metric is recomposed as a weighted
average at the pivot's own grain rather than averaged-of-averages. `pivot_compare` (`"diff"` | `"pct_diff"`)
adds a gap column per metric — only when the pivoted dimension has exactly two values, since a gap has no
single definition across more — and `color_by_sign` extends sign-based cell coloring (always on for a diff
column) to every metric column. Column labels are data (a postcode, a derive-qualified metric name appearing
twice), so both the format lookup and the label-collision fix (`_dedup_pivot_labels`, see above) resolve at
runtime rather than off the static spec.

---

## Operations — the /ops flight deck (s32 Track A)

The app was built and deployed before it was *operated*: LLM calls had no retry or
timeout, tokens were counted but never priced, prod ran with no traces, there was
no security testing, and deploys were a blind `apply -auto-approve`. Track A closes
those gaps and routes every result into one place.

**The organising principle: every hardening outcome becomes a row in Postgres, and
the deck reads Postgres.** So the dashboard stays up when Logfire or the AWS APIs
don't, and "is it healthy, safe, fast and affordable?" is one screen rather than a
grep through CloudWatch.

**`/ops`** (`frontend/src/features/ops/OpsPage.tsx`) is an admin-only tab beside
`/evals` — Evaluations answers "is the answer right?", Ops answers "is the service
healthy?". It renders entirely through primitives the app already owns: the Flight
Deck kit (`HudBox`/`Annunciator`/`InstrumentLabel`) for readouts and lamps, and
`report-engine/PageLayout` + `ui/charts/*` for panels, so it inherits theming, the
chart error boundaries and the SQL-link affordance and adds no new object types.

**One read per window (decision Q3).** `GET /admin/ops/summary` serves a
pre-aggregated `app.ops_rollup` row; the heavy `percentile_cont` scan over 3M+
`query_runs` happens in a refresh, never on a request. A cold or stale rollup is
answered immediately with `stale: true` while a background task recomputes, so the
first ever load renders the frame instead of hanging. `POST /admin/ops/refresh`
(admin) and `POST /ops/ingest/rollup` (machine token) force it; `make ops-rollup`
is the scheduler's hook.

**Two metric tiers**, so "complete" stayed shippable. *Tier 1* is everything
computable from Postgres — latency, errors, traffic, product, cost, denials, SLO
burn, data freshness, and Aurora cold-starts counted from the `db_warming` 503s the
frontend reports (the backend can't log those: the database it would log to is the
thing that was asleep). *Tier 2* is one `boto3 GetMetricData` pull for App Runner
CPU/memory, Aurora ACU/connections and CloudFront cache-hit, off by default and
behind `ops_cloudwatch_enabled` — the Terraform flag and the IAM read grant move
together, so the pull can never be enabled without permission. Any Tier-2 failure
renders `saturation: unavailable` and leaves Tier 1 untouched.

**Writes come in through one token-gated endpoint.** `POST /ops/ingest/*`
(`X-Ops-Token`, empty by default = the path is closed) records load tests,
red-team packs, deploys and pipeline runs. The reason is concrete: none of those
writers can reach Aurora — its security group admits the ECS jobs, App Runner's
egress ranges and operator CIDRs, not GitHub Actions runners — and `backend-api`
can. `scripts/ops_ingest.py` is the one client, dependency-free so it runs on a
bare runner, and it exits 0 on failure because telemetry must never fail a deploy.

### The five workstreams

- **W0 · the deck** — migration `0031` (`query_runs` += cache tokens, `cost_usd`,
  `degraded`, `attempts`, `otel_trace_id`, `ttfp_ms`, `status` += `'degraded'`;
  new `load_tests`, `security_runs`, `deploy_events`, `judge_samples`,
  `pipeline_runs`, `ops_rollup`), `app/ops_rollup.py`, `routers/ops.py`, the tab,
  and `frontend/e2e/ops.spec.ts` (admin gating + renders on a cold rollup).

- **W1 · reliability** — `agent/model_factory.py` is now the single retry/timeout
  policy for all **six** LLM call sites (`sandbox_agent`, `object_codegen`,
  `sql_assist`, `skill_codegen`, `titles`, `eval_judge`), which previously had
  none. It retries transport failures and upstream 5xx/429 with full jitter,
  **never a 4xx** (identical failure, real money) and never `UsageLimitExceeded`
  (that guard exists to stop runaway spend; retrying it inverts it) — classified
  by exception *name* down the `__cause__` chain so the module needs no
  provider SDK and is unit-testable in the dependency-light root venv. The
  multi-turn sandbox site passes a `should_retry` veto so a failure *after* work
  was done is salvaged, not replayed at double cost. The backend→agent hop
  retries the same way for connect failures and upstream 5xx/429, but
  deliberately *excludes* read/write/pool timeouts — a timeout means the
  request already reached the agent, whose own retry policy may be mid-flight,
  so retrying at the hop too would stack a second retry cycle on top; on
  exhaustion `/ask` returns a **degraded answer** (`status='degraded'`, a
  plain sentence, a Retry button) instead of a raw 502. `load/k6/chat.js` +
  `make loadtest` produce the first load numbers.

- **W2 · observability & cost** — `agent/pricing.py` prices a run **cache-aware**:
  most input tokens on this workload are prompt-cache hits billed ~10x cheaper, so
  a flat rate overstates spend several-fold, and `cost_usd` is pinned by
  `tests/test_pricing.py` against a known run so a rate change fails CI rather
  than mis-billing quietly. Logfire now instruments `backend-api` too, with W3C
  `traceparent` injected on the agent hop (`agent_client._headers`) so one chat is
  one linked trace, and `query_runs.otel_trace_id` deep-links a slow row on the
  deck to its span waterfall. `LOGFIRE_TOKEN` is finally wired into Terraform
  (prod shipped no traces because nothing ever set it). Two SLOs: availability
  ≥99% of asks served, and p95 **time-to-first-page** ≤3s — the felt latency,
  measured at the first complete `page` frame, deliberately decoupled from
  extract-bound full-answer time (~96s p95 in prod, an eval lever not an infra
  one). Error-budget burn is always computed over 28 days regardless of the
  window shown. The pipeline writes its own freshness to `pipeline_runs` —
  the metric whose absence let prod marts freeze 12 days and take Explore down.
  `docs/runbook.md` is keyed off the deck's lamps.

- **W3 · security** — `security/promptfoo/redteam.yaml` attacks the **product**
  (through `/ask`, as a real user), not a bare model, in four classes that match
  the deck's bars: `rls-bypass`, `jailbreak-to-dml`, `prompt-injection`,
  `pii-exfil`. `tests/security/test_injection.py` is the deterministic,
  zero-LLM, zero-network subset that blocks every merge (43 cases). Writing it
  found two **real** defects in the SQL guard: `SELECT set_config('app.current_
  user_id', …)` was accepted — read-only in form, RLS-context-rewriting in effect,
  and invisible to a node-type check because it parses as an ordinary `Select`
  (the read-only role does *not* stop it; `set_config` needs no write privilege);
  and the keyword denylist scanned string literals, so a query filtering on the
  address `'GRANT ST'` — present in the committed sample — was refused.
  Over-blocking is a defect too: a guard that rejects real queries gets removed.
  Both fixed (`_FORBIDDEN_FUNCTIONS`, `_blank_quoted`) — including a follow-up
  variant where a double-quoted call (`SELECT "set_config"(...)`) slipped the
  same denylist because sqlglot represents its name as an `exp.Identifier`
  rather than a bare string; `_function_name` now unwraps that before
  matching. Chat-path guard
  rejections no longer log as `status='success'`; refusals record `error` +
  `denied` and emit a `security_denied` event, which is the deck's denial counter.
  Questions are length-bounded and PII-scrubbed **on persistence only** (masking
  earlier would change the question the agent answers), with checksum-gated card
  and TFN patterns so prices and postcodes are never mangled. `SECURITY.md` is
  the one-page threat model.

- **W4 · delivery** — `ci.yml` gains a **mypy** job (configured strict since
  Phase 0, never run) and a **Terraform** job: `fmt` + `validate` always, and a
  `plan` commented on the PR when OIDC is reachable, so infrastructure stops being
  applied unseen. `deploy-aws.yml` records every deploy to `deploy_events` at
  start and finish (`if: always()` — a failed deploy is the one the timeline needs)
  with the smoke pass/total. Per **decision Q1** deploys are *recorded, not gated*:
  App Runner has no traffic split and there is no ECS service, so a weighted canary
  would mean ALB+ECS, weeks of work fighting the scale-to-zero cost design —
  instead `make rollback` repoints App Runner at the previous image **digest** (not
  `:latest`, which would redeploy the thing being rolled back). Per **decision Q6**
  the CI deploy role drops `AdministratorAccess` for a scoped policy whose real
  value is the two explicit denies: no IAM users or access keys, and no modifying
  its own role or the OIDC trust. Updating that scoped policy's *own version* is a
  narrow, explicit exception (`AllowSelfPolicyVersionUpdate`, paired with a
  matching `NotResource` carve-out on the deny) so a routine `terraform apply`
  permission change doesn't deadlock behind an out-of-band admin credential —
  every other policy in the account stays denied. `scripts/ops_judge_sample.py`
  scores a slice of live traffic for quality drift — advisory only, since
  without a golden it can judge whether an answer reads well but never whether
  the numbers are right.

### Honest constraints (corrections to the parent plan)

- **App Runner has no traffic split**, and there is no `aws_ecs_service` — hence
  rollback-only (Q1), not canary.
- **The three CloudWatch alarms notify nobody**: their SNS email subscriptions were
  never confirmed. `/ops` is the working substitute — a pull surface, not a page.
- **Cost is cache-dominated**, so pricing had to be too (~⅙ of nominal).
- **The marts are public NSW property records**, so the PII surface is only the
  typed question — which is why Q4 chose regex + Logfire's scrubber.

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
- Styling (s33 decision, supersedes the earlier no-framework doctrine): **Tailwind v4 + shadcn/ui**, migrating
  surface-by-surface. New/converted UI uses shadcn components from `frontend/src/components/ui/` styled by the
  token bridge in `frontend/src/tailwind.css`; that bridge only *aliases* the Flight Deck design tokens in
  `frontend/src/styles.css:9-147`, which remain the single source of truth for color/type (theme still flips
  via `<html data-theme>`, no `.dark` class). Never re-declare legacy token names (`--muted`, `--accent`,
  `--border`, `--radius-*`) — mint only Tailwind-namespaced tokens. `styles.css` stays **unlayered** so it
  wins over Tailwind's layered preflight on unmigrated surfaces; delete a surface's legacy CSS block in the
  same PR that converts it. Icons: `lucide-react` for generic glyphs; brand marks (plane, reticle) stay
  hand-drawn in `frontend/src/ui/icons.tsx`. Fonts are self-hosted via `@fontsource`. Cockpit-brand markup
  (see "The Flight Deck brand (s25)" above) still goes through the `frontend/src/ui/flightdeck.tsx` kit.
  Report-engine cards (`.h-tile` / `.insight-card` / `.chart-card`) stay plain CSS — PNG export must render
  identically outside the app.

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
