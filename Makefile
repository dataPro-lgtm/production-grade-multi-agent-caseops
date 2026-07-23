PYTHON ?= python3

.PHONY: install migrate seed run test lint typecheck security verify compose-up compose-down

install:
	$(PYTHON) -m pip install -e ".[dev]"

migrate:
	$(PYTHON) -m alembic upgrade head

seed:
	$(PYTHON) -m caseops seed

run:
	$(PYTHON) -m uvicorn caseops.api.app:app --host 127.0.0.1 --port 8080 --reload

test:
	$(PYTHON) -m pytest --cov=caseops --cov-report=term-missing --cov-report=xml

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

typecheck:
	$(PYTHON) -m mypy

security:
	$(PYTHON) -m bandit -q -r src
	$(PYTHON) -m pip_audit

verify: lint typecheck test

compose-up:
	docker compose up --build -d

compose-down:
	docker compose down
