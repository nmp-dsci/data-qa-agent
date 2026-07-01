.PHONY: help up down reset logs ps samples migrate pipeline pipeline-full smoke

help:
	@echo "make samples       - (re)generate the small committed sample CSVs from the full data/"
	@echo "make up            - build + start the whole stack (db, migrate, pipeline, api, agent, web)"
	@echo "make migrate       - run the Alembic migration job on its own (against a running db)"
	@echo "make pipeline      - run the dlt + dbt pipeline on the SAMPLE data (against a running db)"
	@echo "make pipeline-full - run the pipeline on the FULL data/ CSVs (516MB/63MB — slower)"
	@echo "make down          - stop the stack"
	@echo "make reset         - stop and wipe the database volume (re-runs migrations on next up)"
	@echo "make logs          - tail logs"
	@echo "make smoke         - run the end-to-end smoke test against a running stack"
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
