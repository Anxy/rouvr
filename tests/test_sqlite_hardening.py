"""FL-A5: SQLite hardening — WAL + busy_timeout + single-writer discipline."""
import os
import sqlite3
import threading
import time

import httpx
import uvicorn
from sqlalchemy import text


def _env_no_auth():
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)


def test_sqlite_pragmas(tmp_path):
    from ledger.app import make_app
    _env_no_auth()
    db = tmp_path / "h.db"
    app = make_app(f"sqlite:///{db}")
    with app.state.engine.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert c.execute(text("PRAGMA busy_timeout")).scalar() == 5000
    seen = sqlite3.connect(db).execute("PRAGMA journal_mode").fetchone()[0]
    assert seen.lower() == "wal"


def _wait_ready(base_url, attempts=100):
    ready = False
    for _ in range(attempts):
        try:
            ready = httpx.get(f"{base_url}/health", timeout=1).status_code == 200
        except httpx.TransportError:
            time.sleep(0.1)
        if ready:
            break
    assert ready


def test_concurrent_evidence_writes_have_zero_lock_errors(tmp_path):
    from ledger.app import make_app
    _env_no_auth()
    db = tmp_path / "c.db"
    port = 18243
    base_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(make_app(f"sqlite:///{db}"), host="127.0.0.1",
                                           port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    _wait_ready(base_url)
    try:
        with httpx.Client(base_url=base_url, timeout=15) as c:
            c.post("/v1/tenants", json={"tenant": "acme"}).raise_for_status()
            c.post("/v1/projects", json={"tenant": "acme", "project": "site"}).raise_for_status()
            tid = c.post("/v1/tasks", json={"title": "t", "intent": "x",
                                            "tenant": "acme", "project": "site"}).json()["id"]
        errors = []

        def write_batch(role, start, count):
            try:
                with httpx.Client(base_url=base_url, timeout=30) as c:
                    for i in range(start, start + count):
                        r = c.post(f"/v1/tasks/{tid}/evidence",
                                   json={"kind": f"{role}-progress",
                                         "body": {"fence": f"{role}-{i}",
                                                  "payload": {"n": i}}})
                        assert r.status_code == 200, r.text
            except (httpx.TransportError, AssertionError) as e:
                errors.append(str(e))

        roles = ["api", "worker"] * 4
        threads = [threading.Thread(target=write_batch, args=(role, n * 12, 12))
                   for n, role in enumerate(roles)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        locked = [e for e in errors if "database is locked" in e.lower()]
        assert locked == []
        assert errors == []
        with httpx.Client(base_url=base_url, timeout=15) as c:
            got = c.get(f"/v1/tasks/{tid}").json()
        assert len(got["evidence"]) == len(threads) * 12
    finally:
        server.should_exit = True
