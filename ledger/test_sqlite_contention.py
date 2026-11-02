"""FL-A5: SQLite hardening — API + worker writing evidence concurrently.

A real server (uvicorn, like tests/test_perf_evidence.py) takes concurrent
POST /v1/tasks/{id}/evidence writes from API threads (sync httpx) and
worker threads (the LedgerWorkHub.attach_evidence client workers use).
Pass: every write lands, zero "database is locked".
"""
from __future__ import annotations

import asyncio
import os
import socket
import sqlite3
import threading
import time

import httpx
import uvicorn
from sqlalchemy import text

from rouvr.errors import RouvrError


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve(app, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        try:
            resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
        except httpx.TransportError:
            time.sleep(0.1)
        else:
            if resp.status_code == 200:
                return server
            time.sleep(0.1)
    raise RuntimeError("test server did not start")


def test_writer_lock_shared_per_db(tmp_path):
    from ledger.app import _write_lock

    db = tmp_path / "s.db"
    assert _write_lock(f"sqlite:///{db}") is _write_lock(f"sqlite:///{db}")
    assert _write_lock(f"sqlite:///{db}") is not _write_lock(f"sqlite:///{tmp_path}/other.db")


def test_api_and_worker_evidence_contention(tmp_path):
    from ledger.app import SQLITE_BUSY_TIMEOUT_MS, make_app
    from rouvr.workhub.ledger import LedgerWorkHub

    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    db = tmp_path / "c.db"
    app = make_app(f"sqlite:///{db}")
    port = _free_port()
    server = _serve(app, port)
    try:
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base, timeout=30) as c:
            c.post("/v1/tenants", json={"tenant": "acme"}).raise_for_status()
            c.post("/v1/projects", json={"tenant": "acme", "project": "site"}).raise_for_status()
            tid = c.post("/v1/tasks", json={"title": "t", "intent": "x",
                                            "tenant": "acme", "project": "site"}).json()["id"]

        errors: list[str] = []
        errors_lock = threading.Lock()

        def api_writer(n: int) -> None:
            try:
                with httpx.Client(base_url=base, timeout=30) as c:
                    for i in range(n):
                        r = c.post(f"/v1/tasks/{tid}/evidence",
                                   json={"kind": "api-note", "body": {"seq": i}})
                        assert r.status_code == 200, f"API write failed: {r.status_code} {r.text[:200]}"
                        assert "database is locked" not in r.text
            except (httpx.HTTPError, AssertionError) as e:  # collected, asserted below
                with errors_lock:
                    errors.append(f"api: {e!r}")

        def worker_writer(n: int) -> None:
            async def _go() -> None:
                hub = LedgerWorkHub(base)
                try:
                    for i in range(n):
                        await hub.attach_evidence(str(tid), "worker-note", f"w-{i}", {"seq": i})
                finally:
                    await hub.aclose()

            try:
                asyncio.run(_go())
            except (RouvrError, httpx.HTTPError) as e:  # collected, asserted below
                with errors_lock:
                    errors.append(f"worker: {e!r}")

        threads = ([threading.Thread(target=api_writer, args=(10,)) for _ in range(8)]
                   + [threading.Thread(target=worker_writer, args=(10,)) for _ in range(8)])
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
            assert not t.is_alive(), "writer thread hung"
        assert "database is locked" not in "\n".join(errors)
        assert errors == [], f"{len(errors)} writer errors, first: {errors[:3]}"

        with httpx.Client(base_url=base, timeout=30) as c:
            got = c.get(f"/v1/tasks/{tid}").json()
        assert len(got["evidence"]) == 160, f"lost writes: {len(got['evidence'])}/160"

        # Config: WAL persists on the file; busy_timeout is per-connection
        # so it is asserted through the app's own engine.
        assert SQLITE_BUSY_TIMEOUT_MS == 5000
        assert sqlite3.connect(db).execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        with app.state.engine.connect() as c:
            assert c.execute(text("PRAGMA busy_timeout")).scalar() == 5000
    finally:
        server.should_exit = True
