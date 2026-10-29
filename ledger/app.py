"""Rouvr ledger: a slim WorkHub-lite (first pass).

Durable tasks, done-contracts, evidence and idempotent receipts over SQL.
Set ROUVR_LEDGER_DATABASE_URL (e.g. postgresql+psycopg://...) or it defaults
to a local SQLite file. Not hardened: no auth, no multi-tenancy.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text

DB_URL = os.environ.get("ROUVR_LEDGER_DATABASE_URL", "sqlite:///./.rouvr/ledger.db")
TRANSITIONS = {
    "open": {"claimed", "blocked"},
    "claimed": {"done", "failed", "blocked", "open"},
    "blocked": {"open"},
    "failed": {"open"},
    "done": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canon(obj) -> tuple[str, str]:
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return s, hashlib.sha256(s.encode()).hexdigest()


def make_app(db_url: str = DB_URL) -> FastAPI:
    if db_url.startswith("sqlite:///./"):
        Path(db_url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(db_url)
    with eng.begin() as c:
        for stmt in (Path(__file__).with_name("schema.sql").read_text().split(";")):
            body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--"))
            if body.strip():
                c.execute(text(body))
    app = FastAPI(title="Rouvr ledger")

    def row(c, sql, **kw):
        r = c.execute(text(sql), kw).mappings().first()
        return dict(r) if r else None

    def task_or_404(c, tid):
        t = row(c, "SELECT * FROM tasks WHERE id=:id", id=tid)
        if not t:
            raise HTTPException(404, "task not found")
        return t

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "rouvr-ledger"}

    @app.post("/v1/tasks")
    def create_task(body: dict):
        with eng.begin() as c:
            n = _now()
            c.execute(text("INSERT INTO tasks(title,intent,state,created_at,updated_at) VALUES(:t,:i,'open',:n,:n)"),
                      {"t": body.get("title", ""), "i": body.get("intent", ""), "n": n})
            return row(c, "SELECT * FROM tasks ORDER BY id DESC LIMIT 1")

    @app.get("/v1/tasks")
    def list_tasks(state: str | None = None):
        with eng.begin() as c:
            q = "SELECT * FROM tasks" + (" WHERE state=:s" if state else "") + " ORDER BY id"
            return {"items": [dict(r) for r in c.execute(text(q), {"s": state}).mappings()]}

    @app.get("/v1/tasks/{tid}")
    def get_task(tid: int):
        with eng.begin() as c:
            t = task_or_404(c, tid)
            t["contracts"] = [dict(r) for r in c.execute(text("SELECT * FROM contracts WHERE task_id=:i ORDER BY id"), {"i": tid}).mappings()]
            t["evidence"] = [dict(r) for r in c.execute(text("SELECT * FROM evidence WHERE task_id=:i ORDER BY id"), {"i": tid}).mappings()]
            t["receipts"] = [dict(r) for r in c.execute(text("SELECT * FROM receipts WHERE task_id=:i ORDER BY id"), {"i": tid}).mappings()]
            return t

    @app.post("/v1/tasks/{tid}/contracts")
    def add_contract(tid: int, body: dict):
        s, h = _canon(body)
        with eng.begin() as c:
            task_or_404(c, tid)
            existing = row(c, "SELECT * FROM contracts WHERE task_id=:i AND sha256=:h", i=tid, h=h)
            if existing:
                return existing
            c.execute(text("INSERT INTO contracts(task_id,body,sha256,created_at) VALUES(:i,:b,:h,:n)"),
                      {"i": tid, "b": s, "h": h, "n": _now()})
            return row(c, "SELECT * FROM contracts WHERE task_id=:i AND sha256=:h", i=tid, h=h)

    @app.post("/v1/tasks/{tid}/evidence")
    def add_evidence(tid: int, body: dict):
        s, h = _canon(body.get("body", {}))
        with eng.begin() as c:
            task_or_404(c, tid)
            c.execute(text("INSERT INTO evidence(task_id,kind,body,sha256,created_at) VALUES(:i,:k,:b,:h,:n)"),
                      {"i": tid, "k": body.get("kind", "note"), "b": s, "h": h, "n": _now()})
            return row(c, "SELECT * FROM evidence WHERE task_id=:i ORDER BY id DESC LIMIT 1", i=tid)

    @app.post("/v1/tasks/{tid}/transition")
    def transition(tid: int, body: dict):
        """Idempotent state change. Requires idempotency_key; replays return the first receipt.
        Moving to 'done' requires a contract and at least one evidence row."""
        key = body.get("idempotency_key")
        to = body.get("to")
        if not key or not to:
            raise HTTPException(422, "idempotency_key and to are required")
        with eng.begin() as c:
            prior = row(c, "SELECT * FROM receipts WHERE idempotency_key=:k", k=key)
            if prior:
                return {"replayed": True, "receipt": prior}
            t = task_or_404(c, tid)
            if to not in TRANSITIONS.get(t["state"], set()):
                raise HTTPException(409, f"illegal transition {t['state']} -> {to}")
            if to == "done":
                nc = row(c, "SELECT COUNT(*) AS n FROM contracts WHERE task_id=:i", i=tid)["n"]
                ne = row(c, "SELECT COUNT(*) AS n FROM evidence WHERE task_id=:i", i=tid)["n"]
                if not nc or not ne:
                    raise HTTPException(409, "done requires a contract and evidence")
            c.execute(text("UPDATE tasks SET state=:s, owner=COALESCE(:o, owner), updated_at=:n WHERE id=:i"),
                      {"s": to, "o": body.get("owner"), "n": _now(), "i": tid})
            s, _ = _canon({"from": t["state"], "to": to, "note": body.get("note")})
            c.execute(text("INSERT INTO receipts(task_id,idempotency_key,action,body,created_at) VALUES(:i,:k,:a,:b,:n)"),
                      {"i": tid, "k": key, "a": f"transition:{to}", "b": s, "n": _now()})
            return {"replayed": False, "receipt": row(c, "SELECT * FROM receipts WHERE idempotency_key=:k", k=key)}

    # ---- R3 parity: assignment / lease / attempt / result ----
    def _expiry(ttl_s) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=ttl_s)).isoformat()

    def _receipted(c, key):
        prior = row(c, "SELECT * FROM receipts WHERE idempotency_key=:k", k=key)
        if prior:
            return {"replayed": True, "row": json.loads(prior["body"])}
        return None

    def _remember(c, tid, key, action, payload: dict):
        s, _ = _canon(payload)
        c.execute(text("INSERT INTO receipts(task_id,idempotency_key,action,body,created_at) VALUES(:i,:k,:a,:b,:n)"),
                  {"i": tid, "k": key, "a": action, "b": s, "n": _now()})

    @app.get("/v1/tasks/{tid}/binding")
    def get_binding(tid: int):
        with eng.begin() as c:
            task_or_404(c, tid)
            a = row(c, "SELECT * FROM assignments WHERE task_id=:i AND status='active' ORDER BY id DESC LIMIT 1", i=tid)
            if not a:
                raise HTTPException(404, "no active assignment")
            return {"assignment": a, "lease": row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)}

    @app.post("/v1/tasks/{tid}/assignments/claim")
    def claim_assignment(tid: int, body: dict):
        key, principal = body.get("key"), body.get("principal", "")
        attempt_no, ttl_s = int(body.get("attempt_no", 1)), int(body.get("ttl_s", 120))
        if not key or not principal:
            raise HTTPException(422, "key and principal are required")
        with eng.begin() as c:
            task_or_404(c, tid)
            if (hit := _receipted(c, key)):
                return {"replayed": True, "assignment": hit["row"]}
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            if lease and lease["expires_at"] > _now() and lease["holder"] != principal:
                raise HTTPException(409, "lease held by another holder")
            epoch = (lease["lease_epoch"] + 1) if lease else 1
            try:
                c.execute(text("INSERT INTO assignments(task_id,principal,attempt_no,created_at) VALUES(:i,:p,:n,:t)"),
                          {"i": tid, "p": principal, "n": attempt_no, "t": _now()})
            except Exception as exc:
                raise HTTPException(409, f"attempt_no {attempt_no} already claimed") from exc
            c.execute(text("INSERT INTO leases(task_id,holder,lease_epoch,expires_at,updated_at) VALUES(:i,:h,:e,:x,:t) "
                            "ON CONFLICT(task_id) DO UPDATE SET holder=:h, lease_epoch=:e, expires_at=:x, updated_at=:t"),
                      {"i": tid, "h": principal, "e": epoch, "x": _expiry(ttl_s), "t": _now()})
            a = row(c, "SELECT * FROM assignments WHERE task_id=:i AND attempt_no=:n", i=tid, n=attempt_no)
            _remember(c, tid, key, "claim", a)
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            return {"replayed": False, "assignment": a, "lease_epoch": epoch, "lease": lease}

    @app.post("/v1/tasks/{tid}/leases/renew")
    def renew_lease(tid: int, body: dict):
        key, holder = body.get("key"), body.get("holder", "")
        expected, ttl_s = body.get("expected_lease_epoch"), int(body.get("ttl_s", 120))
        if not key or not holder or expected is None:
            raise HTTPException(422, "key, holder and expected_lease_epoch are required")
        with eng.begin() as c:
            task_or_404(c, tid)
            if (hit := _receipted(c, key)):
                return {"replayed": True, "lease": hit["row"]}
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            if not lease:
                raise HTTPException(404, "no lease")
            if lease["holder"] != holder or int(lease["lease_epoch"]) != int(expected):
                raise HTTPException(409, "stale epoch or wrong holder")
            if lease["expires_at"] <= _now():
                raise HTTPException(409, "lease expired")
            c.execute(text("UPDATE leases SET expires_at=:x, updated_at=:t WHERE task_id=:i"),
                      {"x": _expiry(ttl_s), "t": _now(), "i": tid})
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            _remember(c, tid, key, "renew", lease)
            return {"replayed": False, "lease": lease}

    @app.post("/v1/tasks/{tid}/assignments/reclaim")
    def reclaim_assignment(tid: int, body: dict):
        key = body.get("key")
        if not key:
            raise HTTPException(422, "key is required")
        with eng.begin() as c:
            task_or_404(c, tid)
            if (hit := _receipted(c, key)):
                return {"replayed": True, **hit["row"]}
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            if lease and lease["expires_at"] > _now():
                raise HTTPException(409, "lease still live")
            if lease:
                c.execute(text("DELETE FROM leases WHERE task_id=:i"), {"i": tid})
                c.execute(text("UPDATE assignments SET status='released' WHERE task_id=:i AND status='active'"), {"i": tid})
            out = {"reclaimed": True}
            _remember(c, tid, key, "reclaim", out)
            return {"replayed": False, **out}

    @app.post("/v1/tasks/{tid}/attempts")
    def open_attempt(tid: int, body: dict):
        key, attempt_no, epoch = body.get("key"), body.get("attempt_no"), body.get("lease_epoch")
        if not key or attempt_no is None or epoch is None:
            raise HTTPException(422, "key, attempt_no and lease_epoch are required")
        with eng.begin() as c:
            task_or_404(c, tid)
            if (hit := _receipted(c, key)):
                return {"replayed": True, "attempt": hit["row"]}
            existing = row(c, "SELECT * FROM attempts WHERE task_id=:i AND attempt_no=:n", i=tid, n=attempt_no)
            if existing:
                return {"replayed": True, "attempt": existing}
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            if not lease or int(lease["lease_epoch"]) != int(epoch):
                raise HTTPException(409, "attempt epoch does not match live lease")
            c.execute(text("INSERT INTO attempts(task_id,attempt_no,lease_epoch,created_at) VALUES(:i,:n,:e,:t)"),
                      {"i": tid, "n": attempt_no, "e": epoch, "t": _now()})
            a = row(c, "SELECT * FROM attempts WHERE task_id=:i AND attempt_no=:n", i=tid, n=attempt_no)
            _remember(c, tid, key, "attempt", a)
            return {"replayed": False, "attempt": a}

    @app.post("/v1/tasks/{tid}/results")
    def record_result(tid: int, body: dict):
        key, attempt_id, epoch = body.get("key"), body.get("attempt_id"), body.get("lease_epoch")
        verdict, result = body.get("verdict", ""), body.get("result", {})
        if not key or attempt_id is None or epoch is None or not verdict:
            raise HTTPException(422, "key, attempt_id, lease_epoch and verdict are required")
        core, _ = _canon({"attempt_id": attempt_id, "lease_epoch": epoch, "verdict": verdict, "result": result})
        with eng.begin() as c:
            task_or_404(c, tid)
            if (hit := _receipted(c, key)):
                return {"replayed": True, "result": hit["row"]}
            existing = row(c, "SELECT * FROM results WHERE task_id=:i AND attempt_id=:a", i=tid, a=attempt_id)
            if existing:
                if existing["body"] != core:
                    raise HTTPException(409, "REPLAY_CONFLICT: same attempt, different result core")
                return {"replayed": True, "result": existing}
            c.execute(text("INSERT INTO results(task_id,attempt_id,lease_epoch,verdict,body,created_at) VALUES(:i,:a,:e,:v,:b,:t)"),
                      {"i": tid, "a": attempt_id, "e": epoch, "v": verdict, "b": core, "t": _now()})
            r = row(c, "SELECT * FROM results WHERE task_id=:i AND attempt_id=:a", i=tid, a=attempt_id)
            _remember(c, tid, key, "result", r)
            return {"replayed": False, "result": r}

    return app


app = make_app()
