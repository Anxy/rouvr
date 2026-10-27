"""Rouvr ledger: a slim WorkHub-lite (first pass).

Durable tasks, done-contracts, evidence and idempotent receipts over SQL.
Set ROUVR_LEDGER_DATABASE_URL (e.g. postgresql+psycopg://...) or it defaults
to a local SQLite file. Not hardened: no auth, no multi-tenancy.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
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

    return app


app = make_app()
