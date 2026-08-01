.PHONY: help up down reset logs ps samples migrate mcp-test mcp-smoke pipeline pipeline-full pipeline-docs smoke e2e e2e-chat e2e-ops eval eval-diagnose eval-export eval-import eval-compare eval-pack-version loadtest redteam injection-suite ops-rollup rollback

help:
	@echo "make samples       - (re)generate the small committed sample CSVs from the full data/"
	@echo "make up            - build + start the whole stack (db, migrate, pipeline, api, agent, web)"
	@echo "make migrate       - run the Alembic migration job on its own (against a running db)"
	@echo "make pipeline      - run the dlt + dbt pipeline on the SAMPLE data (against a running db)"
	@echo "make pipeline-full - run the pipeline on the FULL data/ CSVs (516MB/63MB — slower)"
	@echo "make pipeline-docs - serve the dbt docs UI (lineage, raw->staging->marts) at localhost:8180"
	@echo "make down          - stop the stack"
	@echo "make reset         - stop and wipe the database volume (re-runs migrations on next up)"
	@echo "make logs          - tail logs"
	@echo "make smoke         - run the end-to-end smoke test against a running stack"
	@echo ""
	@echo "make eval-export   - golden examples: DB -> evals/cases/*.yaml (review in a PR)"
	@echo "make eval-import   - golden examples: evals/cases/*.yaml -> DB (seeds any env)"
	@echo "make eval          - score the golden pack against the running agent"
	@echo "make eval-compare  - base vs experiment, with the regression gate"
	@echo "make eval-diagnose - failure clusters + one-lever hypotheses (read-only)"
	@echo "make eval-pack-version - print the content hash of the golden pack"
	@echo ""
	@echo "make mcp-test      - MCP protocol conformance (deterministic, no LLM)"
	@echo "make mcp-smoke     - drive a REAL Claude client through the MCP server"
	@echo ""
	@echo "make loadtest      - k6 load test -> app.load_tests (SCENARIO=browse|chat)"
	@echo "make redteam       - promptfoo red-team the governed boundary -> app.security_runs"
	@echo "make injection-suite - the deterministic, zero-LLM guard tests (also runs in CI)"
	@echo "make ops-rollup    - recompute the /ops deck's windows now"
	@echo "make rollback      - revert App Runner to the previous image digest (prod)"
	@echo ""
	@echo "Then open http://localhost:5230 and sign in as admin / user1 / user2."

samples:
	python3 scripts/make_samples.py

migrate:
	docker compose run --rm --build migrate

pipeline:
	docker compose run --rm --build pipeline

pipeline-full:
	docker compose run --rm --build -e PIPELINE_SOURCE=full pipeline

pipeline-docs:
	@echo "dbt docs UI: http://localhost:8180 (Ctrl+C to stop). Run 'make pipeline' first so target/ is fresh."
	docker compose --profile docs run --rm --build --service-ports pipeline-docs

up:
	docker compose up --build

down:
	docker compose down

reset:
	docker compose down -v

logs:
	docker compose logs -f

ps:
	docker compose ps

smoke:
	python3 scripts/smoke_test.py

# Golden examples move between the database and the version-controlled pack
# (s24). The repo is the source of truth; the DB is a working surface.
eval-export:
	uv run python scripts/eval_pack.py export

eval-import:
	uv run python scripts/eval_pack.py import

eval-pack-version:
	@uv run python scripts/eval_pack.py version

# Score the pack against the running agent. Narrow with DATASET/TIER/CASE — the
# runner works down to a single golden, which is the inner diagnose->fix loop.
#   make eval                          make eval DATASET=nsw_rent
#   make eval CASE=nsw_rent-give-...   make eval EXPERIMENT=fewer-turns
#   make eval INCLUDE_DRAFTS=1         # also score draft goldens
eval:
	uv run python scripts/eval_run.py \
	  $(if $(DATASET),--dataset $(DATASET)) $(if $(TIER),--tier $(TIER)) \
	  $(if $(CASE),--case $(CASE)) $(if $(EXPERIMENT),--experiment $(EXPERIMENT)) \
	  $(if $(HYPOTHESIS),--hypothesis "$(HYPOTHESIS)") $(if $(NO_JUDGE),--no-judge) \
	  $(if $(INCLUDE_DRAFTS),--include-drafts)

# Base vs experiment, with the regression gate.
#   make eval-compare A=<run-id> B=<run-id>
# Read-only diagnosis over a scored run: failure clusters + one-lever
# hypotheses. Proposes; never writes (decision D-3).
#   make eval-diagnose            make eval-diagnose RUN=<run-id>
eval-diagnose:
	uv run python scripts/eval_diagnose.py $(RUN)

eval-compare:
	uv run python scripts/eval_compare.py --base $(A) --candidate $(B)

# Playwright E2E against a running stack: Template Studio + playground matrix.
e2e:
	cd frontend && npm run e2e:studio

# The slow live-LLM chat answer E2E (agent answers a real question).
e2e-chat:
	cd frontend && npm run e2e:chat

# The Ops flight deck E2E: admin gating + the deck renders on a cold rollup.
e2e-ops:
	cd frontend && npx playwright test ops

# ---------------------------------------------------------------------------
# MCP server (s35 rung 3) — two tiers, deliberately
# ---------------------------------------------------------------------------
# mcp-test is deterministic protocol conformance: it connects a real MCP client,
# checks the tool surface and that the guardrails still refuse a write. No LLM,
# so it can gate every merge, and it catches a renamed or malformed tool before
# a model ever sees one.
mcp-test:
	uv run --directory services/mcp-server pytest tests -q

# mcp-smoke drives a REAL Claude client and asserts it actually invoked an
# mcp__datapilot__* tool BEFORE trusting any figure in the answer. That ordering
# is the whole test: Claude knows roughly what Sydney property costs, so pointed
# at a dead server it produces a plausible number from memory and a naive check
# goes green. Verified to exit 1 against an unreachable server.
#   make mcp-smoke EXPECT=15217          make mcp-smoke MCP_URL=https://<host>/mcp
mcp-smoke:
	uv run python scripts/mcp_smoke.py $(if $(EXPECT),--expect $(EXPECT)) $(if $(MCP_URL),--url $(MCP_URL))

# ---------------------------------------------------------------------------
# Operations (s32 Track A)
# ---------------------------------------------------------------------------

# k6 load test against a running stack, recorded into app.load_tests so the ops
# deck's load tile shows a real measurement instead of a number from a README.
# k6 is NOT a repo dependency — install it (brew install k6) before running.
#   make loadtest                             browse, 20 VUs, 30s, local
#   make loadtest SCENARIO=chat VUS=3 DURATION=60s
#   make loadtest BASE_URL=https://<api> TOKEN=<bearer>
SCENARIO ?= browse
VUS ?= 20
DURATION ?= 30s
BASE_URL ?= http://localhost:8000
loadtest:
	@command -v k6 >/dev/null || { echo "k6 not installed (brew install k6)"; exit 1; }
	mkdir -p load/out
	BASE_URL=$(BASE_URL) SCENARIO=$(SCENARIO) VUS=$(VUS) DURATION=$(DURATION) \
	  TOKEN=$${TOKEN:-} k6 run --summary-export load/out/summary.json load/k6/chat.js
	uv run python scripts/ops_ingest.py load-test --k6-summary load/out/summary.json \
	  --scenario $(SCENARIO) --vus $(VUS) --duration-s $$(python3 -c \
	  "import re,sys; s='$(DURATION)'; m=re.match(r'(\d+)([smh]?)',s); n=int(m.group(1)); \
	   print(n*{'':1,'s':1,'m':60,'h':3600}[m.group(2)])")

# promptfoo red-team of the governed boundary (RLS bypass, jailbreak to DML,
# prompt injection, PII exfil). Costs model tokens and needs a running stack, so
# it is a deliberate run, not a CI gate — the deterministic subset below is the
# gate. promptfoo comes from the sibling workspace / npx.
redteam:
	cd security/promptfoo && npx -y promptfoo@latest eval -c redteam.yaml \
	  --output ../out/redteam.json --no-cache
	uv run python scripts/ops_ingest.py security-run --kind redteam \
	  --promptfoo-json security/out/redteam.json \
	  --pack-sha $$(uv run python -c \
	  "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('security/promptfoo/redteam.yaml').read_bytes()).hexdigest()[:12])")

# The deterministic half: no LLM, no network, so it blocks every merge.
injection-suite:
	uv run pytest tests/security -q

# Recompute the ops deck's windowed aggregates now (the scheduler's hook).
ops-rollup:
	uv run python scripts/ops_ingest.py rollup

# Revert App Runner to the previously deployed image tag (s32 W4, decision Q1).
#   make rollback                 # both services, to the prior digest
#   make rollback SERVICE=backend-api
rollback:
	./scripts/rollback_apprunner.sh $${SERVICE:-all}
