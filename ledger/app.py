"""Rouvr ledger: a slim WorkHub-lite (first pass).

Durable tasks, done-contracts, evidence and idempotent receipts over SQL.
Set ROUVR_LEDGER_DATABASE_URL to a sqlite:/// URL or it defaults to a
local SQLite file. SQLite only: a postgres URL fails fast in make_app
(the schema and lastrowid usage are SQLite-specific). Not hardened:
no auth beyond tokens. Tenants own projects own tasks (S022 partitions);
both scope fields are mandatory on every create, never defaulted.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import secrets as _secrets

from fastapi import FastAPI, HTTPException, Request
from rapidfuzz import fuzz
from sqlalchemy import create_engine, event, text

try:
    from ledger import auth as _authmod
    from ledger import chat as _chatmod
except ImportError:  # run as a bare module (python ledger/app.py)
    import auth as _authmod  # type: ignore[no-redef]
    import chat as _chatmod  # type: ignore[no-redef]

try:
    from ledger import hyeve_auth as _ha
except ImportError:  # run as a bare module (python ledger/app.py)
    import hyeve_auth as _ha  # type: ignore[no-redef]

DB_URL = os.environ.get("ROUVR_LEDGER_DATABASE_URL", "sqlite:///./.rouvr/ledger.db")
VERSION = "0.1.0"


def _build_sha() -> str:
    """Short git HEAD sha, or "dev" when git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        sha = (out.stdout or "").strip()
        if out.returncode != 0 or not sha:
            return "dev"
        return sha
    except Exception:
        return "dev"


BUILD = _build_sha()
TRANSITIONS = {
    "open": {"claimed", "blocked", "retired"},
    "claimed": {"done", "failed", "blocked", "open", "needs_review"},
    "needs_review": {"done", "blocked", "claimed"},
    "blocked": {"open", "retired"},
    "failed": {"open", "retired"},
    "done": set(),
    "retired": set(),
}

# FL-A4 event catalog (docs/plans/2026-11-02-frontend-lite.md §4): the ONLY
# live channel. Every UI animation binds 1:1 to one of these types. Outbox
# rows are written transactionally by the mutation that produced them, so a
# dead worker yields silence — the UI derives stuck/idle from heartbeat
# absence — never fake progress.
EVIDENCE_EVENT_KINDS = {
    "step": "task.step",
    "tests": "task.tests",
    "test_output": "task.tests",
    "criteria": "task.criteria",
    "reviewer": "review.note",
    "review": "review.note",
    "review_note": "review.note",
    "receipt": "receipt.signed",
    "escalation": "escalation.opened",
    "escalation_resolved": "escalation.resolved",
}
EVENT_CATALOG = {
    "task.created": {"emitter": "POST /v1/tasks, /v1/tasks_with_contract",
                     "payload": "task_id, title, state"},
    "task.transition": {"emitter": "POST /v1/tasks/{id}/transition;"
                                   " claim side-effect open/needs_review -> claimed",
                        "payload": "task_id, from, to, owner?"},
    "task.step": {"emitter": "evidence kind=step (worker-provided, forwarded verbatim)",
                  "payload": "evidence_id + worker body (step/total/label...)"},
    "task.tests": {"emitter": "evidence kind=tests|test_output (worker-provided)",
                   "payload": "evidence_id + worker body (passed/total...)"},
    "task.criteria": {"emitter": "evidence kind=criteria (worker-provided)",
                      "payload": "evidence_id + worker body (done/total/flipped...)"},
    "attempt.started": {"emitter": "POST /v1/tasks/{id}/attempts (fresh only, replays silent)",
                        "payload": "task_id, attempt_id, attempt_no, lease_epoch"},
    "attempt.finished": {"emitter": "POST /v1/tasks/{id}/results (fresh only)",
                         "payload": "task_id, attempt_id, verdict"},
    "review.note": {"emitter": "evidence kind=reviewer|review|review_note",
                    "payload": "evidence_id + reviewer body (verdict...)"},
    "receipt.signed": {"emitter": "evidence kind=receipt (HMAC receipt recorded as evidence)",
                       "payload": "evidence_id + receipt body"},
    "escalation.opened": {"emitter": "evidence kind=escalation",
                          "payload": "evidence_id + body (reason...)"},
    "escalation.resolved": {"emitter": "evidence kind=escalation_resolved",
                            "payload": "evidence_id + body"},
    "knowledge.versioned": {"emitter": "knowledge insert"
                                       " (create/store/version/compact/snapshot/onboard)",
                            "payload": "id, citation, kind, version, supersedes_id"
                                       " (no title/body: visibility-safe)"},
    "knowledge.confirmed": {"emitter": "POST /v1/knowledge/{id}/confirm",
                            "payload": "id, citation"},
    "worker.heartbeat": {"emitter": "POST /v1/workers/heartbeat; fresh lease renew",
                         "payload": "task_id, holder, lease_epoch?, lease_holder?,"
                                    " lease_expires_at? — drives stuck/idle, never progress"},
    "task.evidence": {"emitter": "any other evidence kind (generic fallback)",
                      "payload": "evidence_id, kind + body"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(title: str) -> str:
    """Deterministic kebab-case slug from a title. Stable: same title in the
    same scope always yields the same base; collisions get -2/-3 suffixes."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return (slug[:60].rstrip("-")) or "entry"


def _summarize_text(body: str) -> str:
    """One line, <=160 chars: first non-empty line, whitespace collapsed."""
    for ln in (body or "").splitlines():
        s = re.sub(r"\s+", " ", ln).strip()
        if s:
            return s[:160]
    return ""


def _tags_norm(raw) -> list[str]:
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for t in items:
        s = re.sub(r"[^a-z0-9-]+", "-", str(t).lower()).strip("-")[:40]
        if s and s not in out:
            out.append(s)
        if len(out) >= 10:
            break
    return out


def _semantic_candidates(q: str, limit: int = 50) -> list[tuple[int, float]]:
    """D18 placeholder: sqlite-vec + fastembed semantic layer (NOT BUILT).

    Honest stub: always returns []. search_knowledge calls it as an
    optional RRF run, so a future implementation plugs in here without
    touching the search path. Not a fallback, not a fake: when this
    returns nothing, ranking is purely FTS + RapidFuzz."""
    return []


def _knowledge_search_schema(c) -> None:
    """Migrate search indexes + backfill slug/tags/summary in place.

    New metadata columns are derived, not authored history: rows keep their
    ids, versions and verified flags (like confirm's in-place flag), so no
    new versions are minted. FTS tables are rebuilt when their shape lags.
    """
    cols = [r[1] for r in c.execute(text("PRAGMA table_info(knowledge_fts)"))]
    fts_sql = c.execute(text("SELECT sql FROM sqlite_master WHERE name='knowledge_fts'")).scalar() or ""
    if not set(("slug", "summary", "tags")).issubset(set(cols)) or "porter" not in fts_sql:
        c.execute(text("DROP TABLE IF EXISTS knowledge_fts"))
        c.execute(text("CREATE VIRTUAL TABLE knowledge_fts USING fts5(title, body, slug, summary, tags,"
                       " tokenize='porter unicode61')"))
        needs_rebuild = True
    else:
        needs_rebuild = False
    c.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_trigram"
                   " USING fts5(title, slug, tags, summary, tokenize='trigram')"))
    n_entries = c.execute(text("SELECT COUNT(*) FROM knowledge_entries")).scalar()
    n_fts = c.execute(text("SELECT COUNT(*) FROM knowledge_fts")).scalar()
    n_tri = c.execute(text("SELECT COUNT(*) FROM knowledge_trigram")).scalar()
    # Backfill derived metadata for pre-schema rows, oldest first so the
    # first title in a scope keeps the bare slug and later ones suffix.
    rows = c.execute(text("SELECT id, tenant, project, kind, title, body, slug FROM knowledge_entries"
                          " WHERE slug='' OR slug IS NULL ORDER BY id")).mappings().all()
    taken: dict[tuple[str, str], set[str]] = {}
    if rows:
        have = c.execute(text("SELECT tenant, project, slug FROM knowledge_entries"
                              " WHERE slug<>''")).mappings().all()
        for h in have:
            taken.setdefault((h["tenant"], h["project"]), set()).add(h["slug"])
    for r in rows:
        base = _slugify(r["title"])
        scope = (r["tenant"], r["project"])
        seen = taken.setdefault(scope, set())
        slug, i = base, 2
        while slug in seen:
            slug = f"{base}-{i}"
            i += 1
        seen.add(slug)
        c.execute(text("UPDATE knowledge_entries SET slug=:s, tags=:t, summary=:u WHERE id=:i"),
                  {"s": slug, "t": json.dumps(_tags_norm([r["kind"]])),
                   "u": _summarize_text(r["body"]), "i": r["id"]})
    if needs_rebuild or n_fts != n_entries or (rows and True):
        c.execute(text("DELETE FROM knowledge_fts"))
        for e in c.execute(text("SELECT id, title, body, slug, summary, tags"
                                " FROM knowledge_entries")).mappings():
            c.execute(text("INSERT INTO knowledge_fts(rowid,title,body,slug,summary,tags)"
                           " VALUES(:i,:t,:b,:s,:u,:g)"),
                      {"i": e["id"], "t": e["title"], "b": e["body"],
                       "s": e["slug"] or "", "u": e["summary"] or "",
                       "g": " ".join(json.loads(e["tags"] or "[]"))})
    if n_tri != n_entries:
        c.execute(text("DELETE FROM knowledge_trigram"))
        for e in c.execute(text("SELECT id, title, slug, tags, summary"
                                " FROM knowledge_entries")).mappings():
            c.execute(text("INSERT INTO knowledge_trigram(rowid,title,slug,tags,summary)"
                           " VALUES(:i,:t,:s,:g,:u)"),
                      {"i": e["id"], "t": e["title"], "s": e["slug"] or "",
                       "g": " ".join(json.loads(e["tags"] or "[]")),
                       "u": e["summary"] or ""})


_ONBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rouvr — Register &amp; onboard</title>
<style>
 body{font-family:ui-sans-serif,system-ui,sans-serif;max-width:640px;margin:2rem auto;padding:0 1rem;color:#1c2430;background:#fff}
 h1{font-size:1.4rem} h2{font-size:1.05rem;margin-top:2rem}
 label{display:block;margin:.7rem 0 .2rem;font-weight:600;font-size:.9rem}
 input,select,textarea{width:100%;padding:.5rem;border:1px solid #9aa6b5;border-radius:4px;font:inherit}
 button{margin-top:1rem;padding:.55rem 1.2rem;border:0;border-radius:4px;background:#1668b4;color:#fff;font:inherit;cursor:pointer}
 button:disabled{background:#7d8ea3}
 .err{color:#b3261e;white-space:pre-wrap} .ok{background:#eef6ee;border:1px solid #9dbb9d;padding:1rem;border-radius:4px;white-space:pre-wrap}
 .hidden{display:none} small{color:#45536a}
</style></head><body>
<h1>Rouvr — Register &amp; onboard</h1>
<p>Step 1 creates your account on the Hyeve auth service. Step 2 tells Rouvr
who you are and what your first project is; both profiles are stored in
project knowledge, versioned and searchable.</p>

<h2>1. Register</h2>
<form id="reg">
 <label>Username <input name="username" required autocomplete="username"></label>
 <label>Email <input name="email" type="email" required autocomplete="email"></label>
 <label>Password <input name="password" type="password" required autocomplete="new-password"></label>
 <label>Given name <input name="given_name" required autocomplete="given-name"></label>
 <label>Family name <input name="family_name" required autocomplete="family-name"></label>
 <button>Register</button> <span id="reg-msg"></span>
</form>
<p><small>Already registered? <a href="#" id="show-login">Log in instead</a></small></p>
<form id="login" class="hidden">
 <label>Username <input name="username" required autocomplete="username"></label>
 <label>Password <input name="password" type="password" required autocomplete="current-password"></label>
 <button>Log in</button> <span id="login-msg"></span>
</form>

<h2>2. About you and your project</h2>
<form id="onb">
 <label>Display name <input name="display_name" required></label>
 <label>Your role <input name="role_title" placeholder="Founder, ops lead, ..."></label>
 <label>Organisation <input name="organisation" placeholder="Acme Legal"></label>
 <label>What will you use Rouvr for? <textarea name="use_case" rows="2"></textarea></label>
 <label>Project name <input name="project_name" required></label>
 <label>Project purpose <textarea name="project_purpose" rows="2" required></textarea></label>
 <label>Project type <select name="project_type">
  <option>software</option><option>operations</option><option>content</option>
  <option>research</option><option>mixed</option></select></label>
 <label>North star / success measure <input name="north_star"></label>
 <label>Standing rules for agents <textarea name="standing_rules" rows="2" placeholder="e.g. Cite every source."></textarea></label>
 <button id="onb-btn" disabled>Complete onboarding</button>
</form>
<p id="msg" class="err"></p>
<div id="result" class="ok hidden"></div>
<script>
let token = null;
const msg = t => { document.getElementById('msg').textContent = t || ''; };
async function post(path, data, headers) {
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json', ...(headers||{})}, body: JSON.stringify(data)});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || ('HTTP ' + r.status));
  return j;
}
const collect = f => Object.fromEntries(new FormData(f).entries());
function armed(t) { token = t; document.getElementById('onb-btn').disabled = !t; }
document.getElementById('reg').onsubmit = async e => { e.preventDefault(); msg('');
  try { const j = await post('/v1/onboard/register', collect(e.target)); armed(j.access_token);
    document.getElementById('reg-msg').textContent = 'Registered. Now step 2.'; }
  catch (err) { msg(err.message); } };
document.getElementById('show-login').onclick = e => { e.preventDefault();
  document.getElementById('login').classList.remove('hidden'); };
document.getElementById('login').onsubmit = async e => { e.preventDefault(); msg('');
  try { const j = await post('/v1/onboard/login', collect(e.target)); armed(j.access_token);
    document.getElementById('login-msg').textContent = 'Logged in. Now step 2 (or done if already onboarded).'; }
  catch (err) { msg(err.message); } };
document.getElementById('onb').onsubmit = async e => { e.preventDefault(); msg('');
  try { const j = await post('/v1/onboard/complete', collect(e.target), {Authorization: 'Bearer ' + token});
    const box = document.getElementById('result'); box.classList.remove('hidden');
    box.textContent = 'Welcome, ' + j.username + '.\\nTenant: ' + j.tenant + '  Project: ' + j.project +
      (j.user_profile ? '\\nUser profile: ' + j.user_profile + '\\nProject profile: ' + j.project_profile : '\\n(already onboarded)') +
      (j.rouvr_token ? '\\nYour Rouvr API token (shown once): ' + j.rouvr_token : ''); }
  catch (err) { msg(err.message); } };
</script></body></html>"""


def _canon(obj) -> tuple[str, str]:
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return s, hashlib.sha256(s.encode()).hexdigest()


def _sse_frame(oid: int, etype: str, tenant: str, project: str,
               task_id: int | None, created_at: str, payload: dict) -> str:
    """One SSE event: id: (resume cursor) + event: (type) + data: (envelope)."""
    data, _ = _canon({"id": oid, "type": etype, "tenant": tenant, "project": project,
                      "task_id": task_id, "created_at": created_at, "payload": payload})
    return f"id: {oid}\nevent: {etype}\ndata: {data}\n\n"


def make_app(db_url: str = DB_URL) -> FastAPI:
    if db_url.startswith("postgres"):  # advertised once, never worked: fail fast, not per-insert
        raise ValueError("ledger supports SQLite only: set ROUVR_LEDGER_DATABASE_URL "
                         "to a sqlite:/// URL (Postgres is not supported)")
    if db_url.startswith("sqlite:///./"):
        Path(db_url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    if db_url.startswith("sqlite"):
        # FL-A5: single-writer discipline — one pooled connection per app, so
        # concurrent API threads serialize instead of hitting "database is
        # locked"; cross-process writers wait on the 5s busy timeout below.
        eng = create_engine(
            db_url,
            connect_args={"timeout": 5.0, "check_same_thread": False},
            pool_size=1,
            max_overflow=0,
            pool_timeout=30,
        )

        @event.listens_for(eng, "connect")
        def _sqlite_hardening(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()
    else:
        eng = create_engine(db_url)
    with eng.begin() as c:
        for stmt in (Path(__file__).with_name("schema.sql").read_text().split(";")):
            body = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--"))
            if body.strip():
                c.execute(text(body))
    app = FastAPI(title="Rouvr ledger")
    app.state.engine = eng  # FL-A5: hardened SQLite config, observable for tests

    # Step 1: local session auth (FastAPI Users, argon2, cookie transport).
    # Coexists with token auth; cookie sessions additionally pass CSRF.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    _auth_secret = os.environ.get("ROUVR_AUTH_SECRET") or _secrets.token_hex(32)
    if not os.environ.get("ROUVR_AUTH_SECRET"):
        log_warn = __import__("logging").getLogger(__name__)
        log_warn.warning("ROUVR_AUTH_SECRET unset: sessions invalidate on restart")
    _async_eng = create_async_engine(_authmod.sqlite_async_url(db_url))
    _session_factory = async_sessionmaker(_async_eng, expire_on_commit=False)
    _cookie_secure = _authmod.cookie_secure_for_host(
        os.environ.get("ROUVR_BIND_HOST", "127.0.0.1"))
    _auth_router, _transport, _strategy, _throttle = _authmod.build_auth_router(
        secret=_auth_secret, cookie_secure=_cookie_secure,
        get_session=_session_factory)
    app.include_router(_auth_router)
    app.state.auth_secret = _auth_secret
    app.state.auth_session_factory = _session_factory
    app.state.auth_throttle = _throttle
    app.state.auth_cookie_name = _transport.cookie_name

    # Sync seed on the sync engine: no asyncio.run (make_app must stay
    # callable from async tests) and no ledger side effects — the seed
    # touches only the auth `user` table. Tenant/project scope is
    # registered explicitly through the API (S022), never auto-created.
    _pw, _generated = _authmod.seed_admin(os.environ.get("ROUVR_ADMIN_PASSWORD"), eng)
    if _generated:
        _authmod.emit_secret_once(_pw)

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
        pcols = [r[1] for r in c.execute(text("PRAGMA table_info(projects)"))]
        # Project metadata (UX create form): nullable, added without
        # backfill — older rows simply read back nulls.
        for col in ("title", "code", "description", "domain"):
            if pcols and col not in pcols:
                c.execute(text(f"ALTER TABLE projects ADD COLUMN {col} TEXT"))
        tcols = [r[1] for r in c.execute(text("PRAGMA table_info(tasks)"))]
        for col in ("tenant", "project"):
            if tcols and col not in tcols:
                c.execute(text(f"ALTER TABLE tasks ADD COLUMN {col} TEXT"))
        ucols = [r[1] for r in c.execute(text("PRAGMA table_info(users)"))]
        if ucols and "tenant" not in ucols:
            c.execute(text("ALTER TABLE users ADD COLUMN tenant TEXT"))
        # Knowledge Lite on pre-existing databases (schema.sql covers fresh).
        c.execute(text("CREATE TABLE IF NOT EXISTS knowledge_entries ("
                       "id INTEGER PRIMARY KEY, tenant TEXT NOT NULL, project TEXT NOT NULL,"
                       "kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,"
                       "version INTEGER NOT NULL DEFAULT 1,"
                       "supersedes_id INTEGER REFERENCES knowledge_entries(id),"
                       "verified INTEGER NOT NULL DEFAULT 0, author TEXT NOT NULL,"
                       "visibility TEXT NOT NULL DEFAULT 'company',"
                       "allowed_users TEXT NOT NULL DEFAULT '[]',"
                       "created_at TEXT NOT NULL,"
                       "FOREIGN KEY (tenant, project) REFERENCES projects(tenant, project))"))
        kcols = [r[1] for r in c.execute(text("PRAGMA table_info(knowledge_entries)"))]
        if kcols and "visibility" not in kcols:
            c.execute(text("ALTER TABLE knowledge_entries ADD COLUMN visibility TEXT NOT NULL DEFAULT 'company'"))
        if kcols and "allowed_users" not in kcols:
            c.execute(text("ALTER TABLE knowledge_entries ADD COLUMN allowed_users TEXT NOT NULL DEFAULT '[]'"))
        for col, ddl in (("slug", "TEXT NOT NULL DEFAULT ''"),
                         ("tags", "TEXT NOT NULL DEFAULT '[]'"),
                         ("summary", "TEXT NOT NULL DEFAULT ''")):
            if kcols and col not in kcols:
                c.execute(text(f"ALTER TABLE knowledge_entries ADD COLUMN {col} {ddl}"))
        for col, ddl in (("review_state", "TEXT NOT NULL DEFAULT 'pending'"),
                         ("review_note", "TEXT NOT NULL DEFAULT ''"),
                         ("archived", "INTEGER NOT NULL DEFAULT 0"),
                         ("pinned", "INTEGER NOT NULL DEFAULT 0")):
            if kcols and col not in kcols:
                c.execute(text(f"ALTER TABLE knowledge_entries ADD COLUMN {col} {ddl}"))
        prcols = [r[1] for r in c.execute(text("PRAGMA table_info(projects)"))]
        if prcols and "archived" not in prcols:
            c.execute(text("ALTER TABLE projects ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"))
        c.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(title, body)"))
        _knowledge_search_schema(c)
        # Slice C intake router on pre-existing databases (schema.sql covers fresh).
        c.execute(text("CREATE TABLE IF NOT EXISTS chat_turns ("
                       "id INTEGER PRIMARY KEY, tenant TEXT NOT NULL, project TEXT NOT NULL,"
                       "message_id TEXT NOT NULL, message TEXT NOT NULL, reply TEXT NOT NULL,"
                       "class TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)"))
        tcols2 = [r[1] for r in c.execute(text("PRAGMA table_info(chat_turns)"))]
        if tcols2 and "class" not in tcols2:
            c.execute(text("ALTER TABLE chat_turns ADD COLUMN class TEXT NOT NULL DEFAULT ''"))
        c.execute(text("CREATE INDEX IF NOT EXISTS chat_turns_scope"
                       " ON chat_turns(tenant, project, id)"))
        c.execute(text("CREATE TABLE IF NOT EXISTS intake_classifications ("
                       "id INTEGER PRIMARY KEY, tenant TEXT NOT NULL, project TEXT NOT NULL,"
                       "turn_id TEXT NOT NULL, class TEXT NOT NULL,"
                       "confidence REAL NOT NULL DEFAULT 0.5, category TEXT NOT NULL DEFAULT '',"
                       "created_ids TEXT NOT NULL DEFAULT '{}',"
                       "autonomy_level TEXT NOT NULL DEFAULT 'ask-for-everything',"
                       "decided_by TEXT NOT NULL DEFAULT '', decided_at TEXT NOT NULL DEFAULT '',"
                       "reclass_of INTEGER REFERENCES intake_classifications(id),"
                       "created_at TEXT NOT NULL)"))
        c.execute(text("CREATE INDEX IF NOT EXISTS intake_class_scope_turn"
                       " ON intake_classifications(tenant, project, turn_id)"))
        c.execute(text("CREATE TABLE IF NOT EXISTS chat_autonomy ("
                       "tenant TEXT NOT NULL, project TEXT NOT NULL, level TEXT NOT NULL,"
                       "updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,"
                       "PRIMARY KEY (tenant, project))"))
        c.execute(text("CREATE TABLE IF NOT EXISTS external_identities ("
                       "provider TEXT NOT NULL, subject TEXT NOT NULL,"
                       "username TEXT NOT NULL REFERENCES users(username),"
                       "created_at TEXT NOT NULL, PRIMARY KEY (provider, subject))"))
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

    # Token auth (S011 + S016, partitions S022, fail-closed Knowledge Lite):
    # the shared stack token authenticates as "supervisor" (stack/worker
    # identity, sees all). A per-user token authenticates as that user,
    # scoped to exactly its own tenant's projects and tasks. There is no
    # open-dev mode: with no token configured every /v1 request fails 401.
    # Local dev bypass requires explicit ROUVR_INSECURE_DEV=1 (loud warning,
    # refused on a public bind).
    import sys as _sys
    _token = os.environ.get("ROUVR_LEDGER_TOKEN", "")
    _insecure = os.environ.get("ROUVR_INSECURE_DEV", "") == "1"
    _bind_host = os.environ.get("ROUVR_BIND_HOST",
                                os.environ.get("ROUVR_LEDGER_HOST", "127.0.0.1"))
    _public_bind = _bind_host.strip() in ("", "*", "0.0.0.0", "::",
                                          "0:0:0:0:0:0:0:0") or \
        _bind_host.strip().startswith("0.")
    if _insecure and _public_bind:
        raise RuntimeError("ROUVR_INSECURE_DEV=1 refused with public bind "
                           f"{_bind_host!r}: bind 127.0.0.1 for local dev")
    if _insecure and not _token:
        print("WARNING: ROUVR_INSECURE_DEV=1 allows unauthenticated local dev "
              "(no token configured); never use with a public bind",
              file=_sys.stderr)

    # Pre-account onboarding paths authenticate against hyeve-auth
    # upstream, not the ledger token (the caller has no ledger user yet).
    _ONBOARD_OPEN = {"/v1/onboard/register", "/v1/onboard/login"}

    async def _cookie_identity(request):
        """Step 1: resolve the session cookie. None = continue, else respond."""
        from fastapi.responses import JSONResponse
        tok = request.cookies.get(_transport.cookie_name)
        if not tok:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        try:
            async with _session_factory() as _s:
                _user = await _strategy.read_token(tok, _authmod.make_manager(_s))
        except Exception:
            _user = None
        if _user is None or not _user.is_active:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        request.state.user = _user.email.split("@")[0]
        request.state.session_tenant = _user.tenant
        request.state.session_role = _user.role
        if not _authmod.csrf_allows(request, str(_user.id), _auth_secret):
            return JSONResponse({"detail": "csrf check failed"}, status_code=403)
        return None

    @app.middleware("http")
    async def _auth(request, call_next):
        # FL-A2: the session binds scope server-side. Every /v1 handler takes
        # the tenant from request.state.session_tenant (credential -> user
        # row), never from request args. supervisor=None sees all tenants.
        request.state.user = "supervisor"
        request.state.session_tenant = None
        request.state.session_role = None
        request.state.hyeve_claims = None
        path = request.url.path
        # FL-A3: Lite aliases share ledger auth exactly — canonicalize
        # /api/v1/* back to its ledger path before the guard below, so
        # open/guarded parity holds without a second allowlist.
        guard = path
        if guard == "/api/v1/health":
            guard = "/health"
        elif guard.startswith("/api/v1/"):
            guard = "/v1/" + guard[len("/api/v1/"):]
        if guard.startswith("/v1") and guard not in _ONBOARD_OPEN:
            from fastapi.responses import JSONResponse
            authz = request.headers.get("authorization", "")
            if authz.casefold().startswith("bearer "):
                # Hyeve JWT: verified locally against JWKS. Maps to the
                # onboarded ledger user; an un-onboarded subject gets a
                # sentinel identity with no tenant (fails closed).
                try:
                    claims = _ha.verify_hyeve_jwt(authz[7:].strip())
                except _ha.HyeveAuthError as e:
                    return JSONResponse({"detail": str(e)}, status_code=401)
                request.state.hyeve_claims = claims
                with eng.connect() as c:
                    link = c.execute(text(
                        "SELECT username FROM external_identities"
                        " WHERE provider='hyeve' AND subject=:s"),
                        {"s": str(claims["sub"])}).mappings().first()
                    tenant = None
                    if link:
                        tenant = c.execute(
                            text("SELECT tenant FROM users WHERE username=:u"),
                            {"u": link["username"]}).scalar()
                request.state.user = link["username"] if link else f"hyeve:{claims['sub']}"
                request.state.session_tenant = tenant  # None: unlinked fails closed downstream
                return await call_next(request)
            if _insecure and not _token:
                pass  # explicit local-dev bypass (warned at startup, local bind only)
            elif _token:
                hdr = request.headers.get("x-rouvr-token", "")
                if hdr == _token:
                    pass  # stack identity
                elif hdr:
                    h = hashlib.sha256(hdr.encode()).hexdigest()
                    with eng.connect() as c:
                        u = c.execute(text("SELECT username, disabled, tenant FROM users"
                                           " WHERE token_hash=:h"),
                                      {"h": h}).mappings().first()
                    if not u or u["disabled"]:
                        return JSONResponse({"detail": "unauthorized"}, status_code=401)
                    request.state.user = u["username"]
                    request.state.session_tenant = u["tenant"]
                else:
                    _resp = await _cookie_identity(request)
                    if _resp is not None:
                        return _resp
            else:
                _resp = await _cookie_identity(request)
                if _resp is not None:
                    return _resp
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

    def _caller_tenant(request, c) -> str | None:
        """Session tenant resolved by auth middleware (credential -> user row).
        Supervisor (stack) sees all tenants (None). Tenantless identities
        (never-onboarded hyeve subjects) fail closed."""
        ident = _identity(request)
        if ident == "supervisor":
            return None
        st = getattr(request.state, "session_tenant", "unset")
        if st != "unset":
            if not st:
                raise HTTPException(403, f"no tenant assigned to {ident}")
            return st
        return _tenant_of(c, ident)  # direct-call fallback (no middleware run)

    def _session_scope(request, c, body: dict) -> tuple[str, str]:
        """Authoritative scope: tenant ALWAYS comes from the session; a body
        tenant may restate it but a foreign one is refused (404) before any
        existence check (no project oracle). Project selects within the
        session tenant and must be registered. The stack identity has no
        session tenant and names explicit scope."""
        ident = _identity(request)
        if ident == "supervisor":
            return _scope(body)
        mine = _caller_tenant(request, c)
        assert mine is not None  # _caller_tenant raises for tenantless users
        claimed = body.get("tenant")
        if claimed is not None:
            if not isinstance(claimed, str) or not claimed.strip():
                raise HTTPException(422, "tenant must be a string")
            if claimed.strip() != mine:
                raise HTTPException(404, "tenant scope comes from the session")
        project = body.get("project")
        if not isinstance(project, str) or not project.strip():
            raise HTTPException(422, "project is required")
        return mine, project.strip()

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

    def _own_or_stack(c, tid, request):
        """404 for a caller outside the task's tenant (no oracle).

        Session-aware: the tenant comes from the credential (cookie
        session or token), never from args, so web sessions can read
        and act on their own tenant's tasks."""
        t = task_or_404(c, tid)
        ident = _identity(request)
        if ident != "supervisor" and t.get("tenant") != _caller_tenant(request, c):
            raise HTTPException(404, "task not found")
        return t

    def _stack_only(identity):
        if identity != "supervisor":
            raise HTTPException(403, "worker-only operation")

    def _transition_allowed(request):
        """Stack may transition anything; a session owner may approve/retry
        tasks inside its own tenant (checked by _own_or_stack below).
        User tokens carry no role and stay worker-only (S016)."""
        if _identity(request) == "supervisor":
            return
        if getattr(request.state, "session_role", None) != "owner":
            raise HTTPException(403, "worker-only operation")

    def row(c, sql, **kw):
        r = c.execute(text(sql), kw).mappings().first()
        return dict(r) if r else None

    def task_or_404(c, tid):
        t = row(c, "SELECT * FROM tasks WHERE id=:id", id=tid)
        if not t:
            raise HTTPException(404, "task not found")
        return t

    def _emit(c, tenant, project, etype, task_id, payload):
        """Append one outbox row in the caller's transaction. Transactional =
        no event without its mutation and no mutation without its event; the
        server never writes rows outside a mutation, so the stream can never
        carry fake progress (FL-A4 kill-worker guarantee)."""
        s, _ = _canon(payload)
        return c.execute(text("INSERT INTO outbox(tenant,project,type,task_id,body,created_at)"
                              " VALUES(:t,:p,:y,:i,:b,:n)"),
                         {"t": tenant, "p": project, "y": etype, "i": task_id,
                          "b": s, "n": _now()}).lastrowid

    def _events_after(c, tenant, last_id, limit=1000):
        """Outbox rows after a cursor, oldest first. tenant=None (supervisor)
        sees all; otherwise exactly the caller's own tenant, resolved
        server-side from the session — never from request args."""
        q = "SELECT * FROM outbox WHERE id>:i"
        kw: dict = {"i": last_id, "l": limit}
        if tenant is not None:
            q += " AND tenant=:t"
            kw["t"] = tenant
        return [dict(r) for r in c.execute(text(q + " ORDER BY id LIMIT :l"), kw).mappings()]

    def _contract_body(criteria, task_title, category, plan_slug, slice_id=None):
        """write-task-contract shape: acceptance criteria plus the intake
        category and plan link. Extra keys are additive: legacy readers
        index acceptance_criteria only."""
        body = {"acceptance_criteria": list(criteria)}
        if task_title is not None:
            body["task"] = task_title
        if slice_id is not None:
            body["slice_id"] = slice_id
        if category:
            body["category"] = category
        if plan_slug:
            body["plan_slug"] = plan_slug
        return body

    def _confirm_entry(c, ident, entry) -> None:
        """Existing confirm path semantics: only the creator or an admin
        confirms; sets verified + emits knowledge.confirmed."""
        if ident != "supervisor" and ident != entry.get("author"):
            raise HTTPException(403, "only the creator or an admin confirms")
        c.execute(text("UPDATE knowledge_entries SET verified=1 WHERE id=:i"),
                  {"i": entry["id"]})
        _emit(c, entry["tenant"], entry["project"], "knowledge.confirmed", None,
              {"id": entry["id"],
               "citation": f"knowledge:{entry['id']}@v{entry['version']}"})

    def _parse_plan_body(entry):
        try:
            d = json.loads(entry["body"])
        except ValueError:
            return None
        if not isinstance(d, dict) or not isinstance(d.get("slice"), dict) \
                or not isinstance(d.get("tasks"), list):
            return None
        return d

    def _task_card(c, t):
        """Chat card payload: id/title/state plus the same pipeline
        flags the list endpoint carries, computed from real rows.
        Result sets are small (one slice), so per-task queries are fine."""
        contracts = [dict(r) for r in c.execute(text(
            "SELECT * FROM contracts WHERE task_id=:i ORDER BY id"),
            {"i": t["id"]}).mappings()]
        evidence = [dict(r) for r in c.execute(text(
            "SELECT * FROM evidence WHERE task_id=:i ORDER BY id"),
            {"i": t["id"]}).mappings()]
        receipts = [dict(r) for r in c.execute(text(
            "SELECT * FROM receipts WHERE task_id=:i ORDER BY id"),
            {"i": t["id"]}).mappings()]
        verdicts = [r["verdict"] for r in c.execute(text(
            "SELECT verdict FROM results WHERE task_id=:i ORDER BY id"),
            {"i": t["id"]}).mappings()]
        return {"id": t["id"], "title": t["title"], "state": t.get("state"),
                "tenant": t.get("tenant"), "project": t.get("project"),
                "pipeline": _pipeline_flags(t.get("state"), contracts,
                                           evidence, receipts, verdicts),
                "sealing": _sealing(evidence),
                "needs_spec": _needs_exec_spec(t.get("tenant"), t.get("project"))}

    def _materialize_plan(c, ident, msg_id, model_name, spec, tasks, category,
                          plan_slug, tenant, project, pre_sealed=None):
        """One slice plus one task per step, each with category +
        contract, linked to the plan slug. pre_sealed = {"slice": piece,
        "kids": [piece]} with piece = ((ok, files|error), criteria)
        pre-authored before the txn, or None to skip sealing."""
        n = _now()
        sid = c.execute(text("INSERT INTO tasks(title,intent,state,created_at,updated_at,"
                             "created_by,tenant,project) VALUES(:t,:i,'open',:n,:n,:by,:tn,:p)"),
                        {"t": spec["title"], "i": spec["intent"], "n": n,
                         "by": ident, "tn": tenant, "p": project}).lastrowid
        _emit(c, tenant, project, "task.created", sid,
              {"task_id": sid, "title": spec["title"], "state": "open"})
        s, h = _canon(_contract_body(spec["acceptance_criteria"], None,
                                      category, plan_slug))
        c.execute(text("INSERT INTO contracts(task_id,body,sha256,created_at)"
                       " VALUES(:i,:b,:h,:n)"), {"i": sid, "b": s, "h": h, "n": n})
        if pre_sealed is not None and pre_sealed.get("slice") is not None:
            (sealed, seal_criteria) = pre_sealed["slice"]
            _seal_one_task(c, sid, sealed, seal_criteria)
        kids = []
        kid_pieces = (pre_sealed or {}).get("kids") or []
        for idx, t in enumerate(tasks):
            kid = c.execute(text("INSERT INTO tasks(title,intent,state,created_at,updated_at,"
                                 "created_by,tenant,project) VALUES(:t,:i,'open',:n,:n,:by,:tn,:p)"),
                            {"t": t["title"], "i": t["intent"], "n": n,
                             "by": ident, "tn": tenant, "p": project}).lastrowid
            _emit(c, tenant, project, "task.created", kid,
                  {"task_id": kid, "title": t["title"], "state": "open"})
            s, h = _canon(_contract_body(spec["acceptance_criteria"], t["title"],
                                          category, plan_slug, slice_id=sid))
            c.execute(text("INSERT INTO contracts(task_id,body,sha256,created_at)"
                           " VALUES(:i,:b,:h,:n)"), {"i": kid, "b": s, "h": h, "n": n})
            if idx < len(kid_pieces) and kid_pieces[idx] is not None:
                (sealed, seal_criteria) = kid_pieces[idx]
                _seal_one_task(c, kid, sealed, seal_criteria)
            kids.append(kid)
        for kid in kids:
            s, h = _canon({"slice_id": sid, "source": msg_id, "plan_slug": plan_slug})
            c.execute(text("INSERT INTO evidence(task_id,kind,body,sha256,created_at)"
                           " VALUES(:i,'slice_member',:b,:h,:n)"),
                      {"i": kid, "b": s, "h": h, "n": n})
        for tid, role in [(sid, "slice")] + [(k, "task") for k in kids]:
            s, _ = _canon({"source": msg_id, "role": role, "slice_id": sid,
                           "model": model_name, "plan_slug": plan_slug,
                           "category": category})
            c.execute(text("INSERT INTO receipts(task_id,idempotency_key,action,body,created_at)"
                           " VALUES(:i,:k,'chat:create',:b,:n)"),
                      {"i": tid, "k": msg_id, "b": s, "n": n})
        s, h = _canon({"source": msg_id, "children": kids, "plan_slug": plan_slug})
        c.execute(text("INSERT INTO evidence(task_id,kind,body,sha256,created_at)"
                       " VALUES(:i,'slice',:b,:h,:n)"),
                  {"i": sid, "b": s, "h": h, "n": n})
        slice_row = row(c, "SELECT * FROM tasks WHERE id=:i", i=sid)
        task_rows = [row(c, "SELECT * FROM tasks WHERE id=:i", i=k) for k in kids]
        return slice_row, task_rows

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "rouvr-ledger"}

    @app.get("/v1/version")
    def version():
        return {"version": VERSION, "build": BUILD}

    @app.get("/demo/onboard")
    def demo_onboard():
        from fastapi.responses import HTMLResponse
        return HTMLResponse(_ONBOARD_HTML)

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
        """Register a project under an existing tenant (S022). Idempotent.

        Stack may create anywhere; a session owner may create inside its
        own tenant — the same owner-within-tenant rule task transitions
        use. Anything else stays worker-only. Optional metadata (title,
        code, description, domain) is stored when provided and updated on
        re-creation; unprovided fields keep their stored values."""
        ident = _identity(request)
        tenant, project = _scope(body)
        meta = {}
        for field in ("title", "code", "description", "domain"):
            value = body.get(field)
            if value is None:
                continue
            if not isinstance(value, str):
                raise HTTPException(422, f"{field} must be a string")
            meta[field] = value.strip() or None
        with eng.begin() as c:
            if ident != "supervisor":
                if getattr(request.state, "session_role", None) != "owner":
                    raise HTTPException(403, "worker-only operation")
                if tenant != _caller_tenant(request, c):
                    raise HTTPException(403, f"project {project!r} is outside your tenant")
            if not row(c, "SELECT * FROM tenants WHERE tenant=:t", t=tenant):
                raise HTTPException(404, f"tenant not registered: create {tenant!r} first")
            cols = [r[1] for r in c.execute(text("PRAGMA table_info(projects)"))]
            meta = {k: v for k, v in meta.items() if k in cols}
            c.execute(text("INSERT INTO projects(tenant,project,created_at) VALUES(:t,:p,:n)"
                           " ON CONFLICT(tenant,project) DO NOTHING"),
                      {"t": tenant, "p": project, "n": _now()})
            for field, value in meta.items():
                c.execute(text(f"UPDATE projects SET {field}=:v WHERE tenant=:t AND project=:p"),
                          {"v": value, "t": tenant, "p": project})
            return row(c, "SELECT * FROM projects WHERE tenant=:t AND project=:p",
                       t=tenant, p=project)

    @app.get("/v1/projects")
    def list_projects(request: Request, tenant: str | None = None,
                      include_archived: bool = False):
        """Projects visible to the caller: its own tenant, or all for stack.
        Archived projects are hidden unless include_archived is set."""
        ident = _identity(request)
        with eng.begin() as c:
            mine = _caller_tenant(request, c)
            want = mine if mine is not None else tenant
            cols = [r[1] for r in c.execute(text("PRAGMA table_info(projects)"))]
            select = (["tenant", "project"] +
                      [f for f in ("title", "code", "description", "domain", "archived") if f in cols])
            q = f"SELECT {', '.join(select)} FROM projects"
            kw: dict = {}
            conds = []
            if want:
                conds.append("tenant=:t")
                kw["t"] = want
            if not include_archived and "archived" in cols:
                conds.append("(archived IS NULL OR archived=0)")
            if conds:
                q += " WHERE " + " AND ".join(conds)
            rows = [dict(r) for r in c.execute(text(q + " ORDER BY tenant, project"), kw).mappings()]
            return {"projects": rows}

    @app.patch("/v1/projects/{project}")
    def edit_project(project: str, body: dict, request: Request):
        """Edit a project: rename (name), metadata, archived flag.

        Stack may act anywhere; a session owner inside its own tenant.
        Rename rewrites the tenant/project scope on projects, tasks,
        knowledge, chat turns, classifications and autonomy rows — receipts
        and outbox payloads are historical records and stay as written.
        409 when the target name is already taken."""
        ident = _identity(request)
        with eng.begin() as c:
            if ident == "supervisor":
                tenant = (body.get("tenant") or "").strip() if isinstance(body.get("tenant"), str) else ""
                if not tenant:
                    raise HTTPException(422, "tenant is required")
            else:
                if getattr(request.state, "session_role", None) != "owner":
                    raise HTTPException(403, "worker-only operation")
                tenant = _caller_tenant(request, c)
            cur = row(c, "SELECT * FROM projects WHERE tenant=:t AND project=:p",
                      t=tenant, p=project)
            if not cur:
                raise HTTPException(404, f"project {project!r} not found in tenant {tenant!r}")
            cols = [r[1] for r in c.execute(text("PRAGMA table_info(projects)"))]
            updates, kw = [], {"t": tenant, "p": project}
            for field in ("title", "code", "description", "domain"):
                if field in body and field in cols:
                    value = body[field]
                    if value is not None and not isinstance(value, str):
                        raise HTTPException(422, f"{field} must be a string")
                    updates.append(f"{field}=:{field}")
                    kw[field] = (value.strip() or None) if isinstance(value, str) else None
            if "archived" in body and "archived" in cols:
                updates.append("archived=:a")
                kw["a"] = 1 if body["archived"] else 0
            new_name = body.get("name")
            if new_name is not None:
                new_name = str(new_name).strip()
                if not new_name:
                    raise HTTPException(422, "name must not be blank")
                if new_name != project:
                    taken = row(c, "SELECT * FROM projects WHERE tenant=:t AND project=:p",
                                t=tenant, p=new_name)
                    if taken:
                        raise HTTPException(409, f"project {new_name!r} already exists")
            if not updates and new_name in (None, project):
                return row(c, "SELECT * FROM projects WHERE tenant=:t AND project=:p",
                           t=tenant, p=project)
            rename_to = new_name if isinstance(new_name, str) else project
            if updates:
                c.execute(text(f"UPDATE projects SET {', '.join(updates)}"
                               " WHERE tenant=:t AND project=:p"), kw)
            if rename_to != project:
                c.execute(text("UPDATE projects SET project=:n WHERE tenant=:t AND project=:p"),
                          {"n": rename_to, "t": tenant, "p": project})
                for table in ("tasks", "knowledge_entries", "chat_turns",
                              "intake_classifications", "chat_autonomy"):
                    tcols = [r[1] for r in c.execute(text(f"PRAGMA table_info({table})"))]
                    if tcols and "project" in tcols and "tenant" in tcols:
                        c.execute(text(f"UPDATE {table} SET project=:n"
                                       " WHERE tenant=:t AND project=:p"),
                                  {"n": rename_to, "t": tenant, "p": project})
                _emit(c, tenant, rename_to, "project.renamed", None,
                      {"from": project, "to": rename_to})
                project = rename_to
            return row(c, "SELECT * FROM projects WHERE tenant=:t AND project=:p",
                       t=tenant, p=project)

    @app.post("/v1/users/{username}/disable")
    def disable_user(username: str, request: Request):
        _stack_only(_identity(request))
        with eng.begin() as c:
            c.execute(text("UPDATE users SET disabled=1 WHERE username=:u"), {"u": username})
            return {"disabled": username}

    # -- Onboarding (hackathon cut of Slice 1): hyeve-auth owns identity
    # (person-first: person_id required before user creation). The ledger
    # owns Rouvr scope: complete() creates user/tenant/project + profiles
    # as Knowledge entries, idempotently per Hyeve subject.
    def _slug(raw: str, fallback: str) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", (raw or "").lower()).strip("-")
        return s or re.sub(r"[^a-z0-9]+", "-", fallback.lower()).strip("-") or "user"

    def _upstream(fn, **kw):
        try:
            return fn(**kw)
        except _ha.HyeveAuthError as e:
            code = str(e)
            status = 503 if ("NOT_CONFIGURED" in code or "UNREACHABLE" in code) else 502
            raise HTTPException(status, code) from e

    @app.post("/v1/onboard/register")
    def onboard_register(body: dict):
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        email = str(body.get("email") or "").strip()
        if not username or not password or not email:
            raise HTTPException(422, "username, email and password are required")
        # Person provisioning mode (HYEVE_PERSON_MODE): "inline" (default —
        # auth creates/links the person from details, VPS1 build),
        # "person_service" (create person first, hosted build), or an
        # explicit person_id in the body wins over both.
        person_id = str(body.get("person_id") or "").strip()
        person = None
        given = str(body.get("given_name") or "").strip()
        family = str(body.get("family_name") or "").strip()
        if not person_id:
            if not given or not family:
                raise HTTPException(422, "given_name and family_name are required"
                                         " (person-first onboarding)")
            display = str(body.get("display_name") or f"{given} {family}").strip()
            if os.environ.get("HYEVE_PERSON_MODE", "inline") == "person_service":
                person_id = _upstream(_ha.create_person, given_name=given,
                                      family_name=family, display_name=display)
            else:
                person = {"person_type": str(body.get("person_type") or "Customer"),
                          "given_name": given, "family_name": family,
                          "display_name": display}
        # Admin-driven identity: the configured tenant admin owns
        # RBAC bootstrap and the trusted create call; new users get the
        # rouvr-user role immediately.
        admin = _upstream(_ha.admin_token)
        _upstream(_ha.ensure_rouvr_rbac)
        created = _upstream(_ha.create_user, username=username, password=password,
                            email=email, person_id=person_id, person=person, token=admin)
        uid = str(created.get("user_id") or "") if isinstance(created, dict) else ""
        if not person_id and isinstance(created, dict):
            person_id = str(created.get("person_id") or "")
        if uid:
            _upstream(_ha.assign_role, user_id=uid)
        tokens = _upstream(_ha.login, username=username, password=password)
        return {"username": username, "user_id": uid, "person_id": person_id, **tokens}

    @app.post("/v1/onboard/login")
    def onboard_login(body: dict):
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not username or not password:
            raise HTTPException(422, "username and password are required")
        return _upstream(_ha.login, username=username, password=password)

    @app.post("/v1/onboard/complete")
    def onboard_complete(body: dict, request: Request):
        claims = getattr(request.state, "hyeve_claims", None)
        if not claims:
            raise HTTPException(401, "a valid Hyeve token is required")
        # Identity: sub is the stable key; the username always comes
        # from /me, never from JWT claims (human tokens omit it).
        sub = str(claims["sub"])
        bearer = request.headers.get("authorization", "")[7:].strip()
        hyeve_user = str(_upstream(_ha.me, token=bearer).get("username") or "").strip()
        if not hyeve_user:
            hyeve_user = str(claims.get("username")
                             or claims.get("preferred_username") or "").strip()
        if not hyeve_user:
            raise HTTPException(422, "could not resolve a username for this token")
        with eng.begin() as c:
            link = row(c, "SELECT username FROM external_identities"
                          " WHERE provider='hyeve' AND subject=:s", s=sub)
            if link:  # idempotent: same subject returns its existing scope
                u = row(c, "SELECT username, tenant FROM users WHERE username=:u",
                        u=link["username"])
                proj = row(c, "SELECT project FROM knowledge_entries"
                              " WHERE tenant=:t AND kind='project_profile'"
                              " ORDER BY id LIMIT 1", t=u["tenant"])
                return {"username": u["username"], "tenant": u["tenant"],
                        "project": proj["project"] if proj else None,
                        "rouvr_token": None}
            project_name = str(body.get("project_name") or "").strip()
            purpose = str(body.get("project_purpose") or "").strip()
            ptype = str(body.get("project_type") or "").strip()
            if not project_name or not purpose or not ptype:
                raise HTTPException(422, "project_name, project_purpose and"
                                         " project_type are required")
            if row(c, "SELECT username FROM users WHERE username=:u", u=hyeve_user):
                raise HTTPException(409, f"ledger user {hyeve_user!r} already exists"
                                         " under a different identity")
            org = str(body.get("organisation") or "").strip()
            tenant, base = _slug(org, hyeve_user), None
            base, n = tenant, 2
            while row(c, "SELECT tenant FROM tenants WHERE tenant=:t", t=tenant):
                tenant, n = f"{base}-{n}", n + 1
            project = _slug(project_name, "project")
            now = _now()
            c.execute(text("INSERT INTO tenants(tenant,created_at) VALUES(:t,:n)"),
                      {"t": tenant, "n": now})
            c.execute(text("INSERT INTO projects(tenant,project,created_at)"
                           " VALUES(:t,:p,:n)"), {"t": tenant, "p": project, "n": now})
            rouvr_token = _secrets.token_urlsafe(32)
            c.execute(text("INSERT INTO users(username,display_name,token_hash,tenant,created_at)"
                           " VALUES(:u,:d,:h,:t,:n)"),
                      {"u": hyeve_user, "d": str(body.get("display_name") or ""),
                       "h": hashlib.sha256(rouvr_token.encode()).hexdigest(),
                       "t": tenant, "n": now})
            c.execute(text("INSERT INTO external_identities(provider,subject,username,created_at)"
                           " VALUES('hyeve',:s,:u,:n)"), {"s": sub, "u": hyeve_user, "n": now})
            user_body = json.dumps({
                "display_name": str(body.get("display_name") or ""),
                "username": hyeve_user, "organisation": org,
                "role_title": str(body.get("role_title") or ""),
                "use_case": str(body.get("use_case") or "")}, indent=2)
            proj_body = json.dumps({
                "name": project_name, "purpose": purpose, "type": ptype,
                "north_star": str(body.get("north_star") or ""),
                "standing_rules": str(body.get("standing_rules") or "")}, indent=2)
            up = _knowledge_insert(c, tenant, project, "user_profile",
                                   f"User profile: {hyeve_user}", user_body, hyeve_user)
            pp = _knowledge_insert(c, tenant, project, "project_profile",
                                   f"Project profile: {project_name}", proj_body, hyeve_user)
            # Owner-typed at onboarding: profiles are confirmed by definition.
            c.execute(text("UPDATE knowledge_entries SET verified=1 WHERE id IN (:a,:b)"),
                      {"a": up["id"], "b": pp["id"]})
            rules = str(body.get("standing_rules") or "").strip()
            if rules:
                rk = _knowledge_insert(c, tenant, project, "rule",
                                       "Standing rules (onboarding)", rules, hyeve_user)
                c.execute(text("UPDATE knowledge_entries SET verified=1 WHERE id=:i"),
                          {"i": rk["id"]})
            return {"username": hyeve_user, "tenant": tenant, "project": project,
                    "rouvr_token": rouvr_token,
                    "user_profile": f"knowledge:{up['id']}@v1",
                    "project_profile": f"knowledge:{pp['id']}@v1"}

    # -- Knowledge Lite: append-only, versioned, visibility-scoped memory.
    # Search returns latest versions only; the tenant+visibility filter is
    # inside every query so titles/snippets cannot leak. Visibility:
    # restricted (creator or named members), company (same tenant),
    # public (any signed-in user, never anonymous).
    KNOWLEDGE_KINDS = {"user_profile", "project_profile", "note", "decision", "rule", "snapshot", "skill"}
    KNOWLEDGE_VIS = {"restricted", "company", "public"}
    _LATEST = ("k.id NOT IN (SELECT supersedes_id FROM knowledge_entries"
               " WHERE supersedes_id IS NOT NULL)")

    def _krow(r):
        d = dict(r)
        d["citation"] = f"knowledge:{d['id']}@v{d['version']}"
        for col in ("allowed_users", "tags"):
            try:
                d[col] = json.loads(d.get(col) or "[]")
            except ValueError:
                d[col] = []
        return d

    def _shortlist(r):
        return {"slug": r.get("slug") or "", "title": r["title"], "kind": r["kind"],
                "summary": r.get("summary") or "", "verified": r["verified"],
                "review_state": r.get("review_state") or "pending",
                "pinned": 1 if r.get("pinned") else 0,
                "citation": f"knowledge:{r['id']}@v{r['version']}"}

    _LATEST_SELF = ("id NOT IN (SELECT supersedes_id FROM knowledge_entries"
                    " WHERE supersedes_id IS NOT NULL)")


    def _latest_slugs(c, tenant, project) -> set:
        return {r["slug"] for r in c.execute(text(
            "SELECT slug FROM knowledge_entries WHERE tenant=:t AND project=:p"
            f" AND {_LATEST_SELF} AND slug<>''"), {"t": tenant, "p": project}).mappings()}

    def _assign_slug(c, tenant, project, title, want, exclude: set | None = None) -> str:
        """Unique slug in scope among latest versions. Explicit collisions
        are a 409 (the caller named it); generated ones suffix -2, -3, ..."""
        taken = _latest_slugs(c, tenant, project) - (exclude or set())
        if want:
            slug = re.sub(r"[^a-z0-9-]+", "-", want.lower()).strip("-").rstrip("-")
            if not slug:
                raise HTTPException(422, "slug must contain [a-z0-9-]")
            if len(slug) > 60:
                raise HTTPException(422, "slug must be <= 60 chars")
            if slug in taken:
                raise HTTPException(409, f"slug {slug!r} already in use in {tenant}/{project}")
            return slug
        base = _slugify(title)
        slug, i = base, 2
        while slug in taken:
            slug = f"{base}-{i}"
            i += 1
        return slug

    def _allowed_list(raw) -> list:
        if raw is None:
            return []
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        if isinstance(raw, str):
            try:
                v = json.loads(raw)
                return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
            except ValueError:
                return []
        return []

    def _can_see(k, identity, caller_tenant) -> bool:
        if identity == "supervisor":
            return True  # admin sees all
        vis = k.get("visibility") or "company"
        if vis == "public":
            return True  # any signed-in user; anonymous never reaches here (401)
        if vis == "company":
            return caller_tenant is not None and k["tenant"] == caller_tenant
        if vis == "restricted":
            if identity == k.get("author"):
                return True
            return identity in _allowed_list(k.get("allowed_users"))
        return False

    def _knowledge_or_404(c, kid, identity, request=None):
        k = row(c, "SELECT * FROM knowledge_entries WHERE id=:i", i=kid)
        if not k:
            raise HTTPException(404, "knowledge entry not found")
        if identity == "supervisor":
            return k
        try:
            caller_tenant = _caller_tenant(request, c) if request is not None else _tenant_of(c, identity)
        except HTTPException:
            caller_tenant = None  # sentinel hyeve:<sub>: public only
            if (k.get("visibility") or "company") != "public":
                raise HTTPException(404, "knowledge entry not found")
            return k
        if k["tenant"] != caller_tenant and (k.get("visibility") or "company") != "public":
            raise HTTPException(404, "knowledge entry not found")  # no existence oracle
        if not _can_see(k, identity, caller_tenant):
            raise HTTPException(404, "knowledge entry not found")
        return k

    def _knowledge_insert(c, tenant, project, kind, title, body, author, version=1,
                          supersedes=None, visibility="company", allowed=None,
                          slug=None, tags=None, summary=None, slug_chain=()):
        vis = visibility if visibility in KNOWLEDGE_VIS else "company"
        allow = json.dumps([str(x) for x in (allowed or [])])
        slug = _assign_slug(c, tenant, project, title, slug, set(slug_chain))
        tags = _tags_norm(tags) or [kind]
        summary = _summarize_text(summary) if summary else _summarize_text(body)
        rid = c.execute(text(
            "INSERT INTO knowledge_entries(tenant,project,kind,title,body,version,"
            "supersedes_id,verified,author,visibility,allowed_users,slug,tags,summary,created_at)"
            " VALUES(:tn,:p,:k,:t,:b,:v,:s,0,:a,:vis,:al,:sl,:tg,:su,:n)"),
            {"tn": tenant, "p": project, "k": kind, "t": title, "b": body,
             "v": version, "s": supersedes, "a": author, "vis": vis,
             "al": allow, "sl": slug, "tg": json.dumps(tags), "su": summary,
             "n": _now()}).lastrowid
        c.execute(text("INSERT INTO knowledge_fts(rowid,title,body,slug,summary,tags)"
                       " VALUES(:i,:t,:b,:s,:u,:g)"),
                  {"i": rid, "t": title, "b": body, "s": slug,
                   "u": summary, "g": " ".join(tags)})
        c.execute(text("INSERT INTO knowledge_trigram(rowid,title,slug,tags,summary)"
                       " VALUES(:i,:t,:s,:g,:u)"),
                  {"i": rid, "t": title, "s": slug,
                   "g": " ".join(tags), "u": summary})
        # Leak-safe payload: id/citation/kind only — titles and bodies stay
        # behind the visibility-checked GET (restricted entries share the
        # tenant stream with members who may not see them).
        _emit(c, tenant, project, "knowledge.versioned", None,
              {"id": rid, "citation": f"knowledge:{rid}@v{version}", "kind": kind,
               "version": version, "supersedes_id": supersedes})
        return row(c, "SELECT * FROM knowledge_entries WHERE id=:i", i=rid)

    def _req_visibility(body: dict) -> str:
        if body.get("visibility") is None:
            return "company"
        vis = str(body.get("visibility"))
        if vis not in KNOWLEDGE_VIS:
            raise HTTPException(422, f"visibility must be one of {sorted(KNOWLEDGE_VIS)}")
        return vis

    @app.post("/v1/knowledge")
    def create_knowledge(body: dict, request: Request):
        ident = _identity(request)
        kind = str(body.get("kind") or "")
        title = str(body.get("title") or "").strip()
        kbody = str(body.get("body") or "")
        vis = _req_visibility(body)
        if kind not in KNOWLEDGE_KINDS:
            raise HTTPException(422, f"kind must be one of {sorted(KNOWLEDGE_KINDS)}")
        if not title or not kbody.strip():
            raise HTTPException(422, "title and body are required")
        allowed = _allowed_list(body.get("allowed_users"))
        if vis != "restricted" and allowed:
            raise HTTPException(422, "allowed_users only with visibility=restricted")
        with eng.begin() as c:
            tenant, project = _session_scope(request, c, body)
            _registered(c, tenant, project)
            return _krow(_knowledge_insert(c, tenant, project, kind, title, kbody, ident,
                                           visibility=vis, allowed=allowed,
                                           slug=body.get("slug"), tags=body.get("tags"),
                                           summary=body.get("summary")))

    @app.get("/v1/knowledge/search")
    def search_knowledge(request: Request, q: str, project: str | None = None,
                         n: int = 15, full: bool = False,
                         include_archived: bool = False, offset: int = 0):
        """Shortlist search: top N (default 15) as slug/title/kind/summary/
        verified/citation only; full=true returns whole rows. Retrieval:
        porter-stemmed prefix FTS plus an FTS5 trigram index (partials,
        substrings, typo candidates); the pooled top ~50 are re-ranked
        with RapidFuzz WRatio over title/slug/tags/summary, and every run
        merges by reciprocal rank fusion. A sqlite-vec + fastembed
        semantic run plugs in at _semantic_candidates (D18, not built:
        honest stub, contributes nothing). Verified entries get one extra
        rank-1 RRF vote, so human-confirmed knowledge surfaces above
        unverified near-ties. Tenant/visibility filters
        apply to every candidate."""
        ident = _identity(request)
        tokens = re.findall(r"\w+", q)
        if not tokens:
            raise HTTPException(422, "q must contain a searchable word")
        n = max(1, min(n, 100))
        match = " AND ".join(f'"{t}"*' for t in tokens)
        tri = " OR ".join(f'"{t}"' for t in tokens)

        def _split_trigrams(word: str) -> set[str]:
            w = word.lower()
            return {w[i:i + 3] for i in range(len(w) - 2)} if len(w) >= 3 else set()

        # Candidate expansion for typos: OR the query's own trigrams so one
        # wrong character only loses the trigrams spanning it. This only
        # widens the candidate pool; scoring is RapidFuzz below.
        tri_terms = list(dict.fromkeys(
            g for t in tokens if len(t) >= 4
            for g in sorted(_split_trigrams(t))))
        tri_typo = " OR ".join(f'"{g}"' for g in tri_terms)

        def _fuzz_score(r: dict) -> float:
            fields = [r.get("title") or "", r.get("slug") or "",
                      " ".join(json.loads(r.get("tags") or "[]")),
                      r.get("summary") or ""]
            return max([fuzz.WRatio(q, f) for f in fields] + [0.0])

        with eng.begin() as c:
            if ident == "supervisor":
                mine = None
            else:
                try:
                    mine = _caller_tenant(request, c)
                except HTTPException:
                    # A valid upstream identity is not a Rouvr principal until
                    # onboarding binds it to a local user and tenant.
                    raise HTTPException(403, "complete Rouvr onboarding first")
            proj = " AND k.project=:p" if project else ""
            kw: dict = {"m": match}
            if project:
                kw["p"] = project
            exact = [dict(r) for r in c.execute(text(
                f"SELECT k.*, f.rank AS _rank FROM knowledge_fts f"
                f" JOIN knowledge_entries k ON k.id=f.rowid"
                f" WHERE knowledge_fts MATCH :m AND {_LATEST}{proj}"
                f" ORDER BY f.rank LIMIT 50"), kw).mappings()]
            kw2: dict = {"m": tri}
            if project:
                kw2["p"] = project
            fuzzy = [dict(r) for r in c.execute(text(
                f"SELECT k.*, f.rank AS _rank FROM knowledge_trigram f"
                f" JOIN knowledge_entries k ON k.id=f.rowid"
                f" WHERE knowledge_trigram MATCH :m AND {_LATEST}{proj}"
                f" ORDER BY f.rank LIMIT 50"), kw2).mappings()]
            fused: dict[int, float] = {}
            by_id: dict[int, dict] = {}
            for rank, r in enumerate(exact):
                fused[r["id"]] = fused.get(r["id"], 0.0) + 1.0 / (60 + rank)
                by_id[r["id"]] = r
            for rank, r in enumerate(fuzzy):
                fused[r["id"]] = fused.get(r["id"], 0.0) + 1.0 / (60 + rank)
                by_id[r["id"]] = r
            pool: dict[int, dict] = dict(by_id)
            if tri_typo:
                kw3: dict = {"m": tri_typo}
                if project:
                    kw3["p"] = project
                for r in c.execute(text(
                    f"SELECT k.* FROM knowledge_trigram f"
                    f" JOIN knowledge_entries k ON k.id=f.rowid"
                    f" WHERE knowledge_trigram MATCH :m AND {_LATEST}{proj}"
                    f" LIMIT 50"), kw3).mappings():
                    d = dict(r)
                    pool.setdefault(d["id"], d)
            # RapidFuzz re-rank over the pooled candidates (top ~50), then
            # fuse as one more RRF run.
            ranked = sorted(pool.values(), key=_fuzz_score, reverse=True)[:50]
            for rank, r in enumerate(ranked):
                fused[r["id"]] = fused.get(r["id"], 0.0) + 1.0 / (60 + rank)
                by_id[r["id"]] = r
            for rank, (sid, _score) in enumerate(_semantic_candidates(q)):
                if sid in by_id:
                    fused[sid] += 1.0 / (60 + rank)
            out = []
            boosted = {i: s + (1.0 / 61 if (by_id[i].get("verified") or by_id[i].get("pinned")) else 0.0)
                       for i, s in fused.items()}
            if offset < 0:
                raise HTTPException(422, "offset must be >= 0")
            for eid in sorted(boosted, key=lambda i: boosted[i], reverse=True):
                r = by_id[eid]
                if r.get("archived") and not include_archived:
                    continue  # archived leaves search/counts, stays readable by id
                if len(out) >= n + offset:
                    break
                if ident == "supervisor":
                    pass
                elif (r.get("visibility") or "company") == "public":
                    pass
                elif mine is not None and r["tenant"] == mine and (r.get("visibility") or "company") == "company":
                    pass
                elif (r.get("visibility") or "company") == "restricted" and (
                        r.get("author") == ident or ident in _allowed_list(r.get("allowed_users"))):
                    pass
                else:
                    continue  # never leak titles/snippets across the boundary
                out.append(_krow(r) if full else _shortlist(r))
            return {"items": out[offset:] if offset else out}

    @app.get("/v1/knowledge/{key}")
    def get_knowledge(key: str, request: Request, project: str | None = None):
        ident = _identity(request)
        with eng.begin() as c:
            try:
                kid = int(key)
            except ValueError:
                pass
            else:
                # Latest-only: a superseded id resolves forward to the
                # current version instead of surfacing the stale row.
                # Full history stays available via /history.
                k = _knowledge_or_404(c, kid, ident, request)
                seen = {k["id"]}
                while True:
                    nxt = row(c, "SELECT id FROM knowledge_entries"
                                 " WHERE supersedes_id=:i", i=k["id"])
                    if not nxt or nxt["id"] in seen:
                        break
                    seen.add(nxt["id"])
                    k = _knowledge_or_404(c, nxt["id"], ident, request)
                return _krow(k)
            kw: dict = {"s": key}
            psql = ""
            if project:
                psql = " AND project=:p"
                kw["p"] = project
            rows = [dict(r) for r in c.execute(text(
                "SELECT * FROM knowledge_entries WHERE slug=:s"
                f" AND {_LATEST_SELF}{psql}"), kw).mappings()]
            if ident == "supervisor":
                visible = rows
            else:
                try:
                    mine = _caller_tenant(request, c)
                except HTTPException:
                    mine = None
                visible = []
                for r in rows:
                    if (r.get("visibility") or "company") == "public":
                        visible.append(r)
                    elif mine is not None and r["tenant"] == mine and _can_see(r, ident, mine):
                        visible.append(r)
            if not visible:
                raise HTTPException(404, "knowledge entry not found")  # no existence oracle
            if ident == "supervisor":
                own = visible
            else:
                own = [r for r in visible if r["tenant"] == mine]
            if len(own) == 1:
                pick = own[0]
            elif not own and len(visible) == 1:
                pick = visible[0]  # someone else's public entry
            elif not visible:
                raise HTTPException(404, "knowledge entry not found")
            else:
                raise HTTPException(409, f"slug {key!r} is ambiguous across tenants")
            return _krow(_knowledge_or_404(c, pick["id"], ident, request))

    @app.get("/v1/knowledge/{key}/usage")
    def knowledge_usage(key: str, request: Request, project: str | None = None):
        """Where one entry is applied: tasks citing any of its versions.

        Counts distinct in-scope tasks whose knowledge_citations evidence names
        knowledge:<id>@v<n>; last_used_at is the newest citing evidence.
        Powers the Knowledge page usage line (brand pass 6 consumes it with
        graceful fallback): unknown keys 404, unused entries read zero."""
        ident = _identity(request)
        with eng.begin() as c:
            try:
                base = _knowledge_or_404(c, int(key), ident, request)
            except ValueError:
                base = None
                kw: dict = {"s": key}
                psql = ""
                if project:
                    psql = " AND project=:p"
                    kw["p"] = project
                for r in c.execute(text(
                        "SELECT * FROM knowledge_entries WHERE slug=:s"
                        f" AND {_LATEST_SELF}{psql}"), kw).mappings():
                    d = dict(r)
                    if ident == "supervisor" or (
                            (d.get("visibility") or "company") == "public"):
                        base = d
                        break
                if base is None:
                    raise HTTPException(404, "knowledge entry not found")
            fam = {base["id"]}
            cur = base
            while cur.get("supersedes_id") and cur["supersedes_id"] not in fam:
                fam.add(cur["supersedes_id"])
                cur = row(c, "SELECT * FROM knowledge_entries WHERE id=:i",
                          i=cur["supersedes_id"]) or {}
            cur = base
            while True:
                nxt = row(c, "SELECT * FROM knowledge_entries WHERE supersedes_id=:i",
                          i=cur.get("id"))
                if not nxt or nxt["id"] in fam:
                    break
                fam.add(nxt["id"])
                cur = nxt
            latest = max(fam, key=lambda i: (row(
                c, "SELECT version FROM knowledge_entries WHERE id=:i",
                i=i) or {"version": 0})["version"])
            likes = " OR ".join(f"e.body LIKE '%knowledge:{i}@%'" for i in sorted(fam))
            tenant_scope = None if ident == "supervisor" else _caller_tenant(request, c)
            q = ("SELECT COUNT(DISTINCT e.task_id) AS n, MAX(e.created_at) AS last_used"
                 ", GROUP_CONCAT(DISTINCT e.task_id) AS ids"
                 " FROM evidence e JOIN tasks t ON t.id=e.task_id"
                 f" WHERE e.kind='knowledge_citations' AND ({likes})")
            kw2: dict = {}
            if tenant_scope is not None:
                q += " AND t.tenant=:tn"
                kw2["tn"] = tenant_scope
            agg = row(c, q, **kw2) or {"n": 0, "last_used": None, "ids": None}
            id_list = sorted({int(i) for i in str(agg.get("ids") or "").split(",") if i.strip().isdigit()})
            return {"key": key, "id": base["id"],
                    "citation": f"knowledge:{latest}@v{(row(c, 'SELECT version FROM knowledge_entries WHERE id=:i', i=latest) or {'version': 0})['version']}",
                    "version": (row(c, "SELECT version FROM knowledge_entries WHERE id=:i", i=latest) or {"version": 0})["version"],
                    "applied_to_tasks": agg["n"] or 0,
                    "task_ids": id_list,
                    "last_used_at": agg["last_used"]}

    @app.get("/v1/knowledge/{kid}/history")
    def knowledge_history(kid: int, request: Request):
        """Version chain for one entry, oldest first. Append-only history
        is the point: nothing here is ever rewritten, only superseded."""
        ident = _identity(request)
        with eng.begin() as c:
            head = _knowledge_or_404(c, kid, ident, request)
            chain, seen, cur = [], {head["id"]}, head
            while cur:
                chain.insert(0, _krow(cur))
                nxt = cur.get("supersedes_id")
                if not nxt or nxt in seen:
                    break
                seen.add(nxt)
                cur = row(c, "SELECT * FROM knowledge_entries WHERE id=:i", i=nxt)
                if cur:
                    try:
                        _knowledge_or_404(c, cur["id"], ident, request)
                    except HTTPException:
                        raise HTTPException(404, "knowledge entry not found")
            return {"items": chain}

    @app.post("/v1/knowledge/{kid}/versions")
    def version_knowledge(kid: int, body: dict, request: Request):
        ident = _identity(request)
        title = str(body.get("title") or "").strip()
        kbody = str(body.get("body") or "")
        if not title or not kbody.strip():
            raise HTTPException(422, "title and body are required")
        with eng.begin() as c:
            old = _knowledge_or_404(c, kid, ident, request)
            if row(c, "SELECT id FROM knowledge_entries WHERE supersedes_id=:i", i=kid):
                raise HTTPException(409, "entry already superseded: version the latest instead")
            if ident != "supervisor":
                try:
                    caller_tenant = _caller_tenant(request, c)
                except HTTPException:
                    caller_tenant = None
                if caller_tenant != old["tenant"]:
                    raise HTTPException(403, "versions inherit the entry tenant: no cross-tenant writes")
            vis = body.get("visibility", old.get("visibility") or "company")
            vis = str(vis)
            if vis not in KNOWLEDGE_VIS:
                raise HTTPException(422, f"visibility must be one of {sorted(KNOWLEDGE_VIS)}")
            allowed = _allowed_list(body.get("allowed_users", old.get("allowed_users")))
            if (vis != old.get("visibility") or json.dumps(allowed) != (old.get("allowed_users") or "[]")):
                if ident != "supervisor" and ident != old.get("author"):
                    raise HTTPException(403, "only the creator or an admin changes visibility")
            if vis != "restricted" and allowed:
                raise HTTPException(422, "allowed_users only with visibility=restricted")
            tags = body.get("tags", json.loads(old.get("tags") or "[]"))
            # summary follows the newest body unless the caller pins one
            summary = body.get("summary") if "summary" in body else None
            return _krow(_knowledge_insert(
                c, old["tenant"], old["project"], old["kind"], title, kbody, ident,
                version=old["version"] + 1, supersedes=kid,
                visibility=vis, allowed=allowed,
                slug=old.get("slug") or None, tags=tags, summary=summary,
                slug_chain=[old.get("slug") or ""]))

    @app.post("/v1/knowledge/{kid}/confirm")
    def confirm_knowledge(kid: int, request: Request):
        ident = _identity(request)
        with eng.begin() as c:
            k = _knowledge_or_404(c, kid, ident, request)
            if ident != "supervisor" and ident != k.get("author"):
                raise HTTPException(403, "only the creator or an admin confirms")
            c.execute(text("UPDATE knowledge_entries SET verified=1 WHERE id=:i"), {"i": kid})
            _emit(c, k["tenant"], k["project"], "knowledge.confirmed", None,
                  {"id": kid, "citation": f"knowledge:{kid}@v{k['version']}"})
            return _krow(row(c, "SELECT * FROM knowledge_entries WHERE id=:i", i=kid))

    @app.post("/v1/knowledge/{kid}/review")
    def review_knowledge(kid: int, body: dict, request: Request):
        """Review verdict on an entry: verified | rejected | needs_revision,
        plus an optional reviewer note. Stack may act anywhere; a session
        owner inside its own tenant (broader than creator-only confirm, so
        tenant reviewers can work any entry). verified verdicts keep the
        verified flag in sync for legacy readers."""
        verdict = body.get("verdict")
        if verdict not in ("verified", "rejected", "needs_revision"):
            raise HTTPException(422, "verdict must be verified, rejected or needs_revision")
        note = body.get("note")
        if note is not None and not isinstance(note, str):
            raise HTTPException(422, "note must be a string")
        ident = _identity(request)
        with eng.begin() as c:
            k = _knowledge_or_404(c, kid, ident, request)
            if ident != "supervisor":
                if getattr(request.state, "session_role", None) != "owner":
                    raise HTTPException(403, "worker-only operation")
                if k["tenant"] != _caller_tenant(request, c):
                    raise HTTPException(404, "knowledge entry not found")
            c.execute(text("UPDATE knowledge_entries SET review_state=:v,"
                           " review_note=:n, verified=:ok WHERE id=:i"),
                      {"v": verdict, "n": (note or "").strip()[:2000],
                       "ok": 1 if verdict == "verified" else 0, "i": kid})
            _emit(c, k["tenant"], k["project"], "knowledge.reviewed", None,
                  {"id": kid, "verdict": verdict,
                   "citation": f"knowledge:{kid}@v{k['version']}"})
            return _krow(row(c, "SELECT * FROM knowledge_entries WHERE id=:i", i=kid))

    @app.post("/v1/knowledge/{kid}/archive")
    def archive_knowledge(kid: int, body: dict, request: Request):
        """Archive (or restore) an entry: archived rows leave search, counts
        and kind folders but stay readable by id. Same owner-or-stack gate
        as review. Append-only history is preserved — nothing is deleted."""
        if "archived" not in body:
            raise HTTPException(422, "archived is required")
        ident = _identity(request)
        with eng.begin() as c:
            k = _knowledge_or_404(c, kid, ident, request)
            if ident != "supervisor":
                if getattr(request.state, "session_role", None) != "owner":
                    raise HTTPException(403, "worker-only operation")
                if k["tenant"] != _caller_tenant(request, c):
                    raise HTTPException(404, "knowledge entry not found")
            c.execute(text("UPDATE knowledge_entries SET archived=:a WHERE id=:i"),
                      {"a": 1 if body["archived"] else 0, "i": kid})
            _emit(c, k["tenant"], k["project"], "knowledge.archived", None,
                  {"id": kid, "archived": bool(body["archived"])})
            return _krow(row(c, "SELECT * FROM knowledge_entries WHERE id=:i", i=kid))

    @app.post("/v1/knowledge/{kid}/pin")
    def pin_knowledge(kid: int, body: dict, request: Request):
        """Pin (or unpin) an entry: pinned rows get the same extra rank vote
        verified rows get, so they surface first. Same gate as review."""
        if "pinned" not in body:
            raise HTTPException(422, "pinned is required")
        ident = _identity(request)
        with eng.begin() as c:
            k = _knowledge_or_404(c, kid, ident, request)
            if ident != "supervisor":
                if getattr(request.state, "session_role", None) != "owner":
                    raise HTTPException(403, "worker-only operation")
                if k["tenant"] != _caller_tenant(request, c):
                    raise HTTPException(404, "knowledge entry not found")
            c.execute(text("UPDATE knowledge_entries SET pinned=:p WHERE id=:i"),
                      {"p": 1 if body["pinned"] else 0, "i": kid})
            _emit(c, k["tenant"], k["project"], "knowledge.pinned", None,
                  {"id": kid, "pinned": bool(body["pinned"])})
            return _krow(row(c, "SELECT * FROM knowledge_entries WHERE id=:i", i=kid))

    # -- Session commands (minimal): store / compact / snapshot / store-and-close.
    @app.post("/v1/knowledge/store")
    def knowledge_store(body: dict, request: Request):
        ident = _identity(request)
        items = body.get("items")
        if not isinstance(items, list) or not items:
            raise HTTPException(422, "items is required")
        out = []
        with eng.begin() as c:
            tenant, project = _session_scope(request, c, body)
            _registered(c, tenant, project)
            for it in items:
                if not isinstance(it, dict):
                    raise HTTPException(422, "each item needs kind/title/body")
                kind, title = str(it.get("kind") or ""), str(it.get("title") or "").strip()
                kbody = str(it.get("body") or "")
                vis = _req_visibility(it)
                if kind not in KNOWLEDGE_KINDS:
                    raise HTTPException(422, f"kind must be one of {sorted(KNOWLEDGE_KINDS)}")
                if not title or not kbody.strip():
                    raise HTTPException(422, "title and body are required")
                allowed = _allowed_list(it.get("allowed_users"))
                out.append(_krow(_knowledge_insert(c, tenant, project, kind, title,
                                                  kbody, ident, visibility=vis, allowed=allowed,
                                                  slug=it.get("slug"), tags=it.get("tags"),
                                                  summary=it.get("summary"))))
        return {"items": out}

    @app.post("/v1/knowledge/compact")
    def knowledge_compact(body: dict, request: Request):
        ident = _identity(request)
        title = str(body.get("title") or "Session compact").strip() or "Session compact"
        with eng.begin() as c:
            tenant, project = _session_scope(request, c, body)
            _registered(c, tenant, project)
            rows = [dict(r) for r in c.execute(text(
                "SELECT * FROM knowledge_entries WHERE tenant=:t AND project=:p"
                " AND verified=1 ORDER BY id DESC LIMIT 50"),
                {"t": tenant, "p": project}).mappings()]
            # latest-versions only
            sup = {r["supersedes_id"] for r in c.execute(
                text("SELECT supersedes_id FROM knowledge_entries WHERE supersedes_id IS NOT NULL")
            ).mappings()}
            rows = [r for r in rows if r["id"] not in sup]
            seen = [r for r in rows if _can_see(r, ident, _caller_tenant(request, c))]
            lines = [f"- {r['title']} ({'knowledge:' + str(r['id']) + '@v' + str(r['version'])})"
                     for r in reversed(seen)]
            cbody = "Compact of verified entries:\n" + ("\n".join(lines) if lines else "(none)") \
                + "\n\nSources:\n" + ("\n".join(
                    f"knowledge:{r['id']}@v{r['version']}" for r in reversed(seen)) if seen else "(none)")
            snap_vis, snap_allow = "company", []
            if any((r.get("visibility") or "company") == "restricted" for r in seen):
                snap_vis = "restricted"
                allow: set = set()
                for r in seen:
                    if (r.get("visibility") or "company") == "restricted":
                        allow.add(r.get("author"))
                        allow.update(_allowed_list(r.get("allowed_users")))
                allow.add(ident)
                snap_allow = sorted(a for a in allow if a)
            elif seen and all((r.get("visibility") or "company") == "public" for r in seen):
                snap_vis = "public"
            snap = _krow(_knowledge_insert(c, tenant, project, "snapshot", title, cbody, ident,
                                           visibility=snap_vis, allowed=snap_allow))
            return {"snapshot": snap,
                    "sources": [f"knowledge:{r['id']}@v{r['version']}" for r in reversed(seen)]}

    @app.post("/v1/knowledge/snapshot")
    def knowledge_snapshot(body: dict, request: Request):
        ident = _identity(request)
        with eng.begin() as c:
            tenant, project = _session_scope(request, c, body)
            _registered(c, tenant, project)
            rows = [dict(r) for r in c.execute(text(
                "SELECT * FROM knowledge_entries WHERE tenant=:t AND project=:p"
                " AND verified=1 ORDER BY id"),
                {"t": tenant, "p": project}).mappings()]
            sup = {r["supersedes_id"] for r in c.execute(
                text("SELECT supersedes_id FROM knowledge_entries WHERE supersedes_id IS NOT NULL")
            ).mappings()}
            rows = [_krow(r) for r in rows if r["id"] not in sup
                    and _can_see(r, ident, _caller_tenant(request, c))]
        root = Path(os.environ.get("ROUVR_DATA_DIR", ".data")) / "knowledge-snapshots"
        root.mkdir(parents=True, exist_ok=True)
        ts = _now().replace(":", "").replace("+", "")
        safe = lambda s: re.sub(r"[^A-Za-z0-9_.-]+", "_", s)[:64] or "_"
        path = root / f"{safe(tenant)}-{safe(project)}-{ts}.json"
        path.write_text(json.dumps({"tenant": tenant, "project": project,
                                    "exported_at": _now(), "items": rows}, indent=2))
        return {"path": str(path), "count": len(rows),
                "citations": [r["citation"] for r in rows]}

    @app.post("/v1/knowledge/store-and-close")
    def knowledge_store_and_close(body: dict, request: Request):
        ident = _identity(request)
        items = body.get("items") or []
        title = str(body.get("title") or "Session close").strip() or "Session close"
        with eng.begin() as c:
            tenant, project = _session_scope(request, c, body)
            _registered(c, tenant, project)
            stored = []
            for it in items:
                if not isinstance(it, dict):
                    raise HTTPException(422, "each item needs kind/title/body")
                kind, ititle = str(it.get("kind") or ""), str(it.get("title") or "").strip()
                kbody = str(it.get("body") or "")
                vis = _req_visibility(it)
                if kind not in KNOWLEDGE_KINDS or not ititle or not kbody.strip():
                    raise HTTPException(422, "bad store item")
                allowed = _allowed_list(it.get("allowed_users"))
                if vis != "restricted" and allowed:
                    raise HTTPException(422, "allowed_users only with visibility=restricted")
                stored.append(_krow(_knowledge_insert(c, tenant, project, kind, ititle,
                                                     kbody, ident,
                                                     visibility=vis, allowed=allowed)))
        comp = knowledge_compact({"tenant": tenant, "project": project, "title": title},
                                 request)
        snap = knowledge_snapshot({"tenant": tenant, "project": project}, request)
        return {"stored": stored, "compact": comp["snapshot"],
                "sources": comp["sources"], "snapshot": snap}

    @app.get("/v1/knowledge-rules")
    def knowledge_verified_rules(request: Request, project: str | None = None):
        ident = _identity(request)
        with eng.begin() as c:
            if ident == "supervisor":
                mine = None
            else:
                try:
                    mine = _caller_tenant(request, c)
                except HTTPException:
                    mine = None
            rows = [dict(r) for r in c.execute(text(
                "SELECT * FROM knowledge_entries WHERE kind='rule' AND verified=1"),
                ).mappings()]
            sup = {r["supersedes_id"] for r in c.execute(
                text("SELECT supersedes_id FROM knowledge_entries WHERE supersedes_id IS NOT NULL")
            ).mappings()}
            out = []
            for r in rows:
                if r["id"] in sup:
                    continue
                if project and r["project"] != project:
                    continue
                if ident != "supervisor" and not _can_see(r, ident, mine):
                    continue
                out.append(_krow(r))
            return {"items": out, "citations": [r["citation"] for r in out]}

    @app.post("/v1/knowledge/curate")
    def knowledge_curate(body: dict, request: Request):
        """On-demand curator (light): suggestions only, never verifies/deletes."""
        ident = _identity(request)
        with eng.begin() as c:
            tenant, project = _session_scope(request, c, body)
            _registered(c, tenant, project)
            rows = [dict(r) for r in c.execute(text(
                "SELECT * FROM knowledge_entries WHERE tenant=:t AND project=:p"),
                {"t": tenant, "p": project}).mappings()]
            sup = {r["supersedes_id"] for r in rows if r.get("supersedes_id")}
            latest = [r for r in rows if r["id"] not in sup
                      and _can_see(r, ident, _caller_tenant(request, c))]
        suggestions: list = []
        seen_title: dict = {}
        for r in latest:
            key = re.sub(r"\W+", " ", r["title"]).strip().lower()
            if key in seen_title:
                suggestions.append({"type": "duplicate",
                                    "entries": [seen_title[key], r["id"]],
                                    "note": f"same title: {r['title']!r}",
                                    "proposal": f"merge into knowledge:{seen_title[key]}"})
            else:
                seen_title[key] = r["id"]
        # Contradictions: only when two entries share a subject/scope anchor
        # (quoted phrase, backticked term, or D-numbered decision) and make
        # opposing claims about it (same predicate affirmed in one window,
        # negated in the other). Each flag carries confidence + reason.
        _ANCHOR_RE = re.compile(r'"([^"]{4,})"|`([^`]{4,})`|\b(D\d{1,3})\b')
        _PREDICATES = ("must", "always", "required", "should", "is", "are",
                       "allow", "enable", "use", "store", "delete", "deleted",
                       "keep", "support")
        _NEG_RE = re.compile(r"(?<!\w)(not|never|nothing|cannot|forbid|deny|reject|"
                               r"opposite|without|no)(?!\w)|n't")

        def _anchors(text: str) -> set:
            out = set()
            for m in _ANCHOR_RE.finditer(text):
                out.add(next(g for g in m.groups() if g).lower())
            return out

        def _window(text: str, anchor: str) -> str:
            i = text.lower().find(anchor)
            return text[max(0, i - 100):i + 100 + len(anchor)].lower()

        def _negated(window: str) -> bool:
            return bool(_NEG_RE.search(window))

        contradictions: list = []
        for i, a in enumerate(latest):
            fa = a["title"] + " " + a["body"]
            for b in latest[i + 1:]:
                fb = b["title"] + " " + b["body"]
                for anchor in _anchors(fa) & _anchors(fb):
                    wa, wb = _window(fa, anchor), _window(fb, anchor)
                    preds = [p for p in _PREDICATES if p in wa and p in wb]
                    if not preds or _negated(wa) == _negated(wb):
                        continue
                    conf = "high" if re.fullmatch(r"d\d{1,3}", anchor) else "medium"
                    contradictions.append({
                        "type": "contradiction",
                        "entries": [a["id"], b["id"]],
                        "confidence": conf,
                        "reason": f"shared anchor {anchor!r}: {preds[0]!r} affirmed on one side, "
                                  f"negated on the other",
                        "note": f"possible contradiction: {a['title']!r} vs {b['title']!r}",
                        "proposal": "human review, keep both until resolved"})
                    break
        contradictions.sort(key=lambda s: 0 if s["confidence"] == "high" else 1)
        # Medium-confidence matches are review hints, not flags: a shared
        # quoted phrase with asymmetric negation is usually complementary
        # context (hand-check 2026-11-02: 0/5 real), not an opposing claim.
        # Only all-high lists in small counts are presented as flags.
        contra_mode = "flags"
        if len(contradictions) > 15:
            contradictions, contra_mode = contradictions[:10], "review_hints"
        elif any(s["confidence"] != "high" for s in contradictions):
            contra_mode = "review_hints"
        suggestions.extend(contradictions)
        for r in latest:
            try:
                age = (datetime.now(timezone.utc)
                       - datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))).days
            except ValueError:
                age = 0
            if age > 90 and r["kind"] in ("note", "decision"):
                suggestions.append({"type": "stale", "entries": [r["id"]],
                                                "note": f"older than 90d ({age}d): {r['title']!r}",
                                                "proposal": "confirm still valid or version it"})
            low = (r["title"] + " " + r["body"]).lower()
            imperative = any(w in low for w in ("must", "should", "always", "never", "require"))
            if r["kind"] == "rule" and not imperative:
                suggestions.append({"type": "kind_fix", "entries": [r["id"]],
                                    "note": "rule without imperative wording",
                                    "proposal": "change kind to note/decision as a new version"})
            elif r["kind"] in ("note", "decision") and imperative and "standing rule" in low:
                suggestions.append({"type": "kind_fix", "entries": [r["id"]],
                                    "note": "looks like a standing rule",
                                    "proposal": "change kind to rule as a new version"})
        return {"suggestions": suggestions, "count": len(suggestions),
                "contradictions_mode": contra_mode}

    @app.post("/v1/tasks")
    def create_task(body: dict, request: Request):
        ident = _identity(request)
        with eng.begin() as c:
            tenant, project = _session_scope(request, c, body)
            _registered(c, tenant, project)
            n = _now()
            rid = c.execute(text("INSERT INTO tasks(title,intent,state,created_at,updated_at,created_by,"
                                 "tenant,project)"
                                 " VALUES(:t,:i,'open',:n,:n,:by,:tn,:p)"),
                            {"t": body.get("title", ""), "i": body.get("intent", ""), "n": n,
                             "by": ident, "tn": tenant, "p": project}).lastrowid
            _emit(c, tenant, project, "task.created", rid,
                  {"task_id": rid, "title": body.get("title", ""), "state": "open"})
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
        with eng.begin() as c:
            tenant, project = _session_scope(request, c, body)
            _registered(c, tenant, project)
            n = _now()
            rid = c.execute(text("INSERT INTO tasks(title,intent,state,created_at,updated_at,created_by,"
                                 "tenant,project)"
                                 " VALUES(:t,:i,'open',:n,:n,:by,:tn,:p)"),
                            {"t": body.get("title", ""), "i": body.get("intent", ""), "n": n,
                             "by": ident, "tn": tenant, "p": project}).lastrowid
            _emit(c, tenant, project, "task.created", rid,
                  {"task_id": rid, "title": body.get("title", ""), "state": "open"})
            s, h = _canon(contract)
            c.execute(text("INSERT INTO contracts(task_id,body,sha256,created_at) VALUES(:i,:b,:h,:n)"),
                      {"i": rid, "b": s, "h": h, "n": n})
            t = row(c, "SELECT * FROM tasks WHERE id=:i", i=rid)
            t["contracts"] = [dict(r) for r in c.execute(
                text("SELECT * FROM contracts WHERE task_id=:i ORDER BY id"), {"i": rid}).mappings()]
            return t

    @app.post("/v1/chat")
    def chat(body: dict, request: Request):
        """Step 2 + Slice C: one supervised model turn -> intake router, over SSE.

        Streams ``token`` events as the model responds, then a ``result``
        event with the reply, created ids, and receipt source. A clarifying
        question (at most one) creates nothing; otherwise the slice task
        (contract = acceptance criteria), child tasks, slice-link evidence,
        and per-row ``chat:create`` receipts naming the message id are all
        written in one transaction with their ``task.created`` outbox rows.

        Slice C: a classified turn (intake block) routes chat (answer + log
        row), knowledge (answer + entry), task (intake task first, then the
        pipeline) or ambiguous (one question, defaulting to chat). A
        plan-shaped turn is ALWAYS saved as an unverified plan knowledge
        entry first, then the autonomy-gated confirm gate: ask (offer with
        Convert/Edit/Just-keep-it), or auto-convert for imperatives, free
        autonomy, and docs/research under only-important. plan_action turns
        (convert/edit/keep, or a plain yes/no/edit reply to a pending
        offer) never call the model. Every classification is recorded with
        who/when/level and is reclassifiable by appending.
        """
        from fastapi.responses import StreamingResponse
        message = body.get("message", "")
        if not isinstance(message, str) or not message.strip():
            raise HTTPException(422, "message is required")
        if len(message) > _chatmod.MESSAGE_LIMIT:
            raise HTTPException(422,
                                f"message exceeds {_chatmod.MESSAGE_LIMIT} characters")
        message_id = body.get("message_id") or _secrets.token_hex(8)
        if not isinstance(message_id, str) or not message_id.strip():
            raise HTTPException(422, "message_id must be a string")
        message_id = message_id.strip()
        autonomy_override = body.get("autonomy")
        if autonomy_override is not None and autonomy_override not in _chatmod.AUTONOMY_LEVELS:
            raise HTTPException(422, f"autonomy must be one of {list(_chatmod.AUTONOMY_LEVELS)}")
        plan_action = body.get("plan_action")
        if plan_action is not None and plan_action not in ("convert", "edit", "keep"):
            raise HTTPException(422, "plan_action must be convert, edit or keep")
        plan_entry = body.get("plan_entry")
        if plan_entry is not None and (isinstance(plan_entry, bool)
                                       or not isinstance(plan_entry, int)):
            raise HTTPException(422, "plan_entry must be an integer knowledge id")
        try:
            provider = _chatmod.provider_from_env()
        except (ValueError, RuntimeError) as e:
            raise HTTPException(502, f"model provider unavailable: {e}")
        with eng.begin() as c:  # fail fast on scope before streaming
            tenant, project = _session_scope(request, c, body)
            _registered(c, tenant, project)

        def _frame(event: str, payload: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

        # ---- Slice C intake router helpers (share the chat closure scope:
        # tenant, project, message_id, provider, autonomy_override). Every
        # helper takes an open connection; yields happen outside `with`
        # blocks so transactions never span SSE frames.
        def _chat_level(c) -> str:
            """Effective autonomy: per-turn override, stored setting, default."""
            if autonomy_override is not None:
                return autonomy_override
            r = row(c, "SELECT level FROM chat_autonomy WHERE tenant=:t AND project=:p",
                    t=tenant, p=project)
            if r and r.get("level") in _chatmod.AUTONOMY_LEVELS:
                return r["level"]
            return _chatmod.DEFAULT_AUTONOMY

        def _log_turn(c, cls: str, reply: str) -> None:
            """One append-only conversation-log row per turn. No DELETE path
            touches chat_turns: history is never hard-deleted."""
            c.execute(text("INSERT INTO chat_turns(tenant,project,message_id,message,reply,class,"
                           "created_at) VALUES(:t,:p,:m,:g,:r,:c,:n)"),
                      {"t": tenant, "p": project, "m": message_id, "g": message.strip(),
                       "r": reply, "c": cls or "", "n": _now()})

        def _record_class(c, ident: str, level: str, cls: str, conf: float,
                          cat: str | None, created: dict, reclass_of=None) -> dict:
            rid = c.execute(text("INSERT INTO intake_classifications(tenant,project,turn_id,class,"
                                 "confidence,category,created_ids,autonomy_level,decided_by,"
                                 "decided_at,reclass_of,created_at)"
                                 " VALUES(:t,:p,:u,:c,:f,:g,:d,:l,:b,:a,:r,:n)"),
                            {"t": tenant, "p": project, "u": message_id, "c": cls,
                             "f": conf, "g": cat or "", "d": json.dumps(created),
                             "l": level, "b": ident, "a": _now(), "r": reclass_of,
                             "n": _now()}).lastrowid
            return row(c, "SELECT * FROM intake_classifications WHERE id=:i", i=rid)

        def _plan_entries(c) -> list:
            """Latest-version plan entries (tagged "plan"), newest first."""
            rows = [dict(r) for r in c.execute(text(
                "SELECT * FROM knowledge_entries WHERE tenant=:t AND project=:p"
                " AND tags LIKE '%\"plan\"%' ORDER BY id DESC"),
                {"t": tenant, "p": project}).mappings()]
            sup = {r["supersedes_id"] for r in rows if r.get("supersedes_id")}
            return [r for r in rows if r["id"] not in sup]

        def _decided_plan_ids(c) -> set:
            """Plan entries already converted or kept (from classification rows)."""
            out = set()
            for r in c.execute(text("SELECT created_ids FROM intake_classifications"
                                    " WHERE tenant=:t AND project=:p ORDER BY id DESC LIMIT 200"),
                               {"t": tenant, "p": project}).mappings():
                try:
                    d = json.loads(r["created_ids"])
                except ValueError:
                    continue
                if isinstance(d, dict) and (d.get("converted") or d.get("kept")) \
                        and isinstance(d.get("plan_entry"), int):
                    out.add(d["plan_entry"])
            return out

        def _pending_plan(c):
            """Newest plan entry with no convert/keep decision, or None."""
            decided = _decided_plan_ids(c)
            for e in _plan_entries(c):
                if e["id"] not in decided:
                    return e
            return None

        def _plan_entry_for_turn(c):
            """Plan entry already saved for this turn id (resend dedupe)."""
            want = f"turn-{message_id}"
            for e in _plan_entries(c):
                try:
                    tags = json.loads(e.get("tags") or "[]")
                except ValueError:
                    tags = []
                if want in tags:
                    return e
            return None

        def _resolve_plan_entry(c, explicit):
            """Explicit plan_entry id (scoped + plan-tagged + latest) or the
            pending offer. Returns None when nothing resolves."""
            if explicit is not None:
                e = row(c, "SELECT * FROM knowledge_entries WHERE id=:i", i=explicit)
                if not e or e["tenant"] != tenant or e["project"] != project:
                    return None
                try:
                    tags = json.loads(e.get("tags") or "[]")
                except ValueError:
                    tags = []
                if "plan" not in tags:
                    return None
                nxt = row(c, "SELECT id FROM knowledge_entries WHERE supersedes_id=:i",
                          i=e["id"])
                if nxt:
                    return None  # superseded: version the latest instead
                return e
            return _pending_plan(c)

        def _save_plan_entry(c, ident, spec, tasks, category, cls):
            """A plan/proposal is ALWAYS saved as an unverified knowledge
            entry (source = turn id, plan slug) before anything else."""
            titles = [t["title"] for t in tasks]
            summary = f"{len(titles)} steps: " + ", ".join(titles[:5])
            if len(titles) > 5:
                summary += f" (+{len(titles) - 5} more)"
            return _knowledge_insert(
                c, tenant, project, "note", f"Plan: {spec['title']}",
                json.dumps({"source": message_id, "category": category, "class": cls,
                            "slice": spec, "tasks": tasks}),
                ident, tags=["plan", f"turn-{message_id}"], summary=summary[:160])

        _EXPLICIT_PLAN_RE = re.compile(
            r"\b(prep\w*|draft\w*|propos\w*|draw up|write(?: up)?)"
            r"\b.{0,40}\bplans?\b"
            r"|\bplans?\b.{0,20}\bproposal\b", re.IGNORECASE)

        def _offer_result(entry, tasks, cls, level, reply, category):
            return {"message_id": message_id, "reply": reply, "question": None,
                    "slice": None, "tasks": [], "created": 0,
                    "badge": {"kind": "saved", "label": f"Saved: {entry['slug']}"},
                    "offer": {"plan_entry_id": entry["id"], "plan_slug": entry["slug"],
                              "task_titles": [t["title"] for t in tasks],
                              "category": category or "feature"},
                    "classification": cls, "autonomy": level}

        def _existing_intake_task(c):
            """Task already created from this turn id (resend dedupe). Prefers
            the plain intake task (no plan slug) over plan-convert rows that
            share the chat:create key space. Audit rows are still appended."""
            rows = [dict(r) for r in c.execute(text(
                "SELECT t.*, r.body AS _rbody FROM tasks t"
                " JOIN receipts r ON r.task_id=t.id"
                " WHERE r.idempotency_key=:m AND r.action='chat:create'"
                " AND t.tenant=:t AND t.project=:p ORDER BY t.id"),
                {"m": message_id, "t": tenant, "p": project}).mappings()]
            plain, fallback = None, None
            for rr in rows:
                body = rr.pop("_rbody")
                if fallback is None:
                    fallback = rr
                try:
                    slug = (json.loads(body or "{}") or {}).get("plan_slug")
                except ValueError:
                    slug = None
                if slug is None and plain is None:
                    plain = rr
            return plain if plain is not None else fallback

        def _entry_for_turn_id(c):
            """Non-plan knowledge entry already saved for this turn id (resend
            dedupe). Plan entries resolve through the offer flow instead."""
            want = f"turn-{message_id}"
            for e in [dict(r) for r in c.execute(text(
                    "SELECT * FROM knowledge_entries WHERE tenant=:t AND project=:p"
                    " ORDER BY id DESC"),
                    {"t": tenant, "p": project}).mappings()]:
                try:
                    tags = json.loads(e.get("tags") or "[]")
                except ValueError:
                    continue
                if want in tags and "plan" not in tags:
                    nxt = row(c, "SELECT id FROM knowledge_entries"
                                 " WHERE supersedes_id=:i", i=e["id"])
                    if not nxt:
                        return e
            return None

        def _create_intake_task(c, ident, msg_id, model_name, title, intent, criteria,
                                category, plan_slug, sealed_piece=None):
            """One task with category + contract (the task branch: created
            FIRST, state open = queued for pickup, then the pipeline).
            sealed_piece = ((ok, files|error), criteria) pre-authored before
            the txn, or None to skip sealing (legacy callers)."""
            n = _now()
            tid = c.execute(text("INSERT INTO tasks(title,intent,state,created_at,updated_at,"
                                 "created_by,tenant,project) VALUES(:t,:i,'open',:n,:n,:by,:tn,:p)"),
                            {"t": title, "i": intent, "n": n,
                             "by": ident, "tn": tenant, "p": project}).lastrowid
            _emit(c, tenant, project, "task.created", tid,
                  {"task_id": tid, "title": title, "state": "open"})
            s, h = _canon(_contract_body(criteria, title, category, plan_slug))
            c.execute(text("INSERT INTO contracts(task_id,body,sha256,created_at)"
                           " VALUES(:i,:b,:h,:n)"), {"i": tid, "b": s, "h": h, "n": n})
            if sealed_piece is not None:
                (sealed, seal_criteria) = sealed_piece
                _seal_one_task(c, tid, sealed, seal_criteria)
            s, _ = _canon({"source": msg_id, "role": "task", "model": model_name,
                           "category": category, "plan_slug": plan_slug})
            c.execute(text("INSERT INTO receipts(task_id,idempotency_key,action,body,created_at)"
                           " VALUES(:i,:k,'chat:create',:b,:n)"),
                      {"i": tid, "k": msg_id, "b": s, "n": n})
            return row(c, "SELECT * FROM tasks WHERE id=:i", i=tid)

        def _build_convert_seals(spec, tasks, want_head):
            """Seal jobs for one Convert, keyed by creation order. Slice/head
            suites cover the full acceptance criteria; each kid suite covers
            its own step (a whole-slice suite on a step would test other
            steps' work). Authored BEFORE the write txn (see _author_seal_batch)."""
            jobs = []
            if want_head:
                jobs.append(("head", list(spec["acceptance_criteria"])))
            jobs.append(("slice", list(spec["acceptance_criteria"])))
            for i, t in enumerate(tasks):
                step = f"Step: {t['title']}. {t.get('intent') or ''}".strip()
                jobs.append((f"kid-{i}", [step]))
            batch = _author_seal_batch(jobs)
            return {"slice_title": spec["title"], "n_kids": len(tasks),
                    "head": batch.get("head"), "slice": batch.get("slice"),
                    "kids": [batch.get(f"kid-{i}") for i in range(len(tasks))]}

        def _do_convert(c, ident, msg_id, model_name, entry, level, cls, conf, cat,
                        pre_sealed=None):
            """Convert = confirm the plan entry, then one slice + one task
            per step. Idempotent on the plan entry: the plan:convert receipt
            replays the same slice so double-click/double-send yields one.
            pre_sealed (from _build_convert_seals, authored before the txn)
            seals each created task; replays ignore it."""
            spec_d = _parse_plan_body(entry)
            if spec_d is None:
                raise HTTPException(422, "plan entry body is not a plan")
            spec, tasks = spec_d["slice"], spec_d["tasks"]
            category = spec_d.get("category") or cat or "feature"
            own_cls = spec_d.get("class") if spec_d.get("class") in _chatmod.INTAKE_CLASSES \
                else "task"
            rep = row(c, "SELECT * FROM receipts WHERE idempotency_key=:k"
                         " AND action='plan:convert'",
                      k=f"plan-convert:{entry['id']}")
            first_row = None
            if rep is not None:
                try:
                    rb = json.loads(rep["body"])
                except ValueError:
                    rb = {}
                slice_row = row(c, "SELECT * FROM tasks WHERE id=:i",
                                i=rb.get("slice_id", rep["task_id"]))
                if slice_row is None:
                    raise HTTPException(409, "plan was converted but its slice is gone")
                all_rows = [row(c, "SELECT * FROM tasks WHERE id=:i", i=i)
                            for i in (rb.get("task_ids") or [])]
                all_rows = [t for t in all_rows if t]
                replayed = True
            else:
                _confirm_entry(c, ident, entry)
                if pre_sealed is not None and (
                        pre_sealed.get("slice_title") != spec["title"]
                        or pre_sealed.get("n_kids") != len(tasks)):
                    # Plan changed between pre-txn sealing and this write txn
                    # (edited entry): sealed suites no longer match — mark
                    # every task pending and let POST /v1/tasks/{id}/seal retry.
                    void = ("plan changed between seal and convert; "
                            "use POST /v1/tasks/{id}/seal to retry")
                    pre_sealed = {
                        "slice_title": spec["title"], "n_kids": len(tasks),
                        "head": ((False, void), list(spec["acceptance_criteria"])),
                        "slice": ((False, void), list(spec["acceptance_criteria"])),
                        "kids": [((False, void), [f"Step: {t['title']}"])
                                 for t in tasks]}
                head_piece = (pre_sealed or {}).get("head")
                if own_cls == "task" or cls == "task":
                    first_row = _create_intake_task(
                        c, ident, msg_id, model_name, spec["title"], spec["intent"],
                        spec["acceptance_criteria"], category, entry["slug"],
                        sealed_piece=head_piece)
                slice_row, kid_rows = _materialize_plan(
                    c, ident, msg_id, model_name, spec, tasks, category, entry["slug"],
                    tenant, project, pre_sealed=pre_sealed)
                all_rows = ([first_row] if first_row else []) + kid_rows
                s, _ = _canon({"plan_entry": entry["id"], "plan_slug": entry["slug"],
                               "message_id": msg_id, "slice_id": slice_row["id"],
                               "task_ids": [t["id"] for t in all_rows]})
                c.execute(text("INSERT INTO receipts(task_id,idempotency_key,action,body,created_at)"
                               " VALUES(:i,:k,'plan:convert',:b,:n)"),
                          {"i": slice_row["id"], "k": f"plan-convert:{entry['id']}",
                           "b": s, "n": _now()})
                replayed = False
            lines = []
            # head = the intake-first task when the plan originated from a
            # task-class (imperative) turn: fresh converts know it directly,
            # replays re-derive it (first row that is neither the slice nor
            # one of its slice_member children).
            head = first_row
            if head is None and replayed and all_rows \
                    and all_rows[0]["id"] != slice_row["id"] \
                    and not _is_slice_child(c, all_rows[0]["id"], slice_row["id"]):
                head = all_rows[0]
            if head is not None:
                lines.append(f"Task #{head['id']} {head['title']} (open).")
            lines.append(f"Converted plan '{entry['slug']}' into Slice #{slice_row['id']}"
                         f" {slice_row['title']}: {len(all_rows) - (1 if head else 0)} task(s).")
            lines.append(_chatmod.render_cards(
                slice_row, [t for t in all_rows if t["id"] != slice_row["id"]]))
            if replayed:
                lines.append("(already converted — showing the existing slice.)")
            reply = "\n".join(lines)
            _log_turn(c, "task", reply)
            created = {"plan_entry": entry["id"], "plan_slug": entry["slug"],
                       "slice": slice_row["id"],
                       "tasks": [t["id"] for t in all_rows if t["id"] != slice_row["id"]]}
            if head is not None:
                created["task"] = head["id"]
            if replayed:
                created["replayed"] = True
            else:
                created["confirmed"] = True
            _record_class(c, ident, level, "task", conf, category, created)
            kids_only = [t for t in all_rows if t["id"] != slice_row["id"]]
            badge_id = head["id"] if head is not None else slice_row["id"]
            return {"message_id": message_id, "reply": reply, "question": None,
                    "slice": {"id": slice_row["id"], "title": slice_row["title"]},
                    "tasks": [_task_card(c, t) for t in all_rows],
                    "created": 1 + len(all_rows),
                    "badge": {"kind": "task", "label": f"Task #{badge_id}"},
                    "classification": "task", "autonomy": level}

        def _is_slice_child(c, tid, sid) -> bool:
            for r in c.execute(text("SELECT body FROM evidence WHERE task_id=:i"
                                    " AND kind='slice_member'"), {"i": tid}).mappings():
                try:
                    if json.loads(r["body"]).get("slice_id") == sid:
                        return True
                except ValueError:
                    continue
            return False

        def _run_plan_action(action):
            """Convert/Edit/Just-keep-it on a plan offer. Never calls the
            model for convert/keep; edit runs exactly one revision round.
            Returns SSE frame strings (token + result, or error)."""
            ident = _identity(request)
            model_name = provider["model"]
            with eng.begin() as c:
                level = _chat_level(c)
            if action == "edit":
                rev_spec, rev_tasks = None, None
                try:
                    maybe = _chatmod.parse_intake_turn(message.strip())
                except ValueError:
                    maybe = None
                if maybe is not None and maybe["slice"] is not None:
                    rev_spec, rev_tasks = maybe["slice"], maybe["tasks"]
                else:
                    with eng.begin() as c:
                        entry = _resolve_plan_entry(c, plan_entry)
                        cur = _parse_plan_body(entry) if entry is not None else None
                    if entry is None or cur is None:
                        return [_frame("error", {"detail":
                            "no pending plan to edit — say what you want planned first"})]
                    rev_messages = [
                        {"role": "system", "content": _chatmod.REVISE_PROMPT},
                        {"role": "user", "content":
                         "Current plan JSON:\n" + json.dumps(cur)
                         + "\n\nRevision note:\n" + message.strip()}]
                    try:
                        rev_text = _chatmod.complete_once(rev_messages, provider)
                    except RuntimeError as e:
                        return [_frame("error", {"detail": f"model request failed: {e}"})]
                    try:
                        rev = _chatmod.parse_intake_turn(rev_text)
                    except ValueError as e:
                        return [_frame("error", {"detail": f"unusable revision: {e}"})]
                    if rev["slice"] is None:
                        return [_frame("error", {"detail":
                            "unusable revision: no plan in the revised turn"})]
                    rev_spec, rev_tasks = rev["slice"], rev["tasks"]
                try:
                    with eng.begin() as c:
                        entry = _resolve_plan_entry(c, plan_entry)
                        if entry is None:
                            raise HTTPException(404, "no pending plan to edit")
                        old_d = _parse_plan_body(entry) or {}
                        cls0 = old_d.get("class")
                        if cls0 not in _chatmod.INTAKE_CLASSES:
                            cls0 = "task"
                        cat0 = old_d.get("category")
                        if cat0 not in _chatmod.TASK_CATEGORIES:
                            cat0 = None
                        try:
                            allowed = json.loads(entry.get("allowed_users") or "[]")
                        except ValueError:
                            allowed = []
                        new_entry = _knowledge_insert(
                            c, tenant, project, entry["kind"], entry["title"],
                            json.dumps({"source": message_id, "category": cat0,
                                        "class": cls0, "slice": rev_spec,
                                        "tasks": rev_tasks}),
                            ident, version=entry["version"] + 1, supersedes=entry["id"],
                            visibility=entry.get("visibility") or "company",
                            allowed=allowed, slug=entry["slug"],
                            tags=["plan", f"turn-{message_id}"],
                            summary=(f"{len(rev_tasks)} steps: "
                                     + ", ".join(t["title"] for t in rev_tasks[:5]))[:160],
                            slug_chain=[entry["slug"]])
                        reply = _chatmod.OFFER_TEMPLATE.format(
                            n=len(rev_tasks),
                            titles=", ".join(t["title"] for t in rev_tasks))
                        _log_turn(c, cls0, reply)
                        _record_class(c, ident, level, cls0, 1.0, cat0,
                                      {"plan_entry": new_entry["id"],
                                       "revised_from": entry["id"], "offered": True})
                        result = _offer_result(new_entry, rev_tasks, cls0, level,
                                               reply, cat0)
                except HTTPException as e:
                    return [_frame("error", {"detail": str(e.detail)})]
                return [_frame("token", {"text": reply}), _frame("result", result)]
            try:
                with eng.begin() as c:
                    entry = _resolve_plan_entry(c, plan_entry)
                    if entry is None:
                        raise HTTPException(404, "no pending plan — say what you want"
                                                 " planned first")
                    spec_d0 = _parse_plan_body(entry) or {}
                    already = row(c, "SELECT task_id FROM receipts WHERE idempotency_key=:k"
                                     " AND action='plan:convert'",
                                  k=f"plan-convert:{entry['id']}")
                pre_sealed = None
                if action != "keep" and already is None and spec_d0.get("slice"):
                    # Slow model calls with NO open txn (see task branch).
                    spec0, tasks0 = spec_d0["slice"], spec_d0.get("tasks") or []
                    own0 = spec_d0.get("class") if spec_d0.get("class") in _chatmod.INTAKE_CLASSES \
                        else "task"
                    cls0 = spec_d0.get("class")
                    if cls0 not in _chatmod.INTAKE_CLASSES:
                        cls0 = "task"
                    pre_sealed = _build_convert_seals(
                        spec0, tasks0, want_head=(own0 == "task" or cls0 == "task"))
                with eng.begin() as c:
                    entry = _resolve_plan_entry(c, plan_entry)
                    if entry is None:
                        raise HTTPException(404, "no pending plan — say what you want"
                                                 " planned first")
                    if action == "keep":
                        reply = (f"Kept as knowledge only (Saved: {entry['slug']})"
                                 " — no tasks created.")
                        _log_turn(c, "knowledge", reply)
                        _record_class(c, ident, level, "knowledge", 1.0, None,
                                      {"plan_entry": entry["id"], "kept": True})
                        result = {"message_id": message_id, "reply": reply,
                                  "question": None, "slice": None, "tasks": [],
                                  "created": 0,
                                  "badge": {"kind": "saved",
                                            "label": f"Saved: {entry['slug']}"},
                                  "classification": "knowledge", "autonomy": level}
                    else:  # convert
                        spec_d0 = _parse_plan_body(entry) or {}
                        cls0 = spec_d0.get("class")
                        if cls0 not in _chatmod.INTAKE_CLASSES:
                            cls0 = "task"
                        cat0 = spec_d0.get("category")
                        if cat0 not in _chatmod.TASK_CATEGORIES:
                            cat0 = None
                        result = _do_convert(c, ident, message_id, model_name,
                                             entry, level, cls0, 1.0, cat0,
                                             pre_sealed=pre_sealed)
                        reply = result["reply"]
            except HTTPException as e:
                return [_frame("error", {"detail": str(e.detail)})]
            return [_frame("token", {"text": reply}), _frame("result", result)]

        def _chat_identity_block(c, scope_tenant: str | None) -> str:
            """Live per-request identity bundle: tenant/project, tool names
            from the live MCP registry, skill catalogue, open work, verified
            rules. Uses the closure's authoritative scope (already resolved
            by _session_scope, fail closed); the supervisor identity sees all
            tenants exactly like the read paths below."""
            ident = _identity(request)
            mine = scope_tenant if ident != "supervisor" else None
            try:
                tools = _chatmod.live_tool_manifest()
            except Exception:
                tools = []
            sup = {s["supersedes_id"] for s in [dict(x) for x in c.execute(text(
                "SELECT supersedes_id FROM knowledge_entries"
                " WHERE supersedes_id IS NOT NULL")).mappings()]}
            tcond = "" if mine is None else " AND tenant=:t"
            tkw = {} if mine is None else {"t": mine}
            skills = []
            for r in [dict(x) for x in c.execute(text(
                    "SELECT * FROM knowledge_entries WHERE kind='skill'"
                    f"{tcond} ORDER BY id DESC LIMIT 20"), tkw).mappings()]:
                if r["id"] in sup:
                    continue
                if not _can_see(r, ident, mine):
                    continue
                blurb = (r.get("summary") or r.get("body") or "").strip().splitlines()
                skills.append({"slug": r.get("slug") or "",
                               "title": r["title"],
                               "blurb": (blurb[0][:100] if blurb else "")})
                if len(skills) >= 8:
                    break
            open_rows = [dict(x) for x in c.execute(text(
                "SELECT id, title FROM tasks WHERE state='open'"
                f"{' AND tenant=:t' if mine is not None else ''}"
                " ORDER BY id DESC LIMIT 5"), tkw).mappings()]
            open_count = c.execute(text(
                "SELECT COUNT(*) FROM tasks WHERE state='open'"
                f"{' AND tenant=:t' if mine is not None else ''}"), tkw).scalar()
            rules = []
            for r in [dict(x) for x in c.execute(text(
                    "SELECT * FROM knowledge_entries WHERE kind='rule'"
                    " AND verified=1"
                    f"{tcond} ORDER BY id DESC LIMIT 20"), tkw).mappings()]:
                if r["id"] in sup:
                    continue
                if not _can_see(r, ident, mine):
                    continue
                rules.append(f"knowledge:{r['id']}@v{r['version']}")
                if len(rules) >= 5:
                    break
            return _chatmod.identity_block(
                tools, skills, open_count or 0,
                [t["title"] for t in open_rows], rules,
                tenant if mine is None else mine, project)

        def _run_intake_turn(turn, turn_text, retries):
            """One classified model turn: route chat/knowledge/task/ambiguous,
            or the plan confirm gate for plan-shaped turns. Returns SSE
            frame strings (token + result, or error)."""
            ident = _identity(request)
            model_name = provider["model"]
            intake = turn["intake"]
            cls, conf = intake["class"], intake["confidence"]
            cat = intake["category"]
            msg_text = message.strip()
            if turn["question"] is not None:
                with eng.begin() as c:
                    level = _chat_level(c)
                    _log_turn(c, cls, turn["question"])
                    _record_class(c, ident, level, cls, conf, cat, {})
                result = {"message_id": message_id, "reply": turn["question"],
                          "question": turn["question"],
                          "slice": None, "tasks": [], "created": 0,
                          "badge": {"kind": "logged", "label": "Logged"},
                          "classification": cls, "autonomy": level}
                return [_frame("result", result)]
            if turn["slice"] is not None:
                spec, tasks = turn["slice"], turn["tasks"]
                try:
                    with eng.begin() as c:
                        level = _chat_level(c)
                    # Explicit plan phrasing routes to plan intent even when
                    # the model classifies the turn as a task: the user asked
                    # for a proposal, not for work to start.
                    explicit_plan = bool(_EXPLICIT_PLAN_RE.search(msg_text))
                    auto = (cls == "task" or level == "free" or (
                        level == "only-important"
                        and (cat or "feature") in _chatmod.AUTO_CONVERT_CATEGORIES)
                        ) and not explicit_plan
                    pre_sealed = None
                    if auto:
                        # Slow model calls with NO open txn (see task branch).
                        own = spec.get("class") if spec.get("class") in _chatmod.INTAKE_CLASSES \
                            else "task"
                        pre_sealed = _build_convert_seals(
                            spec, tasks, want_head=(own == "task" or cls == "task"))
                    with eng.begin() as c:
                        level = _chat_level(c)
                        entry = _plan_entry_for_turn(c)
                        if entry is None:
                            entry = _save_plan_entry(c, ident, spec, tasks, cat, cls)
                        if auto:
                            result = _do_convert(c, ident, message_id, model_name,
                                                 entry, level, cls, conf, cat,
                                                 pre_sealed=pre_sealed)
                            reply = result["reply"]
                        else:
                            reply = ((turn["answer"] + "\n\n")
                                     if turn["answer"].strip() else "") \
                                + _chatmod.OFFER_TEMPLATE.format(
                                    n=len(tasks),
                                    titles=", ".join(t["title"] for t in tasks))
                            _log_turn(c, cls, reply)
                            _record_class(c, ident, level, cls, conf, cat,
                                          {"plan_entry": entry["id"], "offered": True})
                            result = _offer_result(entry, tasks, cls, level, reply,
                                                   cat)
                except HTTPException as e:
                    return [_frame("error", {"detail": str(e.detail)})]
                return [_frame("token", {"text": reply}), _frame("result", result)]
            if cls == "knowledge":
                ko = turn["knowledge"] or {}
                kind = ko.get("kind") or "note"
                if kind not in KNOWLEDGE_KINDS:
                    kind = "note"
                src = turn["answer"] or msg_text
                title = (ko.get("title") or "").strip() \
                    or (src.strip().splitlines()[0][:60] if src.strip() else "")
                bodyk = (ko.get("body") or "").strip() or src
                if not title or not bodyk.strip():
                    return [_frame("error", {"detail":
                        "unusable model turn: knowledge needs a title and a body"})]
                try:
                    with eng.begin() as c:
                        level = _chat_level(c)
                        krow = _entry_for_turn_id(c)
                        replayed = krow is not None
                        if krow is None:
                            krow = _knowledge_insert(c, tenant, project, kind, title,
                                                     bodyk, ident,
                                                     tags=[kind, f"turn-{message_id}"])
                        reply = ((turn["answer"] + "\n")
                                 if turn["answer"].strip() else "") \
                            + f"Saved: {krow['slug']}" \
                            + (" (already saved.)" if replayed else "")
                        _log_turn(c, "knowledge", reply)
                        _record_class(c, ident, level, "knowledge", conf, cat,
                                      {"knowledge": [krow["id"]],
                                       **({"replayed": True} if replayed else {})})
                        result = {"message_id": message_id, "reply": reply,
                                  "question": None, "slice": None, "tasks": [],
                                  "created": 0,
                                  "badge": {"kind": "saved",
                                            "label": f"Saved: {krow['slug']}"},
                                  "knowledge": {"id": krow["id"], "slug": krow["slug"]},
                                  "classification": "knowledge", "autonomy": level}
                except HTTPException as e:
                    return [_frame("error", {"detail": str(e.detail)})]
                return [_frame("token", {"text": reply}), _frame("result", result)]
            if cls == "task":
                spec = turn["task"] or {"title": msg_text[:80], "intent": msg_text}
                criteria = turn["contract"]["acceptance_criteria"] if turn["contract"] \
                    else [f"Satisfy the request: {msg_text[:160]}"]
                category = cat or "feature"
                try:
                    # Replay check first (short txn): a double-send replays
                    # without spending a second seal authoring call.
                    with eng.begin() as c:
                        level = _chat_level(c)
                        trow = _existing_intake_task(c)
                        replayed = trow is not None
                    sealed_piece = None
                    if trow is None:
                        # Slow model call with NO open txn (never hold a
                        # SQLite write lock across 15-60 s of generation).
                        sealed_piece = _author_seal_batch([("task", list(criteria))]).get("task")
                    with eng.begin() as c:
                        if trow is None:
                            trow = _existing_intake_task(c)
                        replayed = trow is not None
                        if trow is None:
                            trow = _create_intake_task(c, ident, message_id, model_name,
                                                       spec["title"], spec["intent"],
                                                       criteria, category, None,
                                                       sealed_piece=sealed_piece)
                        reply = ((turn["answer"] + "\n")
                                 if turn["answer"].strip() else "") \
                            + f"Task #{trow['id']} {trow['title']} (open)." \
                            + (" (already created — showing the existing task.)"
                               if replayed else "")
                        _log_turn(c, "task", reply)
                        _record_class(c, ident, level, "task", conf, category,
                                      {"task": trow["id"],
                                       **({"replayed": True} if replayed else {})})
                        result = {"message_id": message_id, "reply": reply,
                                  "question": None, "slice": None,
                                  "tasks": [_task_card(c, trow)],
                                  "created": 1,
                                  "badge": {"kind": "task",
                                            "label": f"Task #{trow['id']}"},
                                  "classification": "task", "autonomy": level}
                except HTTPException as e:
                    return [_frame("error", {"detail": str(e.detail)})]
                return [_frame("token", {"text": reply}), _frame("result", result)]
            # chat, or ambiguous with no question defaulting to chat.
            reply = turn["answer"]
            if not reply.strip():
                return [_frame("error", {"detail":
                    "unusable model turn: chat needs an answer"})]
            with eng.begin() as c:
                level = _chat_level(c)
                _log_turn(c, cls, reply)
                _record_class(c, ident, level, cls, conf, cat, {})
            result = {"message_id": message_id, "reply": reply, "question": None,
                      "slice": None, "tasks": [], "created": 0,
                      "badge": {"kind": "logged", "label": "Logged"},
                      "classification": cls, "autonomy": level}
            return [_frame("result", result)]

        def _events():
            # Non-streaming turns: the reasoning model behind the Model API
            # burns most of a streaming budget thinking and yields zero
            # content deltas (see e2e/diag_empty.py). One complete block is
            # emitted as a single real token event, then the result event —
            # the SSE contract and the chat UI are unchanged.
            import time as _time
            # Plan actions (Convert/Edit/Just-keep-it buttons, or a plain
            # yes/no/convert reply to a pending offer) never call the model:
            # an explicit plan_action wins, otherwise a matching message
            # resolves the pending offer when one exists.
            msg_text = message.strip()
            action = plan_action
            if action is None:
                norm = msg_text.lower()
                if norm in _chatmod.PLAN_YES or norm in _chatmod.PLAN_NO \
                        or norm in _chatmod.PLAN_EDIT:
                    with eng.begin() as c:
                        has_pending = _pending_plan(c) is not None
                    if has_pending:
                        action = ("convert" if norm in _chatmod.PLAN_YES
                                  else "keep" if norm in _chatmod.PLAN_NO else "edit")
            if action is not None:
                for _chunk in _run_plan_action(action):
                    yield _chunk
                return
            try:
                with eng.begin() as c:
                    ident_block = _chat_identity_block(c, tenant)
            except HTTPException:
                raise
            except Exception as e:  # identity context is enhancement, never a gate
                __import__("logging").getLogger(__name__).warning(
                    "chat identity block skipped: %r", e)
                ident_block = None
            system_prompt = _chatmod.SYSTEM_PROMPT + (
                "\n" + ident_block if ident_block else "")
            turn_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message.strip()}]
            retries = 0
            turn_text = ""
            for attempt in range(1 + _chatmod.MAX_RETRIES):
                if attempt > 0:  # the previous attempt came back empty
                    retries = attempt
                    yield _frame("retrying", {"attempt": attempt,
                                              "reason": "empty model turn, retrying"})
                    _time.sleep(_chatmod.RETRY_BACKOFF_S[attempt - 1])
                try:
                    turn_text = _chatmod.complete_once(turn_messages, provider)
                except RuntimeError as e:
                    yield _frame("error", {"detail": f"model request failed: {e}"})
                    return
                if turn_text.strip():
                    yield _frame("token", {"text": turn_text})
                    break
            try:
                turn = _chatmod.parse_intake_turn(turn_text)
            except ValueError as e:
                yield _frame("error", {"detail": f"unusable model turn: {e}"})
                return
            if turn["intake"] is not None:
                for _chunk in _run_intake_turn(turn, turn_text, retries):
                    yield _chunk
                return
            # Legacy turn (no intake block): exact Step-2 behavior, plus a
            # conversation-log row.
            try:
                plan = _chatmod.parse_chat_plan(turn_text)
            except ValueError as e:
                yield _frame("error", {"detail": f"unusable model turn: {e}"})
                return
            if plan["question"] is not None:
                with eng.begin() as c:
                    _log_turn(c, "", plan["question"])
                yield _frame("result", {"message_id": message_id,
                                        "reply": plan["question"], "question": plan["question"],
                                        "slice": None, "tasks": [], "created": 0})
                return
            ident = _identity(request)
            with eng.begin() as c:
                n = _now()
                spec = plan["slice"]
                sid = c.execute(text("INSERT INTO tasks(title,intent,state,created_at,updated_at,"
                                     "created_by,tenant,project) VALUES(:t,:i,'open',:n,:n,:by,:tn,:p)"),
                                {"t": spec["title"], "i": spec["intent"], "n": n,
                                 "by": ident, "tn": tenant, "p": project}).lastrowid
                _emit(c, tenant, project, "task.created", sid,
                      {"task_id": sid, "title": spec["title"], "state": "open"})
                if spec["acceptance_criteria"]:
                    s, h = _canon({"acceptance_criteria": spec["acceptance_criteria"]})
                    c.execute(text("INSERT INTO contracts(task_id,body,sha256,created_at)"
                                   " VALUES(:i,:b,:h,:n)"), {"i": sid, "b": s, "h": h, "n": n})
                kids = []
                for t in plan["tasks"]:
                    kid = c.execute(text("INSERT INTO tasks(title,intent,state,created_at,updated_at,"
                                         "created_by,tenant,project) VALUES(:t,:i,'open',:n,:n,:by,:tn,:p)"),
                                    {"t": t["title"], "i": t["intent"], "n": n,
                                     "by": ident, "tn": tenant, "p": project}).lastrowid
                    _emit(c, tenant, project, "task.created", kid,
                          {"task_id": kid, "title": t["title"], "state": "open"})
                    # Every child carries the slice acceptance criteria as its
                    # own contract: the approve guard requires a contract per
                    # task, and chat must not leave that to the supervisor.
                    s, h = _canon({"acceptance_criteria": spec["acceptance_criteria"],
                                   "slice_id": sid, "task": t["title"]})
                    c.execute(text("INSERT INTO contracts(task_id,body,sha256,created_at)"
                                   " VALUES(:i,:b,:h,:n)"), {"i": kid, "b": s, "h": h, "n": n})
                    kids.append(kid)
                for kid in kids:
                    s, h = _canon({"slice_id": sid, "source": message_id})
                    c.execute(text("INSERT INTO evidence(task_id,kind,body,sha256,created_at)"
                                   " VALUES(:i,'slice_member',:b,:h,:n)"),
                              {"i": kid, "b": s, "h": h, "n": n})
                for tid, role in [(sid, "slice")] + [(k, "task") for k in kids]:
                    s, _ = _canon({"source": message_id, "role": role,
                                   "slice_id": sid, "model": provider["model"],
                                   "retries": retries})
                    c.execute(text("INSERT INTO receipts(task_id,idempotency_key,action,body,created_at)"
                                   " VALUES(:i,:k,'chat:create',:b,:n)"),
                              {"i": tid, "k": message_id, "b": s, "n": n})
                s, h = _canon({"source": message_id, "children": kids})
                c.execute(text("INSERT INTO evidence(task_id,kind,body,sha256,created_at)"
                               " VALUES(:i,'slice',:b,:h,:n)"),
                          {"i": sid, "b": s, "h": h, "n": n})
                slice_row = row(c, "SELECT * FROM tasks WHERE id=:i", i=sid)
                task_rows = [row(c, "SELECT * FROM tasks WHERE id=:i", i=k) for k in kids]
                cards = [_task_card(c, t) for t in task_rows]
            reply = (f"Slice #{slice_row['id']} {slice_row['title']}: "
                     f"{len(task_rows)} task(s).\n" + _chatmod.render_cards(slice_row, task_rows))
            with eng.begin() as c:
                _log_turn(c, "", reply)
            yield _frame("result", {"message_id": message_id, "reply": reply,
                                    "question": None,
                                    "slice": {"id": slice_row["id"], "title": slice_row["title"]},
                                    "tasks": cards,
                                    "created": 1 + len(task_rows)})

        return StreamingResponse(_events(), media_type="text/event-stream")

    def _plan_lookup(c, tenant, slug, project=None):
        """Latest plan entry for a slug: newest row wins (versions supersede
        by higher id). project narrows; without it the newest across the
        tenant's projects wins and its project is echoed back."""
        q = ("SELECT * FROM knowledge_entries WHERE tenant=:t AND slug=:s"
             " AND tags LIKE '%\"plan\"%'")
        kw: dict = {"t": tenant, "s": slug}
        if project:
            q += " AND project=:p"
            kw["p"] = project
        rows = [dict(r) for r in c.execute(text(q + " ORDER BY id DESC"), kw).mappings()]
        return rows[0] if rows else None

    def _plan_shape(c, entry):
        """Derived plan status + slice chain. draft = unverified, no slice;
        agreed = verified, no slice; materialized = plan:convert receipt."""
        rep = row(c, "SELECT * FROM receipts WHERE idempotency_key=:k"
                     " AND action='plan:convert'",
                  k=f"plan-convert:{entry['id']}")
        if rep is not None:
            try:
                rb = json.loads(rep["body"])
            except ValueError:
                rb = {}
            tids = [t for t in (rb.get("task_ids") or []) if isinstance(t, int)]
            chain = [row(c, "SELECT * FROM tasks WHERE id=:i", i=i) for i in tids]
            chain = [t for t in chain if t]
            cards = []
            for t in chain:
                card = _task_card(c, t)
                card["link"] = f"/workspace/tasks/{t['id']}"
                lease = row(c, "SELECT holder, expires_at FROM leases WHERE task_id=:i",
                            i=t["id"])
                live = (lease is not None and lease.get("expires_at")
                        and lease["expires_at"] > _now())
                card["worker"] = lease["holder"] if live else None
                card["lease_expires_at"] = lease["expires_at"] if live else None
                cards.append(card)
            return {"status": "materialized", "slice": {
                        "id": rep["task_id"],
                        "link": f"/workspace/tasks/{rep['task_id']}"},
                    "tasks": cards, "replayed": True}
        return {"status": "agreed" if entry.get("verified") else "draft",
                "slice": None, "tasks": [], "replayed": False}

    @app.get("/v1/plans/{slug}")
    def get_plan(slug: str, request: Request, tenant: str | None = None,
                 project: str | None = None):
        """Plan lifecycle, derived not stored: the entry plus its status
        (draft/agreed/materialized) and the slice->tasks chain with links."""
        ident = _identity(request)
        with eng.begin() as c:
            if ident == "supervisor":
                if not tenant:
                    raise HTTPException(422, "tenant is required")
                t = tenant
            else:
                t = _caller_tenant(request, c)
                if tenant is not None and tenant != t:
                    raise HTTPException(404, "tenant scope comes from the session")
            entry = _plan_lookup(c, t, slug, project)
            if entry is None:
                raise HTTPException(404, f"no plan {slug!r} in tenant {t!r}")
            if _parse_plan_body(entry) is None:
                raise HTTPException(422, "entry body is not a plan")
            shape = _plan_shape(c, entry)
            return {"tenant": entry["tenant"], "project": entry["project"],
                    "slug": slug,
                    "entry": {"id": entry["id"], "title": entry["title"],
                              "version": entry["version"],
                              "verified": bool(entry.get("verified")),
                              "author": entry.get("author"),
                              "created_at": entry.get("created_at")},
                    **shape}

    @app.post("/v1/plans/{slug}/approve")
    def approve_plan(slug: str, request: Request):
        """Approve a plan: confirm the entry, seal hidden suites (same bounded
        authoring the chat Convert path uses), then materialize one slice +
        one task per step. Idempotent on the plan:convert receipt, so
        approve-then-Convert and Convert-then-approve both replay the same
        slice. The seal model calls run BEFORE the write txn (never holding
        the SQLite write lock); leftovers record seal:authored receipts with
        ok=false and suite_sealed/seal_pending evidence either way."""
        ident = _identity(request)
        with eng.begin() as c:
            if ident == "supervisor":
                t = None
            else:
                if getattr(request.state, "session_role", None) != "owner":
                    raise HTTPException(403, "worker-only operation")
                t = _caller_tenant(request, c)
            entry = None
            if t is None:
                rows = [dict(r) for r in c.execute(
                    text("SELECT * FROM knowledge_entries WHERE slug=:s"
                         " AND tags LIKE '%\"plan\"%' ORDER BY id DESC"),
                    {"s": slug}).mappings()]
                entry = rows[0] if rows else None
            else:
                entry = _plan_lookup(c, t, slug)
            if entry is None:
                raise HTTPException(404, f"no plan {slug!r}")
            if t is not None and entry["tenant"] != t:
                raise HTTPException(404, f"no plan {slug!r} in your tenant")
            spec_d = _parse_plan_body(entry)
            if spec_d is None:
                raise HTTPException(422, "entry body is not a plan")
            rep = row(c, "SELECT * FROM receipts WHERE idempotency_key=:k"
                         " AND action='plan:convert'",
                      k=f"plan-convert:{entry['id']}")
            if rep is not None:
                shape = _plan_shape(c, entry)
                return {"tenant": entry["tenant"], "project": entry["project"],
                        "slug": slug,
                        "entry": {"id": entry["id"], "title": entry["title"],
                                  "version": entry["version"], "verified": True,
                                  "author": entry.get("author"),
                                  "created_at": entry.get("created_at")},
                        "confirmed": True, **shape}
            spec, tasks = spec_d["slice"], spec_d["tasks"]
            category = spec_d.get("category") or "feature"
            et, ep = entry["tenant"], entry["project"]
        # Seal authoring OUTSIDE any write txn: bounded batch (90 s shared),
        # same jobs the Convert path seals (slice criteria + one step each).
        seal_jobs = [("slice", list(spec["acceptance_criteria"]))]
        for st in tasks:
            step = f"Step: {st['title']}. {st.get('intent') or ''}".strip()
            seal_jobs.append((f"kid-{len(seal_jobs) - 1}", [step]))
        seal_durations: dict = {}
        try:
            seal_batch = _author_seal_batch(seal_jobs, durations=seal_durations)
        except Exception as exc:  # noqa: BLE001 — authoring is best-effort; pending is data
            seal_batch = {}
            seal_error = f"{type(exc).__name__}: {exc}"[:200]
        else:
            seal_error = ""
        with eng.begin() as c:
            rep = row(c, "SELECT * FROM receipts WHERE idempotency_key=:k"
                         " AND action='plan:convert'",
                      k=f"plan-convert:{entry['id']}")
            if rep is not None:
                shape = _plan_shape(c, entry)
                return {"tenant": entry["tenant"], "project": entry["project"],
                        "slug": slug,
                        "entry": {"id": entry["id"], "title": entry["title"],
                                  "version": entry["version"], "verified": True,
                                  "author": entry.get("author"),
                                  "created_at": entry.get("created_at")},
                        "confirmed": True, **shape}
            # Approval is a tenant-owner decision, distinct from the
            # creator-confirm rule: mark verified + emit, then materialize.
            c.execute(text("UPDATE knowledge_entries SET verified=1 WHERE id=:i"),
                      {"i": entry["id"]})
            _emit(c, entry["tenant"], entry["project"], "knowledge.confirmed", None,
                  {"id": entry["id"],
                   "citation": f"knowledge:{entry['id']}@v{entry['version']}"})
            try:
                model_name = _chatmod.provider_from_env()["model"]
            except (ValueError, RuntimeError, KeyError):
                model_name = "manual"
            pre_sealed = {
                "slice": seal_batch.get("slice"),
                "kids": [seal_batch.get(f"kid-{i}") for i in range(len(tasks))],
            }
            slice_row, kid_rows = _materialize_plan(
                c, ident, f"plan-approve:{entry['id']}", model_name,
                spec, tasks, category, slug, et, ep,
                pre_sealed=pre_sealed if any(
                    [pre_sealed["slice"]] + pre_sealed["kids"]) else None)
            seals: dict = {}
            jobs = ([("slice", slice_row["id"], pre_sealed["slice"])]
                    + [(f"kid-{i}", kr["id"], pre_sealed["kids"][i])
                       for i, kr in enumerate(kid_rows)])
            for job_key, tid, piece in jobs:
                if piece is None:
                    ok, payload = False, seal_error or "seal authoring unavailable"
                else:
                    (sealed, _criteria) = piece
                    ok, payload = sealed
                # _materialize_plan already wrote the suite_sealed /
                # seal_pending evidence row for authored pieces.
                duration_s = seal_durations.get(job_key, 0.0)
                s, h = _canon({"ok": ok, "duration_s": duration_s,
                               "files": payload if ok else None,
                               "reason": None if ok else str(payload),
                               "job": job_key, "plan_slug": slug})
                c.execute(text("INSERT INTO receipts(task_id,idempotency_key,action,body,created_at)"
                               " VALUES(:i,:k,'seal:authored',:b,:n)"),
                          {"i": tid, "k": f"plan-approve-seal:{entry['id']}:{tid}",
                           "b": s, "n": _now()})
                seals[str(tid)] = {"ok": ok, "duration_s": duration_s}
            # Chain convention matches chat Convert: task_ids holds the kids
            # only (plus an intake head when one exists); the slice travels
            # as slice_id / the receipt's own task_id.
            all_ids = [t["id"] for t in kid_rows]
            s, _ = _canon({"plan_entry": entry["id"], "plan_slug": slug,
                           "message_id": f"plan-approve:{entry['id']}",
                           "slice_id": slice_row["id"], "task_ids": all_ids})
            c.execute(text("INSERT INTO receipts(task_id,idempotency_key,action,body,created_at)"
                           " VALUES(:i,:k,'plan:convert',:b,:n)"),
                      {"i": slice_row["id"], "k": f"plan-convert:{entry['id']}",
                       "b": s, "n": _now()})
            shape = _plan_shape(c, dict(entry, verified=1))
            return {"tenant": et, "project": ep, "slug": slug,
                    "entry": {"id": entry["id"], "title": entry["title"],
                              "version": entry["version"], "verified": True,
                              "author": entry.get("author"),
                              "created_at": entry.get("created_at")},
                    "confirmed": True, "seals": seals, **shape}

    @app.post("/v1/plans/{slug}/execute")
    def execute_plan(slug: str, body: dict, request: Request):
        """Dispatch a materialized plan: 422 unless the plan:convert receipt
        exists. Chain tasks are already claimable when open; an optional
        ``worker`` principal assigns each unassigned open chain task
        server-side with the same conflict rules as the claim endpoint
        (idempotent per plan entry + task, contested tasks reported, never
        forced). Always emits plan.execute so the dispatch itself is
        observable; worker presence comes from live leases on the chain."""
        ident = _identity(request)
        worker = body.get("worker")
        if worker is not None and (not isinstance(worker, str) or not worker.strip()):
            raise HTTPException(422, "worker must be a non-empty string")
        worker = worker.strip() if isinstance(worker, str) else None
        ttl = _ttl_field(body)
        with eng.begin() as c:
            if ident == "supervisor":
                t = None
            else:
                if getattr(request.state, "session_role", None) != "owner":
                    raise HTTPException(403, "worker-only operation")
                t = _caller_tenant(request, c)
            entry = None
            if t is None:
                rows = [dict(r) for r in c.execute(
                    text("SELECT * FROM knowledge_entries WHERE slug=:s"
                         " AND tags LIKE '%\"plan\"%' ORDER BY id DESC"),
                    {"s": slug}).mappings()]
                entry = rows[0] if rows else None
            else:
                entry = _plan_lookup(c, t, slug)
            if entry is None:
                raise HTTPException(404, f"no plan {slug!r}")
            if t is not None and entry["tenant"] != t:
                raise HTTPException(404, f"no plan {slug!r} in your tenant")
            if _parse_plan_body(entry) is None:
                raise HTTPException(422, "entry body is not a plan")
            rep = row(c, "SELECT * FROM receipts WHERE idempotency_key=:k"
                         " AND action='plan:convert'",
                      k=f"plan-convert:{entry['id']}")
            if rep is None:
                raise HTTPException(422, "plan is not materialized — approve first")
            try:
                rb = json.loads(rep["body"])
            except ValueError:
                rb = {}
            tids = [x for x in (rb.get("task_ids") or []) if isinstance(x, int)]
            et, ep = entry["tenant"], entry["project"]
            assigned, contested, open_ids = [], [], []
            for tid in tids:
                tr = row(c, "SELECT * FROM tasks WHERE id=:i", i=tid)
                if not tr or tr["state"] not in ("open", "needs_review", "claimed"):
                    continue
                open_ids.append(tid)
                if worker is None:
                    continue
                key = f"plan-execute:{entry['id']}:{worker}:{tid}"
                if row(c, "SELECT id FROM receipts WHERE task_id=:i AND idempotency_key=:k",
                       i=tid, k=key):
                    assigned.append(tid)
                    continue
                active = row(c, "SELECT * FROM assignments WHERE task_id=:i"
                                " AND status='active' ORDER BY id DESC LIMIT 1", i=tid)
                if active and active.get("principal") != worker:
                    contested.append(tid)
                    continue
                lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
                if (lease and lease.get("expires_at") and lease["expires_at"] > _now()
                        and lease.get("holder") != worker):
                    contested.append(tid)
                    continue
                epoch = (lease["lease_epoch"] + 1) if lease else 1
                if active:
                    c.execute(text("UPDATE assignments SET principal=:p WHERE id=:a"),
                              {"p": worker, "a": active["id"]})
                    aid = active["id"]
                else:
                    aid = c.execute(text("INSERT INTO assignments(task_id,principal,attempt_no,"
                                         "created_at) VALUES(:i,:p,1,:t)"),
                                    {"i": tid, "p": worker, "t": _now()}).lastrowid
                c.execute(text("INSERT INTO leases(task_id,holder,lease_epoch,expires_at,"
                               "updated_at) VALUES(:i,:h,:e,:x,:t) "
                               "ON CONFLICT(task_id) DO UPDATE SET holder=:h,lease_epoch=:e,"
                               "expires_at=:x,updated_at=:t"),
                          {"i": tid, "h": worker, "e": epoch,
                           "x": _expiry(ttl), "t": _now()})
                c.execute(text("UPDATE tasks SET state='claimed', updated_at=:t WHERE id=:i"),
                          {"i": tid, "t": _now()})
                _emit(c, et, ep, "task.transition", tid,
                      {"task_id": tid, "from": tr["state"], "to": "claimed",
                       "owner": worker})
                payload = {"assignment_id": aid, "principal": worker,
                           "lease_epoch": epoch}
                c.execute(text("INSERT INTO receipts(task_id,idempotency_key,action,body,"
                               "created_at) VALUES(:i,:k,'plan.execute.claim',:b,:n)"),
                          {"i": tid, "k": key, "b": json.dumps(payload), "n": _now()})
                assigned.append(tid)
            eid = _emit(c, et, ep, "plan.execute", rep["task_id"],
                        {"slug": slug, "plan_entry": entry["id"],
                         "slice_id": rep["task_id"], "task_ids": open_ids,
                         "worker": worker, "assigned": assigned,
                         "contested": contested})
            shape = _plan_shape(c, entry)
            return {"tenant": et, "project": ep, "slug": slug,
                    "entry": {"id": entry["id"], "title": entry["title"],
                              "version": entry["version"],
                              "verified": bool(entry.get("verified")),
                              "author": entry.get("author"),
                              "created_at": entry.get("created_at")},
                    "execute_event": eid, "worker": worker,
                    "dispatched": open_ids, "assigned": assigned,
                    "contested": contested, **shape}

    @app.get("/v1/chat/autonomy")
    def get_chat_autonomy(request: Request, project: str, tenant: str | None = None):
        """Autonomy level for the plan confirm gate (session/user setting;
        default ask-for-everything when no row)."""
        ident = _identity(request)
        with eng.begin() as c:
            scope = {"project": project}
            if tenant is not None:
                scope["tenant"] = tenant
            t, p = _session_scope(request, c, scope)
            _registered(c, t, p)
            r = row(c, "SELECT level FROM chat_autonomy WHERE tenant=:t AND project=:p",
                    t=t, p=p)
            level = r["level"] if r and r.get("level") in _chatmod.AUTONOMY_LEVELS \
                else _chatmod.DEFAULT_AUTONOMY
            return {"tenant": t, "project": p, "autonomy": level,
                    "default": r is None, "user": ident}

    @app.post("/v1/chat/autonomy")
    def set_chat_autonomy(body: dict, request: Request):
        """Record the session/user autonomy level for the scope."""
        level = body.get("autonomy", body.get("level"))
        if level not in _chatmod.AUTONOMY_LEVELS:
            raise HTTPException(422, f"autonomy must be one of {list(_chatmod.AUTONOMY_LEVELS)}")
        ident = _identity(request)
        with eng.begin() as c:
            t, p = _session_scope(request, c, body)
            _registered(c, t, p)
            c.execute(text("INSERT INTO chat_autonomy(tenant,project,level,updated_by,updated_at)"
                           " VALUES(:t,:p,:l,:b,:n)"
                           " ON CONFLICT(tenant,project) DO UPDATE SET level=:l,"
                           " updated_by=:b, updated_at=:n"),
                      {"t": t, "p": p, "l": level, "b": ident, "n": _now()})
            return {"tenant": t, "project": p, "autonomy": level, "updated_by": ident}

    @app.post("/v1/chat/reclassify")
    def reclassify_turn(body: dict, request: Request):
        """Reclassify a turn: appends a new classification row pointing at
        the prior one (reclass_of). History is never rewritten or deleted."""
        msg_id = body.get("message_id", body.get("turn_id"))
        if not isinstance(msg_id, str) or not msg_id.strip():
            raise HTTPException(422, "message_id is required")
        cls = body.get("class")
        if cls not in _chatmod.INTAKE_CLASSES:
            raise HTTPException(422, f"class must be one of {list(_chatmod.INTAKE_CLASSES)}")
        try:
            conf = float(body.get("confidence", 0.5))
        except (TypeError, ValueError):
            raise HTTPException(422, "confidence must be a number")
        conf = max(0.0, min(1.0, conf))
        cat = body.get("category")
        if cat is not None and cat not in _chatmod.TASK_CATEGORIES:
            raise HTTPException(422, f"category must be one of {list(_chatmod.TASK_CATEGORIES)}")
        ident = _identity(request)
        with eng.begin() as c:
            t, p = _session_scope(request, c, body)
            _registered(c, t, p)
            prior = row(c, "SELECT * FROM intake_classifications"
                           " WHERE tenant=:t AND project=:p AND turn_id=:u"
                           " ORDER BY id DESC LIMIT 1", t=t, p=p, u=msg_id.strip())
            if prior is None:
                raise HTTPException(404, "no classification for that turn")
            rid = c.execute(text("INSERT INTO intake_classifications(tenant,project,turn_id,class,"
                                 "confidence,category,created_ids,autonomy_level,decided_by,"
                                 "decided_at,reclass_of,created_at)"
                                 " VALUES(:t,:p,:u,:c,:f,:g,:d,:l,:b,:a,:r,:n)"),
                            {"t": t, "p": p, "u": msg_id.strip(), "c": cls, "f": conf,
                             "g": cat or "", "d": prior["created_ids"],
                             "l": prior["autonomy_level"], "b": ident, "a": _now(),
                             "r": prior["id"], "n": _now()}).lastrowid
            out = row(c, "SELECT * FROM intake_classifications WHERE id=:i", i=rid)
            try:
                out["created_ids"] = json.loads(out.get("created_ids") or "{}")
            except ValueError:
                out["created_ids"] = {}
            return out

    @app.get("/v1/chat/turns")
    def list_chat_turns(request: Request, project: str, tenant: str | None = None,
                         message_id: str | None = None):
        """Append-only conversation log for the scope (never hard-deleted)."""
        with eng.begin() as c:
            scope = {"project": project}
            if tenant is not None:
                scope["tenant"] = tenant
            t, p = _session_scope(request, c, scope)
            _registered(c, t, p)
            q = "SELECT * FROM chat_turns WHERE tenant=:t AND project=:p"
            kw: dict = {"t": t, "p": p}
            if message_id:
                q += " AND message_id=:m"
                kw["m"] = message_id
            items = [dict(r) for r in c.execute(text(q + " ORDER BY id"), kw).mappings()]
            return {"items": items}

    @app.get("/v1/chat/classifications")
    def list_classifications(request: Request, project: str, tenant: str | None = None,
                             message_id: str | None = None):
        """Classification rows for the scope, oldest first, with created_ids
        parsed. Reclassifications appear as additional rows (reclass_of)."""
        with eng.begin() as c:
            scope = {"project": project}
            if tenant is not None:
                scope["tenant"] = tenant
            t, p = _session_scope(request, c, scope)
            _registered(c, t, p)
            q = "SELECT * FROM intake_classifications WHERE tenant=:t AND project=:p"
            kw: dict = {"t": t, "p": p}
            if message_id:
                q += " AND turn_id=:m"
                kw["m"] = message_id
            items = []
            for r in c.execute(text(q + " ORDER BY id"), kw).mappings():
                d = dict(r)
                try:
                    d["created_ids"] = json.loads(d.get("created_ids") or "{}")
                except ValueError:
                    d["created_ids"] = {}
                items.append(d)
            return {"items": items}

    # States where a task list row must carry WHY it needs attention, and the
    # evidence kinds that explain it. Derived per row from the existing
    # evidence table (no schema migration): the latest escalation/review
    # evidence body, truncated to one short string.
    _ATTENTION_STATES = {"failed", "blocked", "needs_review"}
    _ATTENTION_KINDS = ("escalation", "reviewer", "review", "review_note")

    def _fence_text(fence: object) -> tuple[str | None, str | None]:
        """Split an evidence fence like 'escalate:attempt-3:rework_exhausted'.

        Returns (code, attempt): human-readable code plus the attempt number
        when present, both None when the fence carries neither."""
        if not isinstance(fence, str) or not fence.strip():
            return None, None
        parts = fence.strip().split(":")
        code = parts[-1].replace("_", " ").strip() or None
        attempt = None
        for part in parts:
            m = re.match(r"attempt-(\d+)$", part.strip())
            if m:
                attempt = m.group(1)
                break
        return code, attempt

    def _attention_reason(raw: str | None) -> str | None:
        if raw is None:
            return None
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = raw
        text = None
        attempt = None
        if isinstance(parsed, dict):
            payload = parsed.get("payload")
            if isinstance(payload, dict):
                for key in ("reason", "note", "message", "text", "verdict"):
                    val = payload.get(key)
                    if isinstance(val, str) and val.strip():
                        text = val.strip()
                        break
            if text is None:
                for key in ("reason", "note", "message", "text", "verdict"):
                    val = parsed.get(key)
                    if isinstance(val, str) and val.strip():
                        text = val.strip()
                        break
            code, attempt = _fence_text(parsed.get("fence"))
            if text is None:
                text = code
        else:
            text = parsed if isinstance(parsed, str) else json.dumps(parsed)
            text = text.strip() if isinstance(text, str) else text
        if not text:
            return None
        text = text.replace("_", " ")
        if attempt and attempt not in text:
            text = f"{text} (attempt {attempt})"
        return text[:200] if len(text) > 200 else text

    # ---- P1 pipeline strip: five steps computed ONLY from real ledger rows.
    # Mapping (the web PipelineStrip renders these flags verbatim):
    #   contract : done when >= 1 row in contracts (acceptance_criteria live
    #     here). Otherwise pending.
    #   knowledge: done when a contract body carries plan_slug/category, or a
    #     chat:create / plan:convert receipt body does (intake plan linkage).
    #     Direct POST /v1/tasks rows have neither -> pending (honest).
    #   build    : done when worker output exists — evidence kind in
    #     step/tests/test_output/criteria, or a row in results (attempt
    #     verdict). attention when the latest result verdict is negative
    #     (that blocks the approve gate). Otherwise pending.
    #   review   : done when reviewer-kind evidence (reviewer/review/review_note)
    #     exists (latest verdict non-negative), or the task is done (approval
    #     requires a passing verify on needs_review). attention when the latest
    #     reviewer verdict is negative. Otherwise pending.
    #   evidence : done when >= 1 evidence row exists (the approve gate counts
    #     rows, not kinds). A count cannot fail, so no attention state.
    # There is no pr_record/head-SHA evidence kind in this ledger; build and
    # review are derived from the kinds above, which is what intake, workers,
    # and the verify gate actually write.
    _PIPELINE_BUILD_KINDS = frozenset({"step", "tests", "test_output", "criteria"})
    _PIPELINE_REVIEW_KINDS = ("reviewer", "review", "review_note")
    _PIPELINE_LINK_ACTIONS = frozenset({"chat:create", "plan:convert"})
    _PIPELINE_NEGATIVE = frozenset({"fail", "failed", "failure",
                                    "request_changes", "changes_requested",
                                    "reject", "rejected", "error"})

    def _pipeline_json(raw) -> dict:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except ValueError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    def _pipeline_verdict(body: dict):
        v = body.get("verdict")
        if not isinstance(v, str):
            payload = body.get("payload")
            if isinstance(payload, dict):
                v = payload.get("verdict")
        if not isinstance(v, str) or not v.strip():
            return None
        return v.strip().lower()

    def _seal_paths() -> tuple[Path, Path | None]:
        """Hidden-suite + exec-spec roots (env-overridable). Same-host
        assumption is documented: the worker host reads the same roots."""
        hidden = Path(os.environ.get("HIDDEN_TESTS_ROOT", "/srv/rouvr/hidden-tests"))
        spec = os.environ.get("SPEC_ROOT", "/srv/rouvr/specs")
        return hidden, (Path(spec) if spec else None)

    def _author_seal_batch(jobs: list[tuple[str, list[str]]],
                           budget_s: float = 90.0,
                           durations: dict | None = None,
                           ) -> dict[str, tuple[tuple[bool, object], list[str]]]:
        """Author hidden suites for Convert-created tasks, sequentially inside
        one overall budget (sync Convert path; leftovers fall back to async
        hold + visible badge). No DB touched — call BEFORE opening a write
        txn so a 15-60 s model call never holds a SQLite write lock. Returns
        {key: ((ok, files|error), criteria)}; when durations is given it is
        filled with {key: seconds} per job."""
        from rouvr.mcp_server import _author_hidden_suite, _SUITE_TIMEOUT
        out: dict[str, tuple[tuple[bool, object], list[str]]] = {}
        deadline = time.monotonic() + budget_s
        for key, criteria in jobs:
            start = time.monotonic()
            remaining = deadline - start
            if remaining <= 1:
                out[key] = ((False, f"seal budget exhausted ({budget_s:g}s shared across tasks)"),
                            criteria)
                if durations is not None:
                    durations[key] = round(time.monotonic() - start, 2)
                continue
            try:
                files = _author_hidden_suite(criteria, timeout=min(_SUITE_TIMEOUT, remaining))
                out[key] = ((True, files), criteria)
            except Exception as e:  # noqa: BLE001 — seal failure is data, never fatal
                out[key] = ((False, f"{type(e).__name__}: {e}"[:300]), criteria)
            if durations is not None:
                durations[key] = round(time.monotonic() - start, 2)
        return out

    def _seal_one_task(c, tid: int, sealed: tuple[bool, object], criteria: list[str]) -> None:
        """Write one task's pre-authored suite (or record why it is pending).
        Disk write is fast; the slow model call happened before the txn."""
        ok, payload = sealed
        if ok:
            try:
                from rouvr.hiddentests import write_hidden_suite
                hidden, _ = _seal_paths()
                manifest = write_hidden_suite(hidden, task_id=str(tid), files=payload,
                                              created_by="test_author")
                s, h = _canon({"files": sorted(payload), "tree": manifest.tree_sha256,
                               "sealed_by": "test_author"})
                kind = "suite_sealed"
            except Exception as e:  # noqa: BLE001 — disk failure also falls back to hold
                s, h = _canon({"reason": f"disk: {type(e).__name__}: {e}"[:300],
                               "criteria": criteria})
                kind = "seal_pending"
        else:
            s, h = _canon({"reason": payload, "criteria": criteria})
            kind = "seal_pending"
        c.execute(text("INSERT INTO evidence(task_id,kind,body,sha256,created_at)"
                       " VALUES(:i,:k,:b,:h,:n)"),
                  {"i": tid, "k": kind, "b": s, "h": h, "n": _now()})

    def _sealing(evidence) -> bool:
        """Visible async-hold badge: suite pending and nothing sealed since."""
        kinds = {e.get("kind") for e in (evidence or [])}
        return "seal_pending" in kinds and "suite_sealed" not in kinds

    def _suite_on_disk(tid: int) -> bool:
        from rouvr.hiddentests import MANIFEST
        hidden, _ = _seal_paths()
        return (hidden / str(tid) / MANIFEST).exists()

    def _needs_exec_spec(tenant: str | None, project: str | None) -> bool:
        """True when the worker host has no exec spec for this project (the
        E_CONTRACT fail-closed the pipeline hits at attempt time). Creating
        specs picks the coder's repo — a supervisor trust decision, so Convert
        flags it instead of auto-creating. Unknown (no spec root) reads False."""
        _, root = _seal_paths()
        t, p = str(tenant or ""), str(project or "")
        if root is None or not root.is_dir():
            return False
        if not t or not p or "/" in t or "/" in p or ".." in t or ".." in p:
            return False
        return not (root / t / f"{p}.yaml").exists()

    def _pipeline_flags(state, contracts, evidence, receipts, result_verdicts) -> dict:
        flags = {"contract": "pending", "knowledge": "pending",
                 "build": "pending", "review": "pending",
                 "evidence": "pending"}
        contracts = list(contracts or [])
        evidence = list(evidence or [])
        receipts = list(receipts or [])
        if contracts:
            flags["contract"] = "done"
        for c in contracts:
            b = _pipeline_json(c.get("body"))
            if b.get("plan_slug") or b.get("category"):
                flags["knowledge"] = "done"
                break
        if flags["knowledge"] != "done":
            for r in receipts:
                if r.get("action") not in _PIPELINE_LINK_ACTIONS:
                    continue
                b = _pipeline_json(r.get("body"))
                if b.get("plan_slug") or b.get("category"):
                    flags["knowledge"] = "done"
                    break
        late = [str(v).strip().lower() for v in (result_verdicts or [])]
        if any(e.get("kind") in _PIPELINE_BUILD_KINDS for e in evidence) or late:
            flags["build"] = "done"
        if late and late[-1] in _PIPELINE_NEGATIVE:
            flags["build"] = "attention"
        reviewer_verdicts = [_pipeline_verdict(_pipeline_json(e.get("body")))
                             for e in evidence
                             if e.get("kind") in _PIPELINE_REVIEW_KINDS]
        reviewer_verdicts = [v for v in reviewer_verdicts if v is not None]
        if reviewer_verdicts:
            flags["review"] = ("attention"
                               if reviewer_verdicts[-1] in _PIPELINE_NEGATIVE
                               else "done")
        elif state == "done":
            # Approval requires a passing verify on needs_review: a done task
            # was reviewed, even if the reviewer note itself is gone.
            flags["review"] = "done"
        if evidence:
            flags["evidence"] = "done"
        return flags

    def _attach_pipeline(c, items) -> None:
        """Attach the pipeline object to list rows (batched, no N+1)."""
        ids = [d["id"] for d in items if "id" in d]
        blank = {i: {"contracts": [], "evidence": [],
                      "receipts": [], "verdicts": []} for i in ids}
        for n in range(0, len(ids), 500):
            chunk = ids[n:n + 500]
            ph = ",".join(f":p{j}" for j in range(len(chunk)))
            kw = {f"p{j}": v for j, v in enumerate(chunk)}
            for r in c.execute(text(
                    f"SELECT task_id, body FROM contracts WHERE task_id IN ({ph})"),
                    kw).mappings():
                blank[r["task_id"]]["contracts"].append(r)
            for r in c.execute(text(
                    "SELECT task_id, kind, substr(body,1,400) AS body"
                    f" FROM evidence WHERE task_id IN ({ph})"), kw).mappings():
                blank[r["task_id"]]["evidence"].append(r)
            for r in c.execute(text(
                    "SELECT task_id, action, substr(body,1,400) AS body"
                    f" FROM receipts WHERE task_id IN ({ph})"
                    " AND action IN ('chat:create','plan:convert')"),
                    kw).mappings():
                blank[r["task_id"]]["receipts"].append(r)
            for r in c.execute(text(
                    f"SELECT task_id, verdict FROM results WHERE task_id IN ({ph})"
                    " ORDER BY id"), kw).mappings():
                blank[r["task_id"]]["verdicts"].append(r["verdict"])
        for d in items:
            b = blank.get(d.get("id"), {"contracts": [], "evidence": [],
                                        "receipts": [], "verdicts": []})
            d["pipeline"] = _pipeline_flags(d.get("state"), b["contracts"],
                                            b["evidence"], b["receipts"],
                                            b["verdicts"])
            d["sealing"] = _sealing(b["evidence"])
            d["needs_spec"] = _needs_exec_spec(d.get("tenant"), d.get("project"))

    @app.get("/v1/tasks")
    def list_tasks(request: Request, state: str | None = None, tenant: str | None = None,
                   limit: int | None = None, offset: int = 0):
        ident = _identity(request)
        with eng.begin() as c:
            mine = _caller_tenant(request, c)
            want = mine if mine is not None else tenant  # S022: users see their own tenant
            kinds = ",".join(f"'{k}'" for k in _ATTENTION_KINDS)
            q, kw = ("SELECT tasks.*, (SELECT e.body FROM evidence e WHERE e.task_id=tasks.id"
                     f" AND e.kind IN ({kinds}) ORDER BY e.id DESC LIMIT 1) AS _attention_body"
                     " FROM tasks"), {}
            conds = []
            if state:
                conds.append("state=:s"); kw["s"] = state
            if want:
                conds.append("tenant=:t"); kw["t"] = want
            if conds:
                q += " WHERE " + " AND ".join(conds)
            if limit is not None:
                if limit < 1 or limit > 500:
                    raise HTTPException(422, "limit must be 1..500")
                if offset < 0:
                    raise HTTPException(422, "offset must be >= 0")
            count_q = "SELECT COUNT(*) FROM tasks"
            if conds:
                count_q += " WHERE " + " AND ".join(conds)
            total = c.execute(text(count_q), {k: v for k, v in kw.items() if k in ("s", "t")}).scalar()
            page_q = q + " ORDER BY id"
            if limit is not None:
                page_q += " LIMIT :l OFFSET :o"
                kw["l"], kw["o"] = limit, offset
            items = []
            for r in c.execute(text(page_q), kw).mappings():
                d = dict(r)
                raw = d.pop("_attention_body", None)
                d["attention"] = (_attention_reason(raw)
                                  if d.get("state") in _ATTENTION_STATES else None)
                items.append(d)
            _attach_pipeline(c, items)
            out: dict = {"items": items}
            if limit is not None:
                out["total"] = total
                out["offset"] = offset
            return out

    @app.get("/v1/tasks/{tid}")
    def get_task(tid: int, request: Request):
        with eng.begin() as c:
            t = _own_or_stack(c, tid, request)
            t["contracts"] = [dict(r) for r in c.execute(text("SELECT * FROM contracts WHERE task_id=:i ORDER BY id"), {"i": tid}).mappings()]
            t["evidence"] = [dict(r) for r in c.execute(text("SELECT * FROM evidence WHERE task_id=:i ORDER BY id"), {"i": tid}).mappings()]
            kinds = ",".join(f"'{k}'" for k in _ATTENTION_KINDS)
            raw = c.execute(text("SELECT e.body FROM evidence e WHERE e.task_id=:i"
                                 f" AND e.kind IN ({kinds}) ORDER BY e.id DESC LIMIT 1"),
                            {"i": tid}).scalar()
            t["attention"] = (_attention_reason(raw)
                              if t.get("state") in _ATTENTION_STATES else None)
            t["receipts"] = [dict(r) for r in c.execute(text("SELECT * FROM receipts WHERE task_id=:i ORDER BY id"), {"i": tid}).mappings()]
            verdicts = [r["verdict"] for r in c.execute(
                text("SELECT verdict FROM results WHERE task_id=:i ORDER BY id"),
                {"i": tid}).mappings()]
            t["pipeline"] = _pipeline_flags(t.get("state"), t["contracts"],
                                            t["evidence"], t["receipts"],
                                            verdicts)
            t["sealing"] = _sealing(t["evidence"])
            t["needs_spec"] = _needs_exec_spec(t.get("tenant"), t.get("project"))
            return t

    @app.post("/v1/tasks/{tid}/contracts")
    def add_contract(tid: int, body: dict, request: Request):
        s, h = _canon(body)
        with eng.begin() as c:
            _own_or_stack(c, tid, request)
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
            t = _own_or_stack(c, tid, request)
            kind = body.get("kind", "note")
            dup = row(c, "SELECT * FROM evidence WHERE task_id=:i AND kind=:k AND sha256=:h",
                      i=tid, k=kind, h=h)
            if dup:
                return {**dict(dup), "replayed": True}
            rid = c.execute(text("INSERT INTO evidence(task_id,kind,body,sha256,created_at) VALUES(:i,:k,:b,:h,:n)"),
                            {"i": tid, "k": kind, "b": s, "h": h, "n": _now()}).lastrowid
            parsed = json.loads(s)
            payload = {"evidence_id": rid, "task_id": tid}
            if isinstance(parsed, dict):
                payload.update(parsed)  # worker-provided progress, forwarded verbatim
            else:
                payload["body"] = parsed
            etype = EVIDENCE_EVENT_KINDS.get(kind, "task.step")
            if etype == "task.evidence":
                payload["kind"] = kind
            _emit(c, t["tenant"], t["project"], etype, tid, payload)
            return row(c, "SELECT * FROM evidence WHERE id=:i", i=rid)

    @app.post("/v1/tasks/{tid}/transition")
    def transition(tid: int, body: dict, request: Request):
        _transition_allowed(request)
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
            t = _own_or_stack(c, tid, request)
            if to not in TRANSITIONS.get(t["state"], set()):
                raise HTTPException(409, f"illegal transition {t['state']} -> {to}")
            if to == "done":
                nc = row(c, "SELECT COUNT(*) AS n FROM contracts WHERE task_id=:i", i=tid)["n"]
                ne = row(c, "SELECT COUNT(*) AS n FROM evidence WHERE task_id=:i", i=tid)["n"]
                if not nc or not ne:
                    raise HTTPException(409, "done requires a contract and evidence")
                if _identity(request) != "supervisor":
                    # Owner approve: no skipping verification. The demo
                    # must show a reviewed, verified head — never a jump.
                    if t["state"] != "needs_review":
                        raise HTTPException(409, f"approve requires needs_review (task is {t['state']})")
                    head = row(c, "SELECT verdict FROM results WHERE task_id=:i ORDER BY id DESC LIMIT 1", i=tid)
                    if not head or head["verdict"] != "pass":
                        raise HTTPException(409, "approve requires a passing verify on the current head")
            c.execute(text("UPDATE tasks SET state=:s, owner=COALESCE(:o, owner), updated_at=:n WHERE id=:i"),
                      {"s": to, "o": body.get("owner"), "n": _now(), "i": tid})
            s, _ = _canon({"from": t["state"], "to": to, "note": body.get("note")})
            c.execute(text("INSERT INTO receipts(task_id,idempotency_key,action,body,created_at) VALUES(:i,:k,:a,:b,:n)"),
                      {"i": tid, "k": key, "a": f"transition:{to}", "b": s, "n": _now()})
            _emit(c, t["tenant"], t["project"], "task.transition", tid,
                  {"task_id": tid, "from": t["state"], "to": to, "owner": body.get("owner")})
            return {"replayed": False, "receipt": row(c, "SELECT * FROM receipts WHERE task_id=:i AND idempotency_key=:k", i=tid, k=key)}

    @app.post("/v1/tasks/{tid}/seal")
    def seal_task(tid: int, body: dict, request: Request):
        """(Re-)seal the hidden acceptance suite for one task. Idempotent:
        a suite already sealed (evidence or disk) converges without spending
        another model call. Seal failures return sealed:false with the reason
        instead of raising — the task stays valid, the async hold persists."""
        _transition_allowed(request)
        with eng.begin() as c:
            t = _own_or_stack(c, tid, request)
        criteria: list[str] = []
        with eng.begin() as c:
            pend = [dict(r) for r in c.execute(
                text("SELECT body FROM evidence WHERE task_id=:i AND kind='seal_pending' ORDER BY id DESC LIMIT 1"),
                {"i": tid}).mappings()]
            if pend:
                try:
                    criteria = [x for x in (json.loads(pend[0]["body"]).get("criteria") or [])
                                if isinstance(x, str) and x.strip()]
                except ValueError:
                    criteria = []
            if not criteria:
                for r in c.execute(
                        text("SELECT body FROM contracts WHERE task_id=:i ORDER BY id LIMIT 1"),
                        {"i": tid}).mappings():
                    try:
                        ac = json.loads(r["body"]).get("acceptance_criteria") or []
                    except ValueError:
                        ac = []
                    criteria = [x for x in ac if isinstance(x, str) and x.strip()]
                    if criteria:
                        break
            if not criteria:
                raise HTTPException(422, "task has no acceptance criteria to seal")
            already = [dict(r) for r in c.execute(
                text("SELECT body FROM evidence WHERE task_id=:i AND kind='suite_sealed' ORDER BY id DESC LIMIT 1"),
                {"i": tid}).mappings()]
            on_disk = _suite_on_disk(tid)
            if already or on_disk:
                if not already:
                    s, h = _canon({"files": [], "sealed_by": "test_author",
                                   "converged": "suite found on disk"})
                    c.execute(text("INSERT INTO evidence(task_id,kind,body,sha256,created_at)"
                                   " VALUES(:i,'suite_sealed',:b,:h,:n)"),
                              {"i": tid, "b": s, "h": h, "n": _now()})
                return {"sealed": True, "replayed": True, "task_id": tid}
        sealed = _author_seal_batch([(str(tid), criteria)], budget_s=90.0).get(str(tid))
        with eng.begin() as c:
            _own_or_stack(c, tid, request)
            _seal_one_task(c, tid, sealed[0], sealed[1])
            return {"sealed": sealed[0][0],
                    "reason": None if sealed[0][0] else sealed[0][1],
                    "task_id": tid}

    @app.patch("/v1/tasks/{tid}")
    def edit_task(tid: int, body: dict, request: Request):
        """Edit title/intent of an unclaimed open task, optionally moving it
        to another project in the same tenant. Requires idempotency_key;
        replays return the first receipt. Claimed/leased or non-open tasks
        refuse with 409 — editing live work would fork the worker's context.
        Moves refuse with 422 when the task is a slice or slice member
        (the chain lives in one project) or the target is unregistered;
        cross-tenant moves are never allowed."""
        key = body.get("idempotency_key")
        if not key:
            raise HTTPException(422, "idempotency_key is required")
        title = body.get("title")
        intent = body.get("intent")
        dest = body.get("project")
        if title is not None and not str(title).strip():
            raise HTTPException(422, "title must not be blank")
        if intent is not None and not str(intent).strip():
            raise HTTPException(422, "intent must not be blank")
        if dest is not None and not str(dest).strip():
            raise HTTPException(422, "project must not be blank")
        if title is None and intent is None and dest is None:
            raise HTTPException(422, "nothing to edit: pass title, intent and/or project")
        with eng.begin() as c:
            prior = row(c, "SELECT * FROM receipts WHERE task_id=:i AND idempotency_key=:k", i=tid, k=key)
            if prior:
                if prior.get("action") != "edit":
                    raise HTTPException(409, f"idempotency key already used for {prior.get('action')}")
                return {"replayed": True, "receipt": prior}
            t = _own_or_stack(c, tid, request)
            if t["state"] != "open":
                raise HTTPException(409, f"only open tasks are editable (state is {t['state']})")
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            if lease and lease.get("expires_at") and lease["expires_at"] > _now():
                raise HTTPException(409, f"task is claimed by {lease['holder']}")
            new_project = str(dest).strip() if dest is not None else t["project"]
            if new_project != t["project"]:
                if not row(c, "SELECT * FROM projects WHERE tenant=:t AND project=:p",
                           t=t["tenant"], p=new_project):
                    raise HTTPException(404, f"project {new_project!r} not registered")
                linked = row(c, "SELECT id FROM evidence WHERE task_id=:i"
                                " AND kind IN ('slice','slice_member') LIMIT 1", i=tid)
                if linked:
                    raise HTTPException(422, "slice tasks stay in their slice's project")
            updates, kw = [], {"i": tid, "n": _now()}
            if title is not None:
                updates.append("title=:t")
                kw["t"] = str(title).strip()
            if intent is not None:
                updates.append("intent=:d")
                kw["d"] = str(intent).strip()
            if new_project != t["project"]:
                updates.append("project=:p")
                kw["p"] = new_project
            updates.append("updated_at=:n")
            c.execute(text(f"UPDATE tasks SET {', '.join(updates)} WHERE id=:i"), kw)
            s, h = _canon({"title": kw.get("t", t["title"]), "intent": kw.get("d", t.get("intent")),
                           "project": kw.get("p", t["project"])})
            c.execute(text("INSERT INTO receipts(task_id,idempotency_key,action,body,created_at)"
                           " VALUES(:i,:k,'edit',:b,:n)"),
                      {"i": tid, "k": key, "b": s, "n": _now()})
            return {"replayed": False, "task_id": tid,
                    "title": kw.get("t", t["title"]), "intent": kw.get("d", t.get("intent")),
                    "project": kw.get("p", t["project"])}

    @app.post("/v1/tasks/{tid}/retire")
    def retire_task(tid: int, body: dict, request: Request):
        """Retire a task with a mandatory reason. The reason is stored as a
        retire_note (the note column the reporter asked for) and the task
        moves to the terminal retired state — done stays reserved for
        verified work. Idempotent on idempotency_key; replaying a retired
        task converges without a second note."""
        _transition_allowed(request)
        reason = body.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise HTTPException(422, "retiring requires a non-empty reason")
        key = body.get("idempotency_key")
        if not key:
            raise HTTPException(422, "idempotency_key is required")
        with eng.begin() as c:
            t = _own_or_stack(c, tid, request)
            if t["state"] == "retired":
                return {"replayed": True, "task_id": tid}
            if "retired" not in TRANSITIONS.get(t["state"], set()):
                raise HTTPException(409, f"illegal transition {t['state']} -> retired")
            prior = row(c, "SELECT * FROM receipts WHERE task_id=:i AND idempotency_key=:k", i=tid, k=key)
            if prior:
                if prior.get("action") != "transition:retired":
                    raise HTTPException(409, f"idempotency key already used for {prior.get('action')}")
                return {"replayed": True, "receipt": prior}
            s, h = _canon({"reason": reason.strip(), "retired_by": _identity(request)})
            c.execute(text("INSERT INTO evidence(task_id,kind,body,sha256,created_at)"
                           " VALUES(:i,'retire_note',:b,:h,:n)"),
                      {"i": tid, "b": s, "h": h, "n": _now()})
            c.execute(text("UPDATE tasks SET state='retired', updated_at=:n WHERE id=:i"),
                      {"n": _now(), "i": tid})
            c.execute(text("INSERT INTO receipts(task_id,idempotency_key,action,body,created_at)"
                           " VALUES(:i,:k,:a,:b,:n)"),
                      {"i": tid, "k": key, "a": "transition:retired",
                       "b": json.dumps({"from": t["state"], "to": "retired"}), "n": _now()})
            _emit(c, t["tenant"], t["project"], "task.transition", tid,
                  {"task_id": tid, "from": t["state"], "to": "retired"})
            return {"replayed": False, "task_id": tid}

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
            t0 = task_or_404(c, tid)
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
            if t0["state"] in ("open", "needs_review"):
                _emit(c, t0["tenant"], t0["project"], "task.transition", tid,
                      {"task_id": tid, "from": t0["state"], "to": "claimed",
                       "owner": principal})
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
            t = task_or_404(c, tid)
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
            _emit(c, t["tenant"], t["project"], "worker.heartbeat", tid,
                  {"task_id": tid, "holder": holder, "lease_epoch": lease["lease_epoch"],
                   "lease_expires_at": lease["expires_at"]})
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
            t = task_or_404(c, tid)

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
            _emit(c, t["tenant"], t["project"], "attempt.started", tid,
                  {"task_id": tid, "attempt_id": a["id"], "attempt_no": attempt_no,
                   "lease_epoch": epoch})
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
            t = task_or_404(c, tid)
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
            _emit(c, t["tenant"], t["project"], "attempt.finished", tid,
                  {"task_id": tid, "attempt_id": attempt_id, "verdict": verdict})
            _remember(c, tid, key, "result", r)
            return {"replayed": False, "result": r}

    @app.post("/v1/workers/heartbeat")
    @app.post("/api/v1/workers/heartbeat")
    def worker_heartbeat(body: dict, request: Request):
        """Worker liveness signal. Emits worker.heartbeat — drives the UI's
        stuck/idle indicator, never progress (a silent worker yields silence,
        and the UI derives stuck from heartbeat absence)."""
        _stack_only(_identity(request))
        tid = _int_field(body, "task_id")
        holder = body.get("holder")
        if tid is None or not isinstance(holder, str) or not holder.strip():
            raise HTTPException(422, "task_id and holder are required")
        with eng.begin() as c:
            t = task_or_404(c, tid)
            lease = row(c, "SELECT * FROM leases WHERE task_id=:i", i=tid)
            eid = _emit(c, t["tenant"], t["project"], "worker.heartbeat", tid,
                        {"task_id": tid, "holder": holder.strip(),
                         "lease_epoch": _int_field(body, "lease_epoch"),
                         "lease_holder": lease["holder"] if lease else None,
                         "lease_expires_at": lease["expires_at"] if lease else None})
            return {"id": eid, "type": "worker.heartbeat", "task_id": tid,
                    "tenant": t["tenant"], "project": t["project"]}

    @app.get("/v1/events")
    @app.get("/api/v1/events")
    async def events(request: Request, cursor: str = "", live: int = 0,
                       limit: int = 100):
        """SSE: the only live channel. Tenant comes from the session
        server-side, never from args. Omit cursor to replay full tenant
        history; pass the last seen id (explicit cursor wins, else
        Last-Event-ID) to resume. live=0 sends the backlog and closes
        (deterministic seed); live=1 (default) tails new rows with
        ": ping" keepalives — comments, not events, so the server never
        synthesizes progress."""
        from fastapi.responses import StreamingResponse
        ident = _identity(request)
        with eng.connect() as c:
            mine = _caller_tenant(request, c)  # session tenant wins; 403 when tenantless
        hdr = (request.headers.get("last-event-id") or "").strip()
        raw = cursor.strip() or hdr
        if raw and not raw.isdigit():
            raise HTTPException(422, "cursor must be a non-negative integer id")
        last = int(raw) if raw else 0

        async def gen():
            nonlocal last
            yield "retry: 3000\n\n"
            idle = 0
            while True:
                if await request.is_disconnected():
                    break
                with eng.connect() as c:
                    rows = _events_after(c, mine, last, limit)
                for r in rows:
                    last = r["id"]
                    yield _sse_frame(r["id"], r["type"], r["tenant"], r["project"],
                                     r["task_id"], r["created_at"], json.loads(r["body"]))
                if not live:
                    break
                idle = 0 if rows else idle + 1
                if idle and idle % 30 == 0:
                    yield ": ping\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "Connection": "keep-alive",
                                          "X-Accel-Buffering": "no"})

    # FL-A3: Lite /api/v1 aliases wrap the handlers above (same endpoint
    # objects, no duplicated bodies).
    try:
        from ledger.lite import mount_lite_routes as _mount_lite
    except ImportError:  # run as a bare module (python ledger/app.py)
        from lite import mount_lite_routes as _mount_lite  # type: ignore[no-redef]
    _mount_lite(app)

    return app


app = make_app()
