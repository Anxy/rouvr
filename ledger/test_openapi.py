"""A1: FastAPI serves a versioned OpenAPI spec; the committed copy stays in sync.

The web typed client (`web/src/lib/api/`) is generated from
`ledger/openapi.json` via `scripts/export_openapi.py` + `pnpm --filter web gen:api`.
"""
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from ledger.app import make_app


def _client(tmp_path):
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    return TestClient(make_app(f"sqlite:///{tmp_path}/l.db"))


def test_openapi_serves_versioned_spec(tmp_path):
    spec = _client(tmp_path).get("/openapi.json").json()
    assert spec["info"]["title"] == "Rouvr ledger"
    assert spec["info"]["version"] == "1.0.0"
    for path in ("/health", "/v1/tasks", "/v1/knowledge/search", "/v1/grants/active"):
        assert path in spec["paths"], path


def test_committed_openapi_spec_in_sync(tmp_path):
    served = _client(tmp_path).get("/openapi.json").json()
    committed = json.loads(Path(__file__).with_name("openapi.json").read_text())
    assert served == committed
