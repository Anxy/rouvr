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
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import secrets as _secrets

from fastapi import FastAPI, HTTPException, Request
from rapidfuzz import fuzz
from sqlalchemy import create_engine, text

try:
    from ledger import hyeve_auth as _ha
except ImportError:  # run as a bare module (python ledger/app.py)
    import hyeve_auth as _ha  # type: ignore[no-redef]

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
        c.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(title, body)"))
        _knowledge_search_schema(c)
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

    @app.middleware("http")
    async def _auth(request, call_next):
        # FL-A2: the session binds scope server-side. Every /v1 handler takes
        # the tenant from request.state.session_tenant (credential -> user
        # row), never from request args. supervisor=None sees all tenants.
        request.state.user = "supervisor"
        request.state.session_tenant = None
        request.state.hyeve_claims = None
        path = request.url.path
        if path.startswith("/v1") and path not in _ONBOARD_OPEN:
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
                    return JSONResponse({"detail": "unauthorized"}, status_code=401)
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
        tenant may restate it but a foreign one is refused (403) before any
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
                raise HTTPException(403, "tenant scope comes from the session")
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

    def _own_or_stack(c, tid, identity):
        """Cross-tenant task access is 404 (no existence oracle), matching
        knowledge reads (FL-A2 parity)."""
        t = task_or_404(c, tid)
        if identity != "supervisor" and t.get("tenant") != _tenant_of(c, identity):
            raise HTTPException(404, "task not found")
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
        """Register a project under an existing tenant (S022). Idempotent."""
        _stack_only(_identity(request))
        tenant, project = _scope(body)  # stack-only: explicit scope
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
    KNOWLEDGE_KINDS = {"user_profile", "project_profile", "note", "decision", "rule", "snapshot"}
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

    def _knowledge_or_404(c, kid, identity):
        k = row(c, "SELECT * FROM knowledge_entries WHERE id=:i", i=kid)
        if not k:
            raise HTTPException(404, "knowledge entry not found")
        if identity == "supervisor":
            return k
        try:
            caller_tenant = _tenant_of(c, identity)
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
                         n: int = 15, full: bool = False):
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
                    mine = _tenant_of(c, ident)
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
            boosted = {i: s + (1.0 / 61 if by_id[i].get("verified") else 0.0)
                       for i, s in fused.items()}
            for eid in sorted(boosted, key=lambda i: boosted[i], reverse=True):
                r = by_id[eid]
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
                if len(out) >= n:
                    break
            return {"items": out}

    @app.get("/v1/knowledge/{key}")
    def get_knowledge(key: str, request: Request, project: str | None = None):
        ident = _identity(request)
        with eng.begin() as c:
            try:
                return _krow(_knowledge_or_404(c, int(key), ident))
            except ValueError:
                pass
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
                    mine = _tenant_of(c, ident)
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
            return _krow(_knowledge_or_404(c, pick["id"], ident))

    @app.get("/v1/knowledge/{kid}/history")
    def knowledge_history(kid: int, request: Request):
        """Version chain for one entry, oldest first. Append-only history
        is the point: nothing here is ever rewritten, only superseded."""
        ident = _identity(request)
        with eng.begin() as c:
            head = _knowledge_or_404(c, kid, ident)
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
                        _knowledge_or_404(c, cur["id"], ident)
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
            old = _knowledge_or_404(c, kid, ident)
            if row(c, "SELECT id FROM knowledge_entries WHERE supersedes_id=:i", i=kid):
                raise HTTPException(409, "entry already superseded: version the latest instead")
            if ident != "supervisor":
                try:
                    caller_tenant = _tenant_of(c, ident)
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
            k = _knowledge_or_404(c, kid, ident)
            if ident != "supervisor" and ident != k.get("author"):
                raise HTTPException(403, "only the creator or an admin confirms")
            c.execute(text("UPDATE knowledge_entries SET verified=1 WHERE id=:i"), {"i": kid})
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
            seen = [r for r in rows if _can_see(r, ident, _tenant_of(c, ident))]
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
                    and _can_see(r, ident, _tenant_of(c, ident))]
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
                    mine = _tenant_of(c, ident)
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
                      and _can_see(r, ident, _tenant_of(c, ident))]
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
