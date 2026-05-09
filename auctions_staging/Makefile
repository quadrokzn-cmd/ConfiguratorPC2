.PHONY: up down restart build migrate test logs ps shell psql

up:
	docker compose up -d --build

down:
	docker compose down

restart:
	docker compose restart app

build:
	docker compose build

migrate:
	docker compose exec -T app python scripts/migrate.py

test:
	docker compose exec -T app pytest -q || true

logs:
	docker compose logs -f --tail=200 app

ps:
	docker compose ps

shell:
	docker compose exec app bash

psql:
	docker compose exec postgres psql -U quadrotech -d quadrotech
