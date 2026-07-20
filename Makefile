.PHONY: help up down reset logs ps samples migrate pipeline pipeline-full pipeline-docs smoke e2e e2e-chat eval eval-diagnose eval-export eval-import eval-compare eval-pack-version

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
eval:
	uv run python scripts/eval_run.py \
	  $(if $(DATASET),--dataset $(DATASET)) $(if $(TIER),--tier $(TIER)) \
	  $(if $(CASE),--case $(CASE)) $(if $(EXPERIMENT),--experiment $(EXPERIMENT)) \
	  $(if $(HYPOTHESIS),--hypothesis "$(HYPOTHESIS)") $(if $(NO_JUDGE),--no-judge)

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
