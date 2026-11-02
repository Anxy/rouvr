"""FL-A5 SQLite hardening: WAL + busy_timeout + single-writer queue.

Contention proof: API writers (httpx against a live uvicorn server) and
worker writers (direct sqlite3 on the same DB file) insert evidence
concurrently; the run must finish with zero "database is locked" failures
and every row present. A lock holder takes BEGIN IMMEDIATE without the
shared mutex — like a separate worker process — so genuine SQLITE_BUSY
retries are exercised and only busy_timeout absorbs them.
"""
from __future__ import annotations

import os
import socket
import sqlite3
import threading
import time

import httpx
import uvicorn
from sqlalchemy import text

from ledger import sqlite_hardening as sh
from ledger.app import make_app

API_THREADS = 4
API_WRITES_EACH = 20
WORKER_THREADS = 2
WORKER_WRITES_EACH = 20
HOLD_CYCLES = 5
HOLD_SECONDS = 0.3


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_ready(base: str) -> None:
    deadline = time.monotonic() + 15.0
    last_error = "no attempt yet"
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base}/health", timeout=1.0)
            if resp.status_code == 200:
                return
            last_error = f"health status {resp.status_code}"
            time.sleep(0.1)
        except httpx.TransportError as exc:
            last_error = str(exc)
            time.sleep(0.1)
    raise AssertionError(f"ledger server never became ready: {last_error}")


def _hardened_env() -> None:
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)


def test_sqlite_pragmas_and_shared_writer(tmp_path):
    _hardened_env()
    db_url = f"sqlite:///{tmp_path}/c.db"
    app = make_app(db_url)
    assert app.title == "Rouvr ledger"
    probe = sqlite3.connect(str(tmp_path / "c.db"))
    try:
        mode = probe.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        probe.close()
    assert str(mode).lower() == "wal"
    engine = sh.create_hardened_engine(db_url)
    try:
        with engine.connect() as conn:
            timeout_ms = conn.execute(text("PRAGMA busy_timeout")).scalar()
    finally:
        engine.dispose()
    assert int(timeout_ms) == sh.BUSY_TIMEOUT_MS
    assert sh.get_write_lock(db_url) is sh.get_write_lock(db_url)
    other = f"sqlite:///{tmp_path}/other.db"
    assert sh.get_write_lock(db_url) is not sh.get_write_lock(other)


def test_api_worker_evidence_contention_has_zero_locks(tmp_path):
    _hardened_env()
    db_file = tmp_path / "l.db"
    db_url = f"sqlite:///{db_file}"
    app = make_app(db_url)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    runner = threading.Thread(target=server.run, daemon=True)
    runner.start()
    base = f"http://127.0.0.1:{port}"
    _wait_ready(base)
    try:
        _run_contention(base, db_file, db_url)
    finally:
        server.should_exit = True
        runner.join(timeout=10)


def _run_contention(base: str, db_file, db_url: str) -> None:
    created = httpx.post(f"{base}/v1/tenants", json={"tenant": "acme"}, timeout=5.0)
    assert created.status_code == 200
    created = httpx.post(f"{base}/v1/projects", json={"tenant": "acme", "project": "site"}, timeout=5.0)
    assert created.status_code == 200
    created = httpx.post(f"{base}/v1/tasks", json={"title": "contention", "intent": "soak",
                                                   "tenant": "acme", "project": "site"}, timeout=5.0)
    assert created.status_code == 200
    tid = created.json()["id"]

    api_errors: list[str] = []
    api_guard = threading.Lock()

    def api_writer(wid: int) -> None:
        failures: list[str] = []
        for seq in range(API_WRITES_EACH):
            try:
                resp = httpx.post(f"{base}/v1/tasks/{tid}/evidence",
                                  json={"kind": f"api-{wid}", "body": {"n": seq}},
                                  timeout=10.0)
                if resp.status_code != 200:
                    failures.append(f"api-{wid}/{seq}: status {resp.status_code}: {resp.text[:200]}")
            except httpx.TransportError as exc:
                failures.append(f"api-{wid}/{seq}: transport: {exc}")
        if failures:
            with api_guard:
                api_errors.extend(failures)

    worker_errors: list[str] = []
    worker_guard = threading.Lock()

    def worker_writer(wid: int) -> None:
        failures: list[str] = []
        conn = sqlite3.connect(str(db_file), timeout=sh.BUSY_TIMEOUT_S, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={sh.BUSY_TIMEOUT_MS}")
            for seq in range(WORKER_WRITES_EACH):
                try:
                    with sh.get_write_lock(db_url):
                        with conn:
                            conn.execute("INSERT INTO evidence(task_id,kind,body,sha256,created_at)"
                                         " VALUES(?,?,?,?,?)",
                                         (tid, f"wk-{wid}", f"{{\"n\":{seq}}}",
                                          f"wk-{wid}-{seq}", "2026-11-02T00:00:00+00:00"))
                except sqlite3.OperationalError as exc:
                    failures.append(f"wk-{wid}/{seq}: operational: {exc}")
        finally:
            conn.close()
        if failures:
            with worker_guard:
                worker_errors.extend(failures)

    hold_errors: list[str] = []
    hold_guard = threading.Lock()
    holding = threading.Event()

    def lock_holder() -> None:
        failures: list[str] = []
        conn = sqlite3.connect(str(db_file), timeout=sh.BUSY_TIMEOUT_S, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={sh.BUSY_TIMEOUT_MS}")
            conn.isolation_level = None
            for cycle in range(HOLD_CYCLES):
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    holding.set()
                    time.sleep(HOLD_SECONDS)
                    conn.execute("INSERT INTO evidence(task_id,kind,body,sha256,created_at)"
                                 " VALUES(?,?,?,?,?)",
                                 (tid, "hold", f"{{\"c\":{cycle}}}", f"hold-{cycle}",
                                  "2026-11-02T00:00:00+00:00"))
                    conn.execute("COMMIT")
                except sqlite3.OperationalError as exc:
                    failures.append(f"hold/{cycle}: operational: {exc}")
                    conn.execute("ROLLBACK")
        finally:
            conn.close()
        if failures:
            with hold_guard:
                hold_errors.extend(failures)

    holder = threading.Thread(target=lock_holder)
    holder.start()
    if not holding.wait(timeout=10):
        raise AssertionError("lock holder never took BEGIN IMMEDIATE")
    threads = [threading.Thread(target=api_writer, args=(wid,)) for wid in range(API_THREADS)]
    threads.extend(threading.Thread(target=worker_writer, args=(wid,)) for wid in range(WORKER_THREADS))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    holder.join(timeout=60)
    stuck = [thread for thread in threads + [holder] if thread.is_alive()]
    assert not stuck, f"{len(stuck)} writer threads did not finish"
    locked = [e for e in api_errors + worker_errors + hold_errors if "database is locked" in e]
    assert not locked, f"database-is-locked failures: {locked[:3]}"
    assert not api_errors, f"API writer failures: {api_errors[:5]}"
    assert not worker_errors, f"worker writer failures: {worker_errors[:5]}"
    assert not hold_errors, f"holder failures: {hold_errors[:5]}"
    check = sqlite3.connect(str(db_file))
    try:
        total = check.execute("SELECT COUNT(*) FROM evidence WHERE task_id=?", (tid,)).fetchone()[0]
    finally:
        check.close()
    expected = API_THREADS * API_WRITES_EACH + WORKER_THREADS * WORKER_WRITES_EACH + HOLD_CYCLES
    assert total == expected
