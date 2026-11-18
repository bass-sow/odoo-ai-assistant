PY ?= .venv/bin/python

.PHONY: install test lint ingest run evals docker aws-deploy

install:
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev,aws,pgvector]"

test:
	$(PY) -m pytest -q

lint:
	.venv/bin/ruff check .

ingest:
	$(PY) -m app.rag.ingest

run:
	.venv/bin/uvicorn app.main:app --reload

evals:
	$(PY) -m evals.run retrieval
	$(PY) -m evals.run extraction
	$(PY) -m evals.run agent

docker:
	docker compose up --build

aws-deploy:
	./scripts/deploy_aws.sh
