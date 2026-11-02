"""FL-A1: FastAPI serves OpenAPI; the typed TS client tracks it.

- /openapi.json is served and covers the ledger routes.
- web/src/lib/api/openapi.json matches the live app's operation index
  (regen with `pnpm --filter web gen:api` after route changes).
- The generated client exists and is the only place using fetch.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi.testclient import TestClient

from ledger.app import make_app

ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "web" / "src" / "lib" / "api"

_FETCH_RE = re.compile(r"(?<![\w$.])fetch\s*\(")


def _client(tmp_path):
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    return TestClient(make_app(f"sqlite:///{tmp_path}/l.db"))


def test_openapi_served_with_ledger_routes(tmp_path):
    c = _client(tmp_path)
    r = c.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["openapi"].startswith("3.")
    paths = spec["paths"]
    for p in (
        "/health",
        "/v1/tasks",
        "/v1/tasks/{tid}/transition",
        "/v1/knowledge/search",
        "/v1/grants/active",
    ):
        assert p in paths, p
    assert set(paths["/v1/tasks"]) >= {"get", "post"}


def _op_index(spec: dict) -> dict:
    """path -> method -> operationId + sorted parameter names (stable across
    cosmetic FastAPI generator changes)."""
    out = {}
    for path, item in spec["paths"].items():
        out[path] = {}
        for method, op in item.items():
            if method in ("parameters", "summary", "description", "servers"):
                continue
            params = sorted(
                f"{p.get('in')}:{p.get('name')}" for p in op.get("parameters", [])
            )
            out[path][method] = (op.get("operationId"), params)
    return out


def test_committed_spec_matches_live_app(tmp_path):
    live = make_app(f"sqlite:///{tmp_path}/l.db").openapi()
    committed = json.loads((API_DIR / "openapi.json").read_text())
    assert committed["openapi"] == live["openapi"]
    assert _op_index(committed) == _op_index(live)


def test_generated_client_is_only_fetch_user():
    schema = API_DIR / "schema.d.ts"
    client = API_DIR / "client.ts"
    assert schema.exists() and "export interface paths" in schema.read_text()
    assert client.exists() and "export async function apiFetch" in client.read_text()
    offenders = []
    for f in sorted((ROOT / "web" / "src").rglob("*.ts*")):
        if API_DIR in f.parents:
            continue
        if _FETCH_RE.search(f.read_text()):
            offenders.append(str(f.relative_to(ROOT)))
    assert offenders == [], f"raw fetch outside generated client: {offenders}"
