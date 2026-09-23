# Ease - common commands. On Windows use Git Bash (make is bundled with many setups) or copy the commands.
PY ?= .venv/Scripts/python
ifeq ($(OS),)
PY = .venv/bin/python
endif

.PHONY: up down logs ps test test-integration lint migrate run-local ops

up:            ## start the whole stack (builds images on first run)
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100 api worker-agent worker-io

ps:
	docker compose ps

ops:           ## Flower dashboard on http://127.0.0.1:5555
	docker compose --profile ops up -d flower

migrate:
	docker compose run --rm migrate

test:
	cd backend && ../$(PY) -m pytest -q

test-integration:
	docker compose up -d postgres redis
	cd backend && ../$(PY) -m pytest -q -m integration tests/integration

lint:
	cd backend && ../$(PY) -m ruff check ease tests

run-local:     ## run one task in the terminal without the web stack: make run-local P="your goal"
	cd backend && ../$(PY) -m ease.graph.run --prompt "$(P)"
