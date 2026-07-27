PYTHON ?= python3

.PHONY: install migrate seed run test lint typecheck security verify acceptance acceptance-chapter-03 acceptance-chapter-04 acceptance-chapter-05 acceptance-chapter-06 acceptance-chapter-07 acceptance-chapter-08 acceptance-chapter-09 acceptance-chapter-10 release-evidence observability-up observability-down compose-up compose-down

install:
	$(PYTHON) -m pip install --require-hashes -r requirements-dev.lock
	$(PYTHON) -m pip install --no-deps -e .

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

acceptance:
	./scripts/acceptance-chapter-02.sh

acceptance-chapter-03:
	./scripts/acceptance-chapter-03.sh

acceptance-chapter-04:
	./scripts/acceptance-chapter-04.sh

acceptance-chapter-05:
	./scripts/acceptance-chapter-05.sh

acceptance-chapter-06:
	./scripts/acceptance-chapter-06.sh

acceptance-chapter-07:
	./scripts/acceptance-chapter-07.sh

acceptance-chapter-08:
	./scripts/acceptance-chapter-08.sh

acceptance-chapter-09:
	./scripts/acceptance-chapter-09.sh

acceptance-chapter-10:
	./scripts/acceptance-chapter-10.sh

release-evidence:
	$(PYTHON) -m caseops.release_evidence \
		--source-commit "$$(git rev-parse HEAD)" \
		--source-ref "$$(git describe --tags --exact-match 2>/dev/null || git branch --show-current)"

observability-up:
	docker compose -f compose.yaml -f deploy/compose.observability.yaml up --build -d

observability-down:
	docker compose -f compose.yaml -f deploy/compose.observability.yaml down

compose-up:
	docker compose up --build -d

compose-down:
	docker compose down
