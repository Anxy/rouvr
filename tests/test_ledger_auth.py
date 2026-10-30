"""S011: ledger requires the shared token when ROUVR_LEDGER_TOKEN is set."""
import httpx
import pytest


@pytest.fixture()
def protected_url(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "sekret")
    import importlib
    import ledger.app as la
    importlib.reload(la)
    import threading, time, uvicorn
    config = uvicorn.Config(la.make_app(f"sqlite:///{tmp_path}/a.db"),
                            host="127.0.0.1", port=18231, log_level="error")
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True); th.start()
    for _ in range(100):
        try:
            if httpx.get("http://127.0.0.1:18231/health", timeout=1).status_code == 200: break
        except httpx.TransportError: pass
        time.sleep(0.1)
    yield "http://127.0.0.1:18231"
    server.should_exit = True
    monkeypatch.delenv("ROUVR_LEDGER_TOKEN")
    importlib.reload(la)


def test_ledger_auth_required(protected_url):
    assert httpx.get(protected_url + "/v1/tasks", timeout=5).status_code == 401
    assert httpx.get(protected_url + "/v1/tasks", timeout=5,
                     headers={"X-Rouvr-Token": "wrong"}).status_code == 401
    ok = httpx.get(protected_url + "/v1/tasks", timeout=5,
                   headers={"X-Rouvr-Token": "sekret"})
    assert ok.status_code == 200 and ok.json()["items"] == []
    assert httpx.get(protected_url + "/health", timeout=5).status_code == 200
