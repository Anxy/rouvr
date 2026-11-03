"""Demo 2 hidden acceptance (test_author, written BEFORE the coder runs).

Run from anywhere:
  TMPDIR=$HOME/dev/rouvr/.data/tmp $HOME/dev/rouvr/.venv/bin/python \\
      -m pytest $HOME/dev/rouvr/e2e/demo2_accept.py -q

Follows the repo's own version-test auth pattern: env first, fresh DB.
"""
import json
import os
import subprocess

import pytest
from fastapi.testclient import TestClient

WT = os.path.expanduser("~/dev/rouvr-demo2-wt")
ADMIN_PW = "accept-pw-0123456789"


@pytest.fixture()
def user_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_AUTH_SECRET", "accept-secret-123456789")
    monkeypatch.setenv("ROUVR_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    import sys
    sys.path.insert(0, WT)
    from ledger.app import make_app
    app = make_app(f"sqlite:///{tmp_path}/v.db")
    reg = TestClient(app)
    headers = {"x-rouvr-token": "stack-secret"}
    reg.post("/v1/tenants", json={"tenant": "demo"}, headers=headers).raise_for_status()
    reg.post("/v1/projects", json={"tenant": "demo", "project": "demo"},
             headers=headers).raise_for_status()
    return TestClient(app)


def _authed(user_client):
    login = user_client.post("/auth/login", data={"username": "admin", "password": ADMIN_PW})
    assert login.status_code == 200, login.text
    return TestClient(user_client.app, cookies=login.cookies)


def test_build_field_on_both_prefixes(user_client):
    c = _authed(user_client)
    r1 = c.get("/v1/version")
    r2 = c.get("/api/v1/version")
    assert r1.status_code == 200 and r2.status_code == 200
    b1, b2 = r1.json(), r2.json()
    assert b1 == b2, f"alias parity broken: {b1} != {b2}"
    assert isinstance(b1.get("version"), str) and b1["version"], "version missing/empty"
    assert isinstance(b1.get("build"), str) and b1["build"], "build missing/empty"


def test_build_matches_worktree_head_or_dev(user_client):
    c = _authed(user_client)
    build = c.get("/v1/version").json()["build"]
    try:
        sha = subprocess.run(
            ["git", "-C", WT, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        sha = ""
    expected = sha if sha else "dev"
    assert build == expected, f"build={build!r} expected {expected!r}"


def test_snapshots_carry_version_paths():
    raw = open(f"{WT}/ledger/openapi.json").read()
    spec = json.loads(raw)
    assert "/v1/version" in spec["paths"], "openapi.json missing /v1/version after regen"
    dts = open(f"{WT}/web/src/lib/api/schema.d.ts").read()
    assert "/v1/version" in dts, "schema.d.ts missing /v1/version (regen skipped?)"
