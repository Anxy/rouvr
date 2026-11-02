"""FL-A5: SQLite hardening — WAL + busy_timeout + single-writer queue.

Contention test: API clients and a sidecar worker (own DB connection)
write evidence concurrently; zero "database is locked" failures.
"""
import os
import socket
import sqlite3
import threading
import time

import httpx
import uvicorn
from sqlalchemy import text


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve(app, port: int):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
        except httpx.TransportError:
            time.sleep(0.1)
        else:
            if resp.status_code == 200:
                return server
            time.sleep(0.1)
    raise RuntimeError("ledger server did not start")


def test_sqlite_pragmas_and_single_writer(tmp_path):
    import ledger.app as appmod

    from ledger.app import SQLITE_BUSY_TIMEOUT_MS, make_app
    assert SQLITE_BUSY_TIMEOUT_MS == 5000
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    app = make_app(f"sqlite:///{tmp_path}/h.db")
    assert appmod.SQLITE_BUSY_TIMEOUT_MS == 5000
    with app.state.engine.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert c.execute(text("PRAGMA busy_timeout")).scalar() == 5000
    # single-writer discipline: one pooled connection per DB, API + workers
    # in this process share its queue instead of racing the file.
    assert type(app.state.engine.pool).__name__ == "QueuePool"
    assert app.state.engine.pool.size() == 1
    raw = sqlite3.connect(tmp_path / "h.db")
    try:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        raw.close()


def test_evidence_contention_api_and_worker(tmp_path):
    from ledger.app import make_app

    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    db = tmp_path / "c.db"
    port = _free_port()
    server = _serve(make_app(f"sqlite:///{db}"), port)
    try:
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base, timeout=30) as c:
            c.post("/v1/tenants", json={"tenant": "acme"}).raise_for_status()
            c.post("/v1/projects", json={"tenant": "acme", "project": "site"}).raise_for_status()
            api_tid = c.post("/v1/tasks", json={"title": "api", "intent": "x",
                                                "tenant": "acme", "project": "site"}).json()["id"]
            worker_tid = c.post("/v1/tasks", json={"title": "worker", "intent": "y",
                                                   "tenant": "acme", "project": "site"}).json()["id"]

        errors: list[str] = []
        lock = threading.Lock()

        def api_writer(n: int, tid: int):
            with httpx.Client(base_url=base, timeout=30) as c:
                for i in range(n):
                    try:
                        r = c.post(f"/v1/tasks/{tid}/evidence",
                                   json={"kind": "note",
                                         "body": {"fence": f"api-{threading.get_ident()}-{i}",
                                                  "payload": {"i": i}}})
                    except httpx.TransportError as e:
                        with lock:
                            errors.append(f"api exc: {e!r}"[:300])
                        continue
                    if r.status_code != 200 or "database is locked" in r.text.lower():
                        with lock:
                            errors.append(f"api {r.status_code}: {r.text[:200]}")

        def worker_writer(n: int):
            # sidecar worker with its own connection: contends at the file
            # level, so WAL + busy_timeout (not just the shared pool) matter.
            conn = sqlite3.connect(db, timeout=5.0)
            try:
                for i in range(n):
                    try:
                        conn.execute(
                            "INSERT INTO evidence(task_id,kind,body,sha256,created_at)"
                            " VALUES (?,?,?,?,?)",
                            (worker_tid, "note",
                             f'{{"fence": "worker-{threading.get_ident()}-{i}"}}',
                             "x", "2026-11-02T00:00:00+00:00"))
                        conn.commit()
                    except sqlite3.OperationalError as e:
                        with lock:
                            errors.append(f"worker exc: {e!r}"[:300])
            finally:
                conn.close()

        threads = ([threading.Thread(target=api_writer, args=(20, api_tid)) for _ in range(4)]
                   + [threading.Thread(target=api_writer, args=(20, worker_tid)) for _ in range(2)]
                   + [threading.Thread(target=worker_writer, args=(40,)) for _ in range(2)])
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert not any(t.is_alive() for t in threads), "writer threads hung"
        assert errors == [], f"{len(errors)} contention failures: {errors[:5]}"

        conn = sqlite3.connect(db)
        try:
            (n_api,) = conn.execute("SELECT COUNT(*) FROM evidence WHERE task_id=?",
                                    (api_tid,)).fetchone()
            (n_worker,) = conn.execute("SELECT COUNT(*) FROM evidence WHERE task_id=?",
                                       (worker_tid,)).fetchone()
        finally:
            conn.close()
        assert (n_api, n_worker) == (80, 120)
    finally:
        server.should_exit = True
