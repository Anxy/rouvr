"""Rouvr ledger: a slim WorkHub-lite (first pass).

Durable tasks, done-contracts, evidence and idempotent receipts over SQL.
Set ROUVR_LEDGER_DATABASE_URL to a sqlite:/// URL or it defaults to a
local SQLite file. SQLite only: a postgres URL fails fast in make_app
(the schema and lastrowid usage are SQLite-specific). Not hardened:
no auth beyond tokens. Tenants own projects own tasks (S022 partitions);
both scope fields are mandatory on every create, never defaulted.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import create_engine, text

DB_URL = os.environ.get("ROUVR_LEDGER_DATABASE_URL", "sqlite:///./.rouvr/ledger.db")
TRANSITIONS = {
    "open": {"claimed", "blocked"},
    "claimed": {"done", "failed", "blocked", "open", "needs_review"},
    "needs_review": {"done", "blocked", "claimed"},
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
    if db_url.startswith("postgres"):  # advertised once, never worked: fail fast, not per-insert
        raise ValueError("ledger supports SQLite only: set ROUVR_LEDGER_DATABASE_URL "
                         "to a sqlite:/// URL (Postgres is not supported)")
    if db_url.startswith("sqlite:///./"):
        Path(db_url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(db_url)
    if db_url.startswith("sqlite"):  # S014: WAL — parallel workers write evidence
        with eng.connect() as c:
            c.execute(text("PRAGMA journal_mode=WAL"))
            c.commit()
    with eng.begin() as c:
        for stmt in (Path(__file__).with_name("schema.sql").read_text().split(";")):
            body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--"))
            if body.strip():
                c.execute(text(body))
    app = FastAPI(title="Rouvr ledger")

    # Add created_by to pre-S016 databases (schema.sql covers fresh ones
    # only via CREATE TABLE; existing tables need the column explicitly).
    with eng.begin() as c:
        cols = [r[1] for r in c.execute(text("PRAGMA table_info(tasks)"))]
        if cols and "created_by" not in cols:
            c.execute(text("ALTER TABLE tasks ADD COLUMN created_by TEXT NOT NULL DEFAULT 'supervisor'"))
    # S022 partitions on pre-S022 databases: new tables plus scope columns.
    # No silent backfill — unscoped rows refuse startup with an explicit
    # assignment message instead of a default.
    with eng.begin() as c:
        c.execute(text("CREATE TABLE IF NOT EXISTS tenants(tenant TEXT PRIMARY KEY,"
                       " created_at TEXT NOT NULL)"))
        c.execute(text("CREATE TABLE IF NOT EXISTS projects(tenant TEXT NOT NULL REFERENCES tenants(tenant),"
                       " project TEXT NOT NULL, created_at TEXT NOT NULL,"
                       " PRIMARY KEY (tenant, project))"))
        tcols = [r[1] for r in c.execute(text("PRAGMA table_info(tasks)"))]
        for col in ("tenant", "project"):
            if tcols and col not in tcols:
                c.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} TEXT"))
        ucols = [r[1] for r in c.execute(text("PRAGMA table_info(users)"))]
        if ucols and "tenant" not in ucols:
            c.execute(text("ALTER TABLE users ADD COLUMN tenant TEXT"))
    with eng.begin() as c:  # begin: the receipts migration below must commit
        n = c.execute(text("SELECT COUNT(*) FROM tasks WHERE tenant IS NULL OR tenant=''"
                           " OR project IS NULL OR project=''")).scalar()
        if n:
            raise ValueError(f"ledger has {n} task(s) without tenant/project scope: assign scope"
                             " explicitly (no defaults); refusing to serve unpartitioned rows")
        if db_url.startswith("sqlite"):
            rsql = c.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='receipts'")).scalar()
            norm = "".join((rsql or "").lower().split())
            if rsql and "unique(task_id,idempotency_key)" not in norm and "unique" in norm:
                c.execute(text("CREATE TABLE receipts_new (id INTEGER PRIMARY KEY,"
                               " task_id INTEGER NOT NULL REFERENCES tasks(id),"
                               " idempotency_key TEXT NOT NULL, action TEXT NOT NULL,"
                               " body TEXT NOT NULL, created_at TEXT NOT NULL,"
                               " UNIQUE (task_id, idempotency_key))"))
                c.execute(text("INSERT INTO receipts_new(id,task_id,idempotency_key,action,body,created_at)"
                               " SELECT id,task_id,idempotency_key,action,body,created_at FROM receipts"))
                c.execute(text("DROP TABLE receipts"))
                c.execute(text("ALTER TABLE receipts_new RENAME TO receipts"))

    # Token auth (S011 + S016, partitions S022): the shared stack token
    # authenticates as "supervisor" (stack/worker identity, sees all). A
    # per-user token authenticates as that user, scoped to exactly its
    # own tenant's projects and tasks. Unset shared token = open dev
    # mode (identity supervisor).
    _token = os.environ.get("ROUVR_LEDGER_TOKEN", "")

    @app.middleware("http")
    async def _auth(request, call_next):
        request.state.user = "supervisor"
        if _token and request.url.path.startswith("/v1"):
            from fastapi.responses import JSONResponse
            hdr = request.headers.get("x-rouvr-token", "")
            if hdr == _token:
                pass  # stack identity
            elif hdr:
                h = hashlib.sha256(hdr.encode()).hexdigest()
                with eng.connect() as c:
                    u = c.execute(text("SELECT username, disabled FROM users WHERE token_hash=:h"),
                                  {"h": h}).mappings().first()
                if not u or u["disabled"]:
                    return JSONResponse({"detail": "unauthorized"}, status_code=401)
                request.state.user = u["username"]
            else:
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    def _identity(request) -> str:
        return getattr(request.state, "user", "supervisor")

    def _tenant_of(c, identity: str | None) -> str | None:
        """Caller tenant: supervisor (stack) sees all tenants; a user sees
        exactly its own tenant. Fail closed when the user has no tenant."""
        if identity == "supervisor":
            return None
        u = row(c, "SELECT tenant FROM users WHERE username=:u", u=identity)
        if not u or not u.get("tenant"):
            raise HTTPException(403, f"no tenant assigned to {identity}")
        return u["tenant"]

    def _scope(body: dict) -> tuple[str, str]:
        """Mandatory separate scope fields (S022): blank/missing/non-string refused."""
        tenant, project = body.get("tenant"), body.get("project")
        if not isinstance(tenant, str) or not tenant.strip():
            raise HTTPException(422, "tenant is required")
        if not isinstance(project, str) or not project.strip():
            raise HTTPException(422, "project is required")
        return tenant.strip(), project.strip()

    def _registered(c, tenant: str, project: str) -> dict:
        p = row(c, "SELECT * FROM projects WHERE tenant=:t AND project=:p", t=tenant, p=project)
        if not p:
            raise HTTPException(404, f"project not registered: create tenant {tenant!r},"
                                    f" then project {project!r}, before adding tasks")
        return p

    def _own_or_stack(c, tid, identity):
        """403 for a caller outside the task's tenant (S022 partition)."""
        t = task_or_404(c, tid)
        if identity != "supervisor" and t.get("tenant") != _tenant_of(c, identity):
            raise HTTPException(403, f"task in tenant {t.get('tenant')!r}")
        return t

    def _stack_only(identity):
        if identity != "supervisor":
            raise HTTPException(403, "worker-only operation")

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

    @app.post("/v1/users")
    def create_user(body: dict, request: Request):
        _stack_only(_identity(request))
        name = str(body.get("username") or "").strip()
        tenant = body.get("tenant")
        if not name or not body.get("token_hash"):
            raise HTTPException(422, "username and token_hash are required")
        if not isinstance(tenant, str) or not tenant.strip():
            raise HTTPException(422, "tenant is required")  # S022: no default tenant
        with eng.begin() as c:
            c.execute(text("INSERT INTO users(username,display_name,token_hash,tenant,created_at)"
                           " VALUES(:u,:d,:h,:t,:n) ON CONFLICT(username) DO UPDATE SET"
                           " token_hash=:h, display_name=:d, tenant=:t, disabled=0"),
                      {"u": name, "d": body.get("display_name", ""), "h": body["token_hash"],
                       "t": tenant.strip(), "n": _now()})
            return row(c, "SELECT username, display_name, tenant, disabled, created_at"
                          " FROM users WHERE username=:u", u=name)

    @app.get("/v1/users")
    def list_users(request: Request):
        _stack_only(_identity(request))
        with eng.begin() as c:
            return {"items": [dict(r) for r in c.execute(
                text("SELECT username, display_name, tenant, disabled, created_at"
                     " FROM users ORDER BY username")).mappings()]}

    @app.post("/v1/tenants")
    def create_tenant(body: dict, request: Request):
        """Register a tenant explicitly (S022). Idempotent: returns the row."""
        _stack_only(_identity(request))
        name = body.get("tenant")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(422, "tenant is required")
        with eng.begin() as c:
            c.execute(text("INSERT INTO tenants(tenant,created_at) VALUES(:t,:n)"
                           " ON CONFLICT(tenant) DO NOTHING"),
                      {"t": name.strip(), "n": _now()})
            return row(c, "SELECT * FROM tenants WHERE tenant=:t", t=name.strip())

    @app.post("/v1/projects")
    def create_project(body: dict, request: Request):
        """Register a project under an existing tenant (S022). Idempotent."""
        _stack_only(_identity(request))
        tenant, project = _scope(body)
        with eng.begin() as c:
            if not row(c, "SELECT * FROM tenants WHERE tenant=:t", t=tenant):
                raise HTTPException(404, f"tenant not registered: create {tenant!r} first")
            c.execute(text("INSERT INTO projects(tenant,project,created_at) VALUES(:t,:p,:n)"
                           " ON CONFLICT(tenant,project) DO NOTHING"),
                      {"t": tenant, "p": project, "n": _now()})
            return row(c, "SELECT * FROM projects WHERE tenant=:t AND project=:p",
                       t=tenant, p=project)

    @app.get("/v1/projects")
    def list_projects(request: Request, tenant: str | None = None):
        """Projects visible to the caller: its own tenant, or all for stack."""
        ident = _identity(request)
        with eng.begin() as c:
            mine = _tenant_of(c, ident)
            want = mine if mine is not None else tenant
            q = "SELECT tenant, project FROM projects"
            kw: dict = {}
            if want:
                q += " WHERE tenant=:t"
                kw["t"] = want
            rows = [dict(r) for r in c.execute(text(q + " ORDER BY tenant, project"), kw).mappings()]
            return {"projects": rows}

    @app.post("/v1/users/{username}/disable")
    def disable_user(username: str, request: Request):
        _stack_only(_identity(request))
        with eng.begin() as c:
            c.execute(text("UPDATE users SET disabled=1 WHERE username=:u"), {"u": username})
            return {"disabled": username}

    @app.post("/v1/tasks")
    def create_task(body: dict, request: Request):
        ident = _identity(request)
        tenant, project = _scope(body)  # S022: mandatory, never defaulted
        with eng.begin() as c:
            _registered(c, tenant, project)
            if _tenant_of(c, ident) not in (None, tenant):
                raise HTTPException(403, f"task in tenant {tenant!r}")
            n = _now()
            rid = c.execute(text("INSERT INTO tasks(title,intent,state,created_at,updated_at,created_by,"
                                 "tenant,project)"
                                 " VALUES(:t,:i,'open',:n,:n,:by,:tn,:p)"),
                            {"t": body.get("title", ""), "i": body.get("intent", ""), "n": n,
                             "by": ident, "tn": tenant, "p": project}).lastrowid
            return row(c, "SELECT * FROM tasks WHERE id=:i", i=rid)

    def _int_field(body: dict, field: str, default=None):
        # JSON ints only: int("1_0") == 10 and int(" 5 ") == 5 would
        # silently accept strings no client should send.
        v = body.get(field, default)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int):
            raise HTTPException(422, f"{field} must be an integer")
        return v

    def _ttl_field(body: dict) -> int:
        ttl = _int_field(body, "ttl_s", 120)
        if ttl is None or not 0 <= ttl <= 86400:  # negative = born expired; huge = _expiry OverflowError
            raise HTTPException(422, "ttl_s must be between 0 and 86400")
        return ttl

    @app.post("/v1/tasks_with_contract")
    def create_task_with_contract(body: dict, request: Request):
        contract = body.get("contract")
        ac = contract.get("acceptance_criteria") if isinstance(contract, dict) else None
        if not isinstance(ac, list) or not ac or not all(isinstance(x, str) and x.strip() for x in ac):
            raise HTTPException(422, "contract with acceptance_criteria is required")
        ident = _identity(request)
        tenant, project = _scope(body)  # S022: mandatory, never defaulted
        with eng.begin() as c:
            _registered(c, tenant, project)
            if _tenant_of(c, ident) not in (None, tenant):
                raise HTTPException(403, f"task in tenant {tenant!r}")
            n = _now()
            rid = c.execute(text("INSERT INTO tasks(title,intent,state,created_at,updated_at,created_by,"
                                 "tenant,project)"
                                 " VALUES(:t,:i,'open',:n,:n,:by,:tn,:p)"),
                            {"t": body.get("title", ""), "i": body.get("intent", ""), "n": n,
                             "by": ident, "tn": tenant, "p": project}).lastrowid
            s, h = _canon(contract)
            c.execute(text("INSERT INTO contracts(task_id,body,sha256,created_at) VALUES(:i,:b,:h,:n)"),
                      {"i": rid, "b": s, "h": h, "n": n})
            t = row(c, "SELECT * FROM tasks WHERE id=:i", i=rid)
            t["contracts"] = [dict(r) for r in c.execute(
                text("SELECT * FROM contracts WHERE task_id=:i ORDER BY id"), {"i": rid}).mappings()]
            return t

    @app.get("/v1/tasks")
    def list_tasks(request: Request, state: str | None = None, tenant: str | None = None):
        ident = _identity(request)
        with eng.begin() as c:
            mine = _tenant_of(c, ident)
            want = mine if mine is not None else tenant  # S022: users see their own tenant
            q, kw = "SELECT * FROM tasks", {}
            conds = []
            if state:
                conds.append("state=:s"); kw["s"] = state
            if want:
                conds.append("tenant=:t"); kw["t"] = want
            if conds:
                q += " WHERE " + " AND ".join(conds)
            return {"items": [dict(r) for r in c.execute(text(q + " ORDER BY id"), kw).mappings()]}

    @app.get("/v1/tasks/{tid}")
    def get_task(tid: int, request: Request):
        with eng.begin() as c:
            t = _own_or_stack(c, tid, _identity(request))
            t["contracts"] = [dict(r) for r in c.execute(text("SELECT * FROM contracts WHERE task_id=:i ORDER BY id"), {"i": tid}).mappings()]
            t["evidence"] = [dict(r) for r in c.execute(text("SELECT * FROM evidence WHERE task_id=:i ORDER BY id"), {"i": tid}).mappings()]
            t["receipts"] = [dict(r) for r in c.execute(text("SELECT * FROM receipts WHERE task_id=:i ORDER BY id"), {"i": tid}).mappings()]
            return t

    @app.post("/v1/tasks/{tid}/contracts")
    def add_contract(tid: int, body: dict, request: Request):
        s, h = _canon(body)
        with eng.begin() as c:
            _own_or_stack(c, tid, _identity(request))
            existing = row(c, "SELECT * FROM contracts WHERE task_id=:i AND sha256=:h", i=tid, h=h)
            if existing:
                return existing
            c.execute(text("INSERT INTO contracts(task_id,body,sha256,created_at) VALUES(:i,:b,:h,:n)"),
                      {"i": tid, "b": s, "h": h, "n": _now()})
            return row(c, "SELECT * FROM contracts WHERE task_id=:i AND sha256=:h", i=tid, h=h)

    @app.post("/v1/grants")
    def create_grant(body: dict, request: Request):
        """Standing grant: owner-approved bounded permission (T5)."""
        _stack_only(_identity(request))
        scope = str(body.get("scope") or "").strip()
        expires_at = str(body.get("expires_at") or "").strip()
        approved_by = str(body.get("approved_by") or "").strip()
        if not scope or not expires_at or not approved_by:
            raise HTTPException(422, "scope, expires_at and approved_by are required")
        with eng.begin() as c:
            rid = c.execute(text("INSERT INTO grants(scope,contract_rev,expires_at,approved_by,created_at)"
                                 " VALUES(:s,:r,:e,:a,:n)"),
                            {"s": scope, "r": str(body.get("contract_rev") or ""),
                             "e": expires_at, "a": approved_by, "n": _now()}).lastrowid
            return row(c, "SELECT * FROM grants WHERE id=:i", i=rid)

    @app.get("/v1/grants/active")
    def active_grant(scope: str, contract_rev: str | None = None):
        """Newest unexpired grant for scope (and contract_rev, when given)."""
        with eng.begin() as c:
            rows = [dict(r) for r in c.execute(
                text("SELECT * FROM grants WHERE scope=:s AND expires_at>:n ORDER BY id DESC"),
                {"s": scope, "n": _now()}).mappings()]
        if contract_rev:
            rows = [g for g in rows if not g["contract_rev"] or g["contract_rev"] == contract_rev]
        return {"active": bool(rows), "grant": rows[0] if rows else None}

    @app.get("/v1/tasks/{tid}/evidence/lookup")
    def lookup_evidence(tid: int, kind: str, fence: str, request: Request):
        _stack_only(_identity(request))
        """S014: single-row fence lookup (replaces client list-and-scan)."""
        with eng.begin() as c:
            task_or_404(c, tid)
            for r in c.execute(text("SELECT * FROM evidence WHERE task_id=:i AND kind=:k ORDER BY id"),
                               {"i": tid, "k": kind}).mappings():
                try:
                    body = json.loads(r["body"])
                except ValueError:
                    continue
                if isinstance(body, dict) and body.get("fence") == fence:
                    return dict(r)
            raise HTTPException(404, "evidence not found")

    @app.post("/v1/tasks/{tid}/evidence")
    def add_evidence(tid: int, body: dict, request: Request):
        s, h = _canon(body.get("body", {}))
        with eng.begin() as c:
            _own_or_stack(c, tid, _identity(request))
            rid = c.execute(text("INSERT INTO evidence(task_id,kind,body,sha256,created_at) VALUES(:i,:k,:b,:h,:n)"),
                            {"i": tid, "k": body.get("kind", "note"), "b": s, "h": h, "n": _now()}).lastrowid
            return row(c, "SELECT * FROM evidence WHERE id=:i", i=rid)

    @app.post("/v1/tasks/{tid}/transition")
    def transition(tid: int, body: dict, request: Request):
        _stack_only(_identity(request))
        """Idempotent state change. Requires idempotency_key; replays return the first receipt.
        Moving to 'done' requires a contract and at least one evidence row."""
        key = body.get("idempotency_key")
        to = body.get("to")
        if not key or not to:
            raise HTTPException(422, "idempotency_key and to are required")
        with eng.begin() as c:
            prior = row(c, "SELECT * FROM receipts WHERE task_id=:i AND idempotency_key=:k", i=tid, k=key)
            if prior:
                if prior.get("action") != f"transition:{to}":
                    raise HTTPException(409, f"idempotency key already used for {prior.get('action')}")
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
            return {"replayed": False, "receipt": row(c, "SELECT * FROM receipts WHERE task_id=:i AND idempotency_key=:k", i=tid, k=key)}

    # ---- R3 parity: assignment / lease / attempt / result ----
    def _expiry(ttl_s) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=ttl_s)).isoformat()

    def _receipted(c, tid, key):
        prior = row(c, "SELECT * FROM receipts WHERE task_id=:tid AND idempotency_key=:k", tid=tid, k=key)
        if prior:
            return {"replayed": True, "row": json.loads(prior["body"])}
        return None

    def _receipted_latest(c, tid, key):
        # Newest receipt for key or its generation suffixes (see _remember_gen),
        # scoped to this task: the same key on another task never replays here.
        # Legacy ::gen suffixes stay readable; new generations use \x1f so a
        # literal client key like K::gen1 cannot alias K's generations.
        r = row(c, "SELECT * FROM receipts WHERE task_id=:tid AND (idempotency_key=:k"
                   " OR (((substr(idempotency_key,1,length(:kpn))=:kpn"
                   " AND substr(idempotency_key,length(:kpn)+1) NOT GLOB '*[^0-9]*'"
                   " AND substr(idempotency_key,length(:kpn)+1) <> '')"
                   " OR (substr(idempotency_key,1,length(:kpo))=:kpo"
                   " AND substr(idempotency_key,length(:kpo)+1) NOT GLOB '*[^0-9]*'"
                   " AND substr(idempotency_key,length(:kpo)+1) <> ''))"
                   " AND EXISTS (SELECT 1 FROM receipts WHERE task_id=:tid AND idempotency_key=:k)))"
                   " ORDER BY id DESC LIMIT 1",
                tid=tid, k=key, kpn=key + "\x1fgen", kpo=key + "::gen")
        return {"replayed": True, "row": json.loads(r["body"])} if r else None

    def _remember(c, tid, key, action, payload: dict):
        s, _ = _canon(payload)
        c.execute(text("INSERT INTO receipts(task_id,idempotency_key,action,body,created_at) VALUES(:i,:k,:a,:b,:n)"),
                  {"i": tid, "k": key, "a": action, "b": s, "n": _now()})

    def _remember_gen(c, tid, key, action, payload: dict):
        # Append-only re-execution: keep the original receipt, store the
        # fresh one under a generation suffix counted from existing rows.
        # \x1f keeps generations out of the client key namespace; legacy
        # ::gen rows still count so numbering never collides with them.
        n = row(c, "SELECT COUNT(*) AS n FROM receipts WHERE task_id=:i AND (idempotency_key=:k"
                   " OR (substr(idempotency_key,1,length(:kpn))=:kpn"
                   " AND substr(idempotency_key,length(:kpn)+1) NOT GLOB '*[^0-9]*'"
                   " AND substr(idempotency_key,length(:kpn)+1) <> '')"
                   " OR (substr(idempotency_key,1,length(:kpo))=:kpo"
                   " AND substr(idempotency_key,length(:kpo)+1) NOT GLOB '*[^0-9]*'"
                   " AND substr(idempotency_key,length(:kpo)+1) <> ''))",
                i=tid, k=key, kpn=key + "\x1fgen", kpo=key + "::gen")["n"]
        _remember(c, tid, f"{key}\x1fgen{n}", action, payload)

    @app.get("/v1/tasks/{tid}/binding")
    def get_binding(tid: int, request: Request):
        _stack_only(_identity(request))
        with eng.begin() as c:
            task_or_404(c, tid)
            a = row(c, "SELECT * FROM assignments WHERE task_id=:i AND status='active' ORDER BY id DESC LIMIT 1", i=tid)
            if not a:
                raise HTTPException(404, "no active assignment")
            return {"assignment": a, "lease": row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)}

    @app.post("/v1/tasks/{tid}/assignments/claim")
    def claim_assignment(tid: int, body: dict, request: Request):
        _stack_only(_identity(request))
        key, principal = body.get("key"), body.get("principal", "")
        attempt_no, ttl_s = _int_field(body, "attempt_no", 1), _ttl_field(body)
        if not key or not principal:
            raise HTTPException(422, "key and principal are required")
        with eng.begin() as c:
            task_or_404(c, tid)
            stale_key = False
            if (hit := _receipted_latest(c, tid, key)):
                prev = (hit["row"] or {}).get("assignment") or hit["row"] or {}
                if prev.get("principal") not in (None, principal):
                    raise HTTPException(409, "claim key already used by another principal")
                if prev.get("attempt_no") is not None and int(prev["attempt_no"]) != attempt_no:
                    raise HTTPException(409, "claim key already used for another attempt_no")
                cur = row(c, "SELECT * FROM assignments WHERE id=:a", a=prev.get("id"))
                if cur and cur["status"] == "active":
                    lease_now = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
                    if cur.get("principal") != principal or not lease_now or lease_now.get("holder") != principal:
                        raise HTTPException(409, "claim superseded by another principal")
                    if lease_now.get("expires_at") and lease_now["expires_at"] > _now():
                        return {"replayed": True, **hit["row"]}
                    # live lease lapsed: stale fallthrough to re-claim (new TTL/epoch)
                stale_key = True  # released since: fall through and re-claim
                if row(c, "SELECT id FROM assignments WHERE task_id=:i AND status='active' AND id<>:a",
                       i=tid, a=prev.get("id")):
                    raise HTTPException(409, "another assignment is still active")
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            if lease and lease["expires_at"] > _now() and lease["holder"] != principal:
                raise HTTPException(409, "lease held by another holder")
            epoch = (lease["lease_epoch"] + 1) if lease else 1
            existing = row(c, "SELECT * FROM assignments WHERE task_id=:i AND attempt_no=:n",
                           i=tid, n=attempt_no)
            if existing and (existing["status"] == "released" or stale_key):
                c.execute(text("UPDATE assignments SET status='active', principal=:p WHERE id=:a"),
                          {"p": principal, "a": existing["id"]})
            elif existing:
                raise HTTPException(409, f"attempt_no {attempt_no} already claimed")
            else:
                try:
                    c.execute(text("INSERT INTO assignments(task_id,principal,attempt_no,created_at) VALUES(:i,:p,:n,:t)"),
                              {"i": tid, "p": principal, "n": attempt_no, "t": _now()})
                except Exception as exc:
                    raise HTTPException(409, f"attempt_no {attempt_no} already claimed") from exc
            c.execute(text("INSERT INTO leases(task_id,holder,lease_epoch,expires_at,updated_at) VALUES(:i,:h,:e,:x,:t) "
                            "ON CONFLICT(task_id) DO UPDATE SET holder=:h, lease_epoch=:e, expires_at=:x, updated_at=:t"),
                      {"i": tid, "h": principal, "e": epoch, "x": _expiry(ttl_s), "t": _now()})
            a = row(c, "SELECT * FROM assignments WHERE task_id=:i AND attempt_no=:n", i=tid, n=attempt_no)
            # Worker pickup is supervisor-visible: open/needs_review -> claimed.
            c.execute(text("UPDATE tasks SET state='claimed', updated_at=:t "
                           "WHERE id=:i AND state IN ('open','needs_review','claimed')"),
                      {"i": tid, "t": _now()})
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            payload = {"assignment": a, "lease_epoch": epoch, "lease": lease}
            if stale_key:
                _remember_gen(c, tid, key, "claim", payload)
            else:
                _remember(c, tid, key, "claim", payload)
            return {"replayed": False, **payload}

    @app.post("/v1/tasks/{tid}/leases/renew")
    def renew_lease(tid: int, body: dict, request: Request):
        _stack_only(_identity(request))
        key, holder = body.get("key"), body.get("holder", "")
        expected, ttl_s = _int_field(body, "expected_lease_epoch"), _ttl_field(body)
        if not key or not holder or expected is None:
            raise HTTPException(422, "key, holder and expected_lease_epoch are required")
        with eng.begin() as c:
            task_or_404(c, tid)
            stale_key = False
            if (hit := _receipted_latest(c, tid, key)):
                stored = hit["row"] or {}
                if stored.get("holder") not in (None, holder) or (
                        stored.get("lease_epoch") is not None and int(stored["lease_epoch"]) != int(expected)):
                    raise HTTPException(409, "renew replay holder or epoch mismatch")
                if stored.get("expires_at", "") > _now():
                    return {"replayed": True, "lease": hit["row"]}
                stale_key = True  # stored lease lapsed: fall through and extend
                if not row(c, "SELECT id FROM assignments WHERE task_id=:i AND status='active'", i=tid):
                    raise HTTPException(409, "no active assignment to renew")
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            if not lease:
                raise HTTPException(404, "no lease")
            if lease["holder"] != holder or int(lease["lease_epoch"]) != int(expected):
                raise HTTPException(409, "stale epoch or wrong holder")
            if lease["expires_at"] <= _now() and not stale_key:
                raise HTTPException(409, "lease expired")
            c.execute(text("UPDATE leases SET expires_at=:x, updated_at=:t WHERE task_id=:i"),
                      {"x": _expiry(ttl_s), "t": _now(), "i": tid})
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            if stale_key:
                _remember_gen(c, tid, key, "renew", lease)
            else:
                _remember(c, tid, key, "renew", lease)
            return {"replayed": False, "lease": lease}

    @app.post("/v1/tasks/{tid}/assignments/reclaim")
    def reclaim_assignment(tid: int, body: dict, request: Request):
        _stack_only(_identity(request))
        key = body.get("key")
        if not key:
            raise HTTPException(422, "key is required")
        with eng.begin() as c:
            task_or_404(c, tid)
            stale_key = False
            if (hit := _receipted(c, tid, key)):
                lease0 = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
                active = row(c, "SELECT id FROM assignments WHERE task_id=:i AND status='active'", i=tid)
                if not (active and (lease0 is None or lease0["expires_at"] <= _now())):
                    return {"replayed": True, **hit["row"]}
                stale_key = True  # died again on a later cycle: re-execute below
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            if lease and lease["expires_at"] > _now():
                raise HTTPException(409, "lease still live")
            if lease:
                # Tombstone, not DELETE: next claim must increment the epoch (fencing).
                c.execute(text("UPDATE leases SET expires_at='2000-01-01T00:00:00+00:00', updated_at=:t WHERE task_id=:i"),
                          {"i": tid, "t": _now()})
            c.execute(text("UPDATE assignments SET status='released' WHERE task_id=:i AND status='active'"), {"i": tid})
            out = {"reclaimed": True}
            if stale_key:
                _remember_gen(c, tid, key, "reclaim", out)
            else:
                _remember(c, tid, key, "reclaim", out)
            return {"replayed": False, **out}

    @app.post("/v1/tasks/{tid}/attempts")
    def open_attempt(tid: int, body: dict, request: Request):
        _stack_only(_identity(request))
        key, attempt_no, epoch = body.get("key"), _int_field(body, "attempt_no"), _int_field(body, "lease_epoch")
        if not key or attempt_no is None or epoch is None:
            raise HTTPException(422, "key, attempt_no and lease_epoch are required")
        with eng.begin() as c:
            task_or_404(c, tid)

            def _reuse(att):
                # Resumed after reclaim: re-fence the reused attempt to the
                # live epoch so record_result(epoch=live) can succeed.
                if not att or att.get("id") is None:
                    return att
                lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
                has_result = row(c, "SELECT id FROM results WHERE task_id=:i AND attempt_id=:a",
                                 i=tid, a=att["id"])
                if lease and not has_result and int(att["lease_epoch"]) != int(lease["lease_epoch"]):
                    c.execute(text("UPDATE attempts SET lease_epoch=:e WHERE id=:a"),
                              {"e": int(lease["lease_epoch"]), "a": att["id"]})
                    return row(c, "SELECT * FROM attempts WHERE id=:a", a=att["id"])
                return att

            if (hit := _receipted(c, tid, key)):
                stored = row(c, "SELECT * FROM attempts WHERE id=:a AND task_id=:i",
                             a=(hit["row"] or {}).get("id"), i=tid) or hit["row"]
                return {"replayed": True, "attempt": _reuse(stored)}
            existing = row(c, "SELECT * FROM attempts WHERE task_id=:i AND attempt_no=:n", i=tid, n=attempt_no)
            if existing:
                return {"replayed": True, "attempt": _reuse(existing)}
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            if not lease or int(lease["lease_epoch"]) != int(epoch):
                raise HTTPException(409, "attempt epoch does not match live lease")
            if lease["expires_at"] <= _now():
                raise HTTPException(409, "lease expired")
            c.execute(text("INSERT INTO attempts(task_id,attempt_no,lease_epoch,created_at) VALUES(:i,:n,:e,:t)"),
                      {"i": tid, "n": attempt_no, "e": epoch, "t": _now()})
            a = row(c, "SELECT * FROM attempts WHERE task_id=:i AND attempt_no=:n", i=tid, n=attempt_no)
            _remember(c, tid, key, "attempt", a)
            return {"replayed": False, "attempt": a}

    @app.post("/v1/tasks/{tid}/results")
    def record_result(tid: int, body: dict, request: Request):
        _stack_only(_identity(request))
        key, attempt_id, epoch = body.get("key"), _int_field(body, "attempt_id"), _int_field(body, "lease_epoch")
        verdict, result = body.get("verdict", ""), body.get("result", {})
        if not key or attempt_id is None or epoch is None or not verdict:
            raise HTTPException(422, "key, attempt_id, lease_epoch and verdict are required")
        core, _ = _canon({"attempt_id": attempt_id, "lease_epoch": epoch, "verdict": verdict, "result": result})
        with eng.begin() as c:
            task_or_404(c, tid)
            if (hit := _receipted(c, tid, key)):
                return {"replayed": True, "result": hit["row"]}
            existing = row(c, "SELECT * FROM results WHERE task_id=:i AND attempt_id=:a", i=tid, a=attempt_id)
            if existing:
                if existing["body"] != core:
                    raise HTTPException(409, "REPLAY_CONFLICT: same attempt, different result core")
                return {"replayed": True, "result": existing}
            attempt = row(c, "SELECT * FROM attempts WHERE id=:a AND task_id=:i", a=attempt_id, i=tid)
            if not attempt or int(attempt["lease_epoch"]) != int(epoch):
                raise HTTPException(409, "attempt not found for task or epoch mismatch")
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            if not lease or int(lease["lease_epoch"]) != epoch:
                raise HTTPException(409, "result epoch does not match live lease")
            if lease["expires_at"] <= _now():
                raise HTTPException(409, "lease expired")
            c.execute(text("INSERT INTO results(task_id,attempt_id,lease_epoch,verdict,body,created_at) VALUES(:i,:a,:e,:v,:b,:t)"),
                      {"i": tid, "a": attempt_id, "e": epoch, "v": verdict, "b": core, "t": _now()})
            r = row(c, "SELECT * FROM results WHERE task_id=:i AND attempt_id=:a", i=tid, a=attempt_id)
            _remember(c, tid, key, "result", r)
            return {"replayed": False, "result": r}

    return app


app = make_app()
