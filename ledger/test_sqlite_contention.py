"""A5 SQLite hardening: API + worker writing evidence concurrently.

Topology: a real API server process (uvicorn) serves one SQLite file while
API threads post evidence over HTTP and worker threads append evidence rows
over direct SQLite connections opened with the shared hardened settings.
Every write must land and no writer may see a lock error.
"""
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from ledger.app import SQLITE_BUSY_TIMEOUT_MS, make_app, sqlite_connect

ROOT = Path(__file__).resolve().parents[1]
TENANT = "acme"
PROJECT = "site"
API_WRITERS = 4
API_ROWS_EACH = 50
WORKER_WRITERS = 2
WORKER_ROWS_EACH = 100


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_ready(base, proc, timeout=60):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if proc.poll() is not None:
            raise AssertionError(f"test server exited early with code {proc.returncode}")
        try:
            resp = httpx.get(f"{base}/health", timeout=2)
        except httpx.HTTPError:
            time.sleep(0.2)
            continue
        if resp.status_code == 200:
            return
        time.sleep(0.2)
    raise AssertionError("test server did not become ready in time")


def _api_writer(base, tid, tag, count, errors, guard):
    client = httpx.Client(base_url=base, timeout=30)
    for seq in range(count):
        try:
            posted = client.post(f"/v1/tasks/{tid}/evidence",
                                 json={"kind": "load", "body": {"writer": tag, "seq": seq}})
        except httpx.HTTPError as exc:
            with guard:
                errors.append(f"{tag}: request failed: {exc!r}")
            break
        if posted.status_code != 200:
            with guard:
                errors.append(f"{tag}: status {posted.status_code}: {posted.text[:200]}")
            break
    client.close()


def _worker_writer(db_file, tid, tag, count, errors, guard):
    try:
        conn = sqlite_connect(db_file)
    except sqlite3.Error as exc:
        with guard:
            errors.append(f"{tag}: connect failed: {exc!r}")
        return
    for seq in range(count):
        try:
            with conn:
                conn.execute("INSERT INTO evidence(task_id,kind,body,sha256,created_at)"
                             " VALUES(?,?,?,?,?)",
                             (tid, "load", '{"writer": "%s", "seq": %d}' % (tag, seq),
                              "%s-%d" % (tag, seq), "2026-11-02T00:00:00+00:00"))
        except sqlite3.Error as exc:
            with guard:
                errors.append(f"{tag}: insert failed: {exc!r}")
            break
    conn.close()


def test_api_and_worker_write_evidence_concurrently(tmp_path):
    db_file = tmp_path / "contention.db"
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env["ROUVR_LEDGER_DATABASE_URL"] = f"sqlite:///{db_file}"
    env["ROUVR_INSECURE_DEV"] = "1"
    env.pop("ROUVR_LEDGER_TOKEN", None)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "--factory", "ledger.app:make_app",
                             "--host", "127.0.0.1", "--port", str(port)],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_ready(base, proc)
        setup = httpx.Client(base_url=base, timeout=30)
        try:
            assert setup.post("/v1/tenants", json={"tenant": TENANT}).status_code == 200
            assert setup.post("/v1/projects", json={"tenant": TENANT, "project": PROJECT}).status_code == 200
            created = setup.post("/v1/tasks", json={"title": "contention",
                                                    "intent": "prove concurrent writes",
                                                    "tenant": TENANT, "project": PROJECT})
            assert created.status_code == 200
            tid = created.json()["id"]
        finally:
            setup.close()
        errors: list[str] = []
        guard = threading.Lock()
        threads = ([threading.Thread(target=_api_writer, args=(base, tid, f"api-{n}",
                                                               API_ROWS_EACH, errors, guard))
                    for n in range(API_WRITERS)]
                   + [threading.Thread(target=_worker_writer, args=(str(db_file), tid, f"worker-{n}",
                                                                     WORKER_ROWS_EACH, errors, guard))
                      for n in range(WORKER_WRITERS)])
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=300)
        stuck = sorted(thread.name for thread in threads if thread.is_alive())
        assert stuck == []
        assert errors == []
        probe = sqlite3.connect(str(db_file))
        try:
            mode = probe.execute("PRAGMA journal_mode").fetchone()[0]
            total = probe.execute("SELECT COUNT(*) FROM evidence WHERE task_id=?", (tid,)).fetchone()[0]
        finally:
            probe.close()
        assert mode.lower() == "wal"
        assert total == API_WRITERS * API_ROWS_EACH + WORKER_WRITERS * WORKER_ROWS_EACH
    finally:
        proc.terminate()
        proc.wait(timeout=30)


def test_sqlite_writer_config_shared_per_database(tmp_path):
    os.environ["ROUVR_INSECURE_DEV"] = "1"
    os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    url = f"sqlite:///{tmp_path}/shared.db"
    first = make_app(url)
    second = make_app(url)
    assert second.state.writer_lock is first.state.writer_lock
    with first.state.engine.connect() as conn:
        timeout_ms = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
    assert timeout_ms == SQLITE_BUSY_TIMEOUT_MS
