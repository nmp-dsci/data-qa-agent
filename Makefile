.PHONY: help up down reset logs ps data smoke

help:
	@echo "make data    - (re)generate the sample housing.csv"
	@echo "make up      - build + start the whole stack (db, backend-api, data-agent, frontend)"
	@echo "make down    - stop the stack"
	@echo "make reset   - stop and wipe the database volume (re-runs seed + load on next up)"
	@echo "make logs    - tail logs"
	@echo "make smoke   - run the end-to-end smoke test against a running stack"
	@echo ""
	@echo "Then open http://localhost:5230 and sign in as admin / user1 / user2."

data:
	python3 scripts/generate_housing.py 300

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
