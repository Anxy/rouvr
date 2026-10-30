"""S014: fence lookup stays correct (and single-row) with many evidence rows."""
import asyncio, json, sqlite3, threading, time

import httpx
import uvicorn


def test_fence_lookup_with_many_rows(tmp_path):
    from ledger.app import make_app
    db = tmp_path / "p.db"
    config = uvicorn.Config(make_app(f"sqlite:///{db}"), host="127.0.0.1", port=18241,
                            log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            if httpx.get("http://127.0.0.1:18241/health", timeout=1).status_code == 200: break
        except httpx.TransportError: pass
        time.sleep(0.1)
    try:
        with httpx.Client(base_url="http://127.0.0.1:18241", timeout=15) as c:
            tid = c.post("/v1/tasks", json={"title": "t", "intent": "x"}).json()["id"]
            conn = sqlite3.connect(db)
            rows = [(tid, "note", json.dumps({"fence": f"f{i}", "payload": {}}, sort_keys=True),
                     "x", "2026-01-01") for i in range(2000)]
            conn.executemany("INSERT INTO evidence(task_id,kind,body,sha256,created_at)"
                             " VALUES (?,?,?,?,?)", rows); conn.commit(); conn.close()
            r = c.get(f"/v1/tasks/{tid}/evidence/lookup", params={"kind": "note", "fence": "f1999"})
            assert r.status_code == 200 and json.loads(r.json()["body"])["fence"] == "f1999"
            assert c.get(f"/v1/tasks/{tid}/evidence/lookup",
                         params={"kind": "note", "fence": "nope"}).status_code == 404
            mode = sqlite3.connect(db).execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"
    finally:
        server.should_exit = True
