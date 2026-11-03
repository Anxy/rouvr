"""Version endpoint: /v1/version and its Lite alias /api/v1/version."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_AUTH_SECRET", "test-secret-for-version-endpoint-123456789")
    monkeypatch.setenv("ROUVR_ADMIN_PASSWORD", "admin-test-password-0123456789")
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    from ledger.app import make_app

    app = make_app(f"sqlite:///{tmp_path}/v.db")
    reg = TestClient(app)
    headers = {"x-rouvr-token": "stack-secret"}
    reg.post("/v1/tenants", json={"tenant": "demo"}, headers=headers).raise_for_status()
    reg.post("/v1/projects", json={"tenant": "demo", "project": "demo"},
             headers=headers).raise_for_status()
    return app


@pytest.fixture()
def user_client(app):
    return TestClient(app)


def _login(user_client, username="admin", password="admin-test-password-0123456789"):
    return user_client.post("/auth/login", data={"username": username, "password": password})


def test_version_on_both_prefixes(user_client):
    login = _login(user_client)
    assert login.status_code == 200, login.text
    c = TestClient(user_client.app, cookies=login.cookies)
    r1 = c.get("/v1/version")
    assert r1.status_code == 200, (r1.status_code, r1.text)
    r2 = c.get("/api/v1/version")
    assert r2.status_code == 200, (r2.status_code, r2.text)
    assert r1.json() == r2.json()
    assert isinstance(r1.json().get("version"), str) and r1.json()["version"]
