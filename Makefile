PY ?= python3
VENV ?= .venv
export PYTHONPATH := .:core:core/src:core/libs

setup:
	$(PY) -m venv $(VENV) && $(VENV)/bin/pip install -r requirements.txt
up:
	docker compose -f demo/docker-compose.yml up -d
api:
	$(VENV)/bin/uvicorn backend.main:app --reload --port 8000
ledger:
	$(VENV)/bin/uvicorn ledger.app:app --port 8221
demo:
	$(VENV)/bin/python demo/morning_report.py
test:
	$(VENV)/bin/pytest -q
# Fast, self-contained checks for the public-cut pieces (no Postgres/Temporal needed).
test-public:
	$(VENV)/bin/pytest -q ledger demo lane/tests workers cli tests core/backend/tests/test_rouvr_model_providers.py
.PHONY: setup up api ledger demo test test-public
