# Sorted — developer Makefile
#
# Shortcuts for common dev workflows. Tab-indented (Make requires it).

.PHONY: help install run test test-watch lint format seed migrate db-up db-down clean

help:
	@echo "Sorted — common commands:"
	@echo ""
	@echo "  make install      - install dependencies in venv"
	@echo "  make run          - run dev server on :5000"
	@echo "  make test         - run pytest"
	@echo "  make test-watch   - re-run tests on change"
	@echo "  make lint         - check code with ruff"
	@echo "  make format       - auto-format with ruff"
	@echo "  make seed         - create demo trade + services"
	@echo "  make migrate      - generate + apply migrations"
	@echo "  make db-up        - start local Postgres in docker"
	@echo "  make db-down      - stop local Postgres"
	@echo "  make clean        - remove caches and build artefacts"
	@echo ""

install:
	python3 -m venv .venv 2>/dev/null || true
	.venv/bin/pip install -e ".[dev]"

run:
	FLASK_ENV=development FLASK_APP=wsgi.py .venv/bin/flask run --debug

test:
	.venv/bin/pytest -x -v

test-watch:
	.venv/bin/pytest-watch -- -x

lint:
	.venv/bin/ruff check app tests

format:
	.venv/bin/ruff format app tests
	.venv/bin/ruff check --fix app tests

seed:
	.venv/bin/flask seed-demo

migrate:
	.venv/bin/flask db migrate -m "auto migration"
	.venv/bin/flask db upgrade

db-up:
	docker run --rm -d --name sorted-pg \
		-e POSTGRES_PASSWORD=dev \
		-e POSTGRES_DB=sorted \
		-p 5432:5432 \
		postgres:16
	@echo "Waiting for Postgres..."
	@sleep 3
	@echo "Postgres up at localhost:5432 (db=sorted, user=postgres, pass=dev)"

db-down:
	docker stop sorted-pg 2>/dev/null || true

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist *.egg-info
