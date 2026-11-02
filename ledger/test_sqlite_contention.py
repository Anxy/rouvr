"""FL-A5 SQLite hardening: observable config plus API/worker contention.

The ledger serves API evidence writes while a worker writes evidence rows
through its own SQLite connection. With WAL, a 5s busy timeout and the
single-writer pool, concurrent writers queue instead of failing.
"""

import json
import os
import sqlite3
import threading
import time

import httpx
import uvicorn
from sqlalchemy import text


def _env():
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)


def test_hardened_config_visible_on_app_state(tmp_path):
    from ledger.app import make_app

    _env()
    app = make_app(f"sqlite:///{tmp_path}/hard.db")
    with app.state.engine.connect() as c:
        mode = c.execute(text("PRAGMA journal_mode")).scalar()
        timeout = c.execute(text("PRAGMA busy_timeout")).scalar()
    assert str(mode).lower() == "wal"
    assert int(timeout) == 5000


def test_api_and_worker_evidence_contention(tmp_path):
    from ledger.app import make_app

    _env()
    db = tmp_path / "contend.db"
    port = 18242
    base = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(
        make_app(f"sqlite:///{db}"), host="127.0.0.1", port=port,
        log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    ready = False
    deadline = time.time() + 20
    while time.time() < deadline and not ready:
        try:
            ready = httpx.get(f"{base}/health", timeout=1).status_code == 200
        except httpx.TransportError:
            ready = False
        if not ready:
            time.sleep(0.1)
    assert ready
    try:
        with httpx.Client(base_url=base, timeout=30) as setup:
            setup.post("/v1/tenants", json={"tenant": "acme"}).raise_for_status()
            setup.post("/v1/projects",
                       json={"tenant": "acme", "project": "site"}).raise_for_status()
            created = setup.post("/v1/tasks", json={"title": "t", "intent": "x",
                                                   "tenant": "acme",
                                                   "project": "site"}).json()
            tid = created["id"]

        errors: list[str] = []
        guard = threading.Lock()

        def record(msg: str):
            with guard:
                errors.append(msg)

        def api_writer(tag: str, count: int):
            with httpx.Client(base_url=base, timeout=30) as client:
                for i in range(count):
                    payload = {"kind": "note", "body": {"tag": tag, "i": i}}
                    try:
                        resp = client.post(f"/v1/tasks/{tid}/evidence",
                                           json=payload)
                        resp.raise_for_status()
                    except (httpx.TransportError, httpx.HTTPStatusError) as e:
                        record(f"api:{tag}:{i}:{e}")

        def worker_writer(tag: str, count: int):
            con = sqlite3.connect(str(db), timeout=5.0)
            try:
                con.execute("PRAGMA busy_timeout=5000")
                for i in range(count):
                    row = (tid, "note", json.dumps({"tag": tag, "i": i}),
                           "x", "2026-01-01")
                    try:
                        con.execute("INSERT INTO evidence(task_id,kind,body,"
                                    "sha256,created_at) VALUES (?,?,?,?,?)", row)
                        con.commit()
                    except sqlite3.OperationalError as e:
                        record(f"worker:{tag}:{i}:{e}")
            finally:
                con.close()

        threads = []
        for n in range(4):
            threads.append(threading.Thread(target=api_writer,
                                            args=(f"api{n}", 25)))
        for n in range(2):
            threads.append(threading.Thread(target=worker_writer,
                                            args=(f"db{n}", 25)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        locked = [e for e in errors if "locked" in e]
        assert locked == []
        assert errors == []
        con = sqlite3.connect(str(db))
        try:
            total = con.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        finally:
            con.close()
        assert total == 150
    finally:
        server.should_exit = True
