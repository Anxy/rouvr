"""Demo 2 reviewer probe: /v1/version must not shell out to git per request.

In containers without .git every request would pay a failing subprocess
spawn. BUILD must be resolved once (import/startup), with "dev" fallback.
Run: TMPDIR=$HOME/dev/rouvr/.data/tmp $HOME/dev/rouvr/.venv/bin/python \\
        -m pytest $HOME/dev/rouvr/e2e/demo2_probe_nosubprocess.py -q
"""
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

WT = os.path.expanduser("~/dev/rouvr-demo2-wt")
sys.path.insert(0, WT)
ADMIN_PW = "probe-pw-0123456789"


@pytest.fixture()
def user_client(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_AUTH_SECRET", "probe-secret-12345678901234567890")
    monkeypatch.setenv("ROUVR_ADMIN_PASSWORD", ADMIN_PW)
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    from ledger.app import make_app
    app = make_app(f"sqlite:///{tmp_path}/p.db")
    reg = TestClient(app)
    headers = {"x-rouvr-token": "stack-secret"}
    reg.post("/v1/tenants", json={"tenant": "demo"}, headers=headers).raise_for_status()
    reg.post("/v1/projects", json={"tenant": "demo", "project": "demo"},
             headers=headers).raise_for_status()
    login = TestClient(app).post("/auth/login", data={"username": "admin", "password": ADMIN_PW})
    assert login.status_code == 200, login.text
    return TestClient(app, cookies=login.cookies)


def test_no_git_subprocess_per_request(user_client, monkeypatch):
    calls = []
    real_run = subprocess.run

    def counting(*a, **k):
        if a and "rev-parse" in str(a[0]):
            calls.append(a[0])
        return real_run(*a, **k)

    monkeypatch.setattr(subprocess, "run", counting)
    assert user_client.get("/v1/version").status_code == 200  # warmup
    del calls[:]
    for _ in range(3):
        r = user_client.get("/v1/version")
        assert r.status_code == 200, r.text
    assert not calls, f"git rev-parse spawned per request ({len(calls)} calls)"
