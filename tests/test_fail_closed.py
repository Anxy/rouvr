"""Fail-closed auth: no open-dev mode. Every /v1 without a valid token 401s."""
import pytest
from fastapi.testclient import TestClient


def _app_no_token(tmp_path, monkeypatch, **env):
    monkeypatch.delenv("ROUVR_LEDGER_TOKEN", raising=False)
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from ledger.app import make_app
    return make_app(f"sqlite:///{tmp_path}/f.db")


def test_no_token_401s(tmp_path, monkeypatch):
    app = _app_no_token(tmp_path, monkeypatch)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/v1/tasks").status_code == 401
        assert c.get("/v1/knowledge/search", params={"q": "x"}).status_code == 401
        assert c.post("/v1/tenants", json={"tenant": "a"}).status_code == 401
        assert c.get("/health").status_code == 200


def test_bad_token_401s(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "sekret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    from ledger.app import make_app
    app = make_app(f"sqlite:///{tmp_path}/b.db")
    with TestClient(app) as c:
        assert c.get("/v1/tasks").status_code == 401
        assert c.get("/v1/tasks", headers={"X-Rouvr-Token": "wrong"}).status_code == 401
        ok = c.get("/v1/tasks", headers={"X-Rouvr-Token": "sekret"})
        assert ok.status_code == 200


def test_insecure_bypass_allows_local(tmp_path, monkeypatch, capsys):
    app = _app_no_token(tmp_path, monkeypatch, ROUVR_INSECURE_DEV="1")
    with TestClient(app) as c:
        assert c.get("/v1/tasks").status_code == 200
    assert "ROUVR_INSECURE_DEV" in capsys.readouterr().err


def test_insecure_refused_on_public_bind(tmp_path, monkeypatch):
    monkeypatch.delenv("ROUVR_LEDGER_TOKEN", raising=False)
    monkeypatch.setenv("ROUVR_INSECURE_DEV", "1")
    monkeypatch.setenv("ROUVR_BIND_HOST", "0.0.0.0")
    from ledger.app import make_app
    with pytest.raises(RuntimeError, match="public bind"):
        make_app(f"sqlite:///{tmp_path}/p.db")
