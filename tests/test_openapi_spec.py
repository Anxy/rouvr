"""FL-A1: committed OpenAPI spec matches the served app; TS client is present."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_openapi_spec_in_sync(monkeypatch, tmp_path):
    db = tmp_path / "spec.db"
    monkeypatch.setenv("ROUVR_LEDGER_DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("ROUVR_INSECURE_DEV", "1")
    monkeypatch.delenv("ROUVR_LEDGER_TOKEN", raising=False)
    from ledger.app import make_app

    served = make_app(f"sqlite:///{db}").openapi()
    committed = json.loads((ROOT / "ledger" / "openapi.json").read_text())
    assert served == committed, "run: python scripts/export_openapi.py"


def test_openapi_key_paths_and_client_present():
    spec = json.loads((ROOT / "ledger" / "openapi.json").read_text())
    for path in (
        "/health",
        "/v1/tasks",
        "/v1/tasks/{tid}/transition",
        "/v1/knowledge/search",
        "/v1/grants/active",
    ):
        assert path in spec["paths"], path
    assert (ROOT / "web" / "src" / "lib" / "api" / "schema.d.ts").exists()
    assert (ROOT / "web" / "src" / "lib" / "api" / "client.ts").exists()
