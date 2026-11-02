"""FL-A1: FastAPI serves OpenAPI; the committed TS client matches it."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "web" / "src" / "lib" / "api"


def _live_spec_dict(tmp_path):
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    from ledger.app import make_app

    return make_app(f"sqlite:///{tmp_path}/o.db").openapi()


def test_fastapi_serves_openapi(tmp_path):
    from fastapi.testclient import TestClient
    from ledger.app import make_app

    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    c = TestClient(make_app(f"sqlite:///{tmp_path}/o.db"))
    r = c.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert "/health" in spec["paths"]
    assert "/v1/tasks" in spec["paths"]
    assert "/v1/knowledge/search" in spec["paths"]


def test_committed_snapshot_matches_live_spec(tmp_path):
    live = json.dumps(_live_spec_dict(tmp_path), indent=2, sort_keys=True) + "\n"
    assert (API_DIR / "openapi.json").read_text() == live


def test_generated_client_covers_spec_paths():
    schema = (API_DIR / "schema.d.ts").read_text()
    for p in ("/health", "/v1/tasks", "/v1/knowledge/search",
              "/v1/tasks/{tid}/transition", "/v1/grants/active"):
        assert p in schema, p
