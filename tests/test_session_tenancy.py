"""FL-A2 session-scoped tenancy: scope comes from the session
(token/JWT -> user row via middleware), never from request args.

Parity rule pinned here (S022 contract preserved):
- row-addressed cross-tenant task access -> 404 (no oracle);
- knowledge rows invisible across the boundary -> 404 (no oracle);
- explicit foreign scope in a body -> 404 before any existence check
  (no ghost-vs-registered project oracle);
- worker-only endpoints -> 403 for user tokens, 401 anonymous.
"""
import hashlib
import os

from fastapi.testclient import TestClient

ACME = "acme"
GLOBEX = "globex"
SITE = "site"


_ABSENT = object()

def _apply_stack_env():
    prev_tok = os.environ.get("ROUVR_LEDGER_TOKEN", _ABSENT)
    prev_insecure = os.environ.get("ROUVR_INSECURE_DEV", _ABSENT)
    os.environ["ROUVR_LEDGER_TOKEN"] = "stack-secret"
    os.environ.pop("ROUVR_INSECURE_DEV", None)
    return (prev_tok, prev_insecure)


def _restore_env(saved):
    prev_tok, prev_insecure = saved
    if prev_tok is _ABSENT:
        os.environ.pop("ROUVR_LEDGER_TOKEN", None)
    else:
        os.environ["ROUVR_LEDGER_TOKEN"] = prev_tok
    if prev_insecure is _ABSENT:
        os.environ.pop("ROUVR_INSECURE_DEV", None)
    else:
        os.environ["ROUVR_INSECURE_DEV"] = prev_insecure


def _seed(tmp_path):
    from ledger.app import make_app
    app = make_app(f"sqlite:///{tmp_path}/s.db")
    stack = {"X-Rouvr-Token": "stack-secret"}
    amy = {"X-Rouvr-Token": "amy-tok"}
    gus = {"X-Rouvr-Token": "gus-tok"}
    c = TestClient(app)
    c.__enter__()
    for tenant in (ACME, GLOBEX):
        assert c.post("/v1/tenants", json={"tenant": tenant}, headers=stack).status_code == 200
        assert c.post("/v1/projects", json={"tenant": tenant, "project": SITE},
                      headers=stack).status_code == 200
    for user, tok, tenant in (("amy", "amy-tok", ACME), ("gus", "gus-tok", GLOBEX)):
        h = hashlib.sha256(tok.encode()).hexdigest()
        r = c.post("/v1/users", json={"username": user, "token_hash": h, "tenant": tenant},
                   headers=stack)
        assert r.status_code == 200, r.text
    return c, stack, amy, gus


def _task(c, headers, **kw):
    body = {"title": "t", "intent": "i", "tenant": ACME, "project": SITE}
    body.update(kw)
    return c.post("/v1/tasks", json=body, headers=headers)


def _entry(c, headers, **kw):
    body = {"tenant": ACME, "project": SITE, "kind": "note",
            "title": "note", "body": "body"}
    body.update(kw)
    return c.post("/v1/knowledge", json=body, headers=headers)


# -- c1: 403/404 parity -----------------------------------------------------

def test_cross_tenant_task_access_denied(tmp_path, monkeypatch):
    saved = _apply_stack_env()
    c, _stack, amy, gus = _seed(tmp_path)
    try:
        ta = _task(c, amy, title="acme task").json()["id"]
        assert c.get(f"/v1/tasks/{ta}", headers=amy).status_code == 200
        for r in (c.get(f"/v1/tasks/{ta}", headers=gus),
                  c.post(f"/v1/tasks/{ta}/evidence", json={"kind": "note", "body": {}},
                         headers=gus),
                  c.post(f"/v1/tasks/{ta}/contracts", json={"checks": ["x"]}, headers=gus)):
            assert r.status_code == 404, (r.status_code, r.text)
    finally:
        try:
            c.__exit__(None, None, None)
        finally:
            _restore_env(saved)


def test_explicit_foreign_scope_create_is_403_no_project_oracle(tmp_path, monkeypatch):
    saved = _apply_stack_env()
    c, _stack, _amy, gus = _seed(tmp_path)
    try:
        bodies = [
            ("POST", "/v1/tasks", {"title": "x", "intent": "i"}),
            ("POST", "/v1/tasks_with_contract",
             {"title": "x", "intent": "i",
              "contract": {"project_id": "s", "description": "d", "acceptance_criteria": ["c"]}}),
            ("POST", "/v1/knowledge", {"kind": "note", "title": "t", "body": "b"}),
            ("POST", "/v1/knowledge/store",
             {"items": [{"kind": "note", "title": "t", "body": "b"}]}),
            ("POST", "/v1/knowledge/compact", {}),
            ("POST", "/v1/knowledge/snapshot", {}),
            ("POST", "/v1/knowledge/store-and-close", {"items": []}),
            ("POST", "/v1/knowledge/curate", {}),
        ]
        for method, path, extra in bodies:
            for project in (SITE, "ghost"):
                body = {"tenant": ACME, "project": project, **extra}
                r = c.request(method, path, json=body, headers=gus)
                assert r.status_code == 404, (path, project, r.status_code, r.text)
    finally:
        try:
            c.__exit__(None, None, None)
        finally:
            _restore_env(saved)


def test_scope_defaults_from_session(tmp_path, monkeypatch):
    saved = _apply_stack_env()
    c, stack, amy, _gus = _seed(tmp_path)
    try:
        r = c.post("/v1/tasks", json={"title": "t", "intent": "i", "project": SITE}, headers=amy)
        assert r.status_code == 200, r.text
        assert (r.json()["tenant"], r.json()["project"]) == (ACME, SITE)
        r = c.post("/v1/knowledge", json={"project": SITE, "kind": "note",
                                          "title": "t", "body": "b"}, headers=amy)
        assert r.status_code == 200, r.text
        assert (r.json()["tenant"], r.json()["project"]) == (ACME, SITE)
        assert c.post("/v1/tasks", json={"title": "t", "intent": "i", "project": SITE},
                      headers=stack).status_code == 422
        assert c.post("/v1/knowledge", json={"project": SITE, "kind": "note",
                                             "title": "t", "body": "b"},
                      headers=stack).status_code == 422
    finally:
        try:
            c.__exit__(None, None, None)
        finally:
            _restore_env(saved)


def test_knowledge_read_parity_404(tmp_path, monkeypatch):
    saved = _apply_stack_env()
    c, _stack, amy, gus = _seed(tmp_path)
    try:
        company = _entry(c, amy, title="co", body="company canary").json()
        restricted = _entry(c, amy, title="re", body="restricted canary",
                            visibility="restricted").json()
        public = _entry(c, amy, title="pu", body="public canary",
                        visibility="public").json()
        for eid in (company["id"], restricted["id"]):
            assert c.get(f"/v1/knowledge/{eid}", headers=gus).status_code == 404
            assert c.get(f"/v1/knowledge/{eid}/history", headers=gus).status_code == 404
            assert c.post(f"/v1/knowledge/{eid}/versions",
                          json={"title": "x", "body": "y"}, headers=gus).status_code == 404
            assert c.post(f"/v1/knowledge/{eid}/confirm", json={}, headers=gus).status_code == 404
        hits = c.get("/v1/knowledge/search", params={"q": "canary"}, headers=gus).json()["items"]
        assert company["citation"] not in [h["citation"] for h in hits]
        assert c.get(f"/v1/knowledge/{public['id']}", headers=gus).status_code == 200
        assert c.post(f"/v1/knowledge/{public['id']}/versions",
                      json={"title": "x", "body": "y"}, headers=gus).status_code == 403
        assert c.post(f"/v1/knowledge/{public['id']}/confirm", json={}, headers=gus).status_code == 403
    finally:
        try:
            c.__exit__(None, None, None)
        finally:
            _restore_env(saved)


def test_stack_only_endpoints_403_for_users_401_anonymous(tmp_path, monkeypatch):
    saved = _apply_stack_env()
    c, _stack, amy, _gus = _seed(tmp_path)
    try:
        ta = _task(c, amy).json()["id"]
        denied = [
            ("POST", f"/v1/tasks/{ta}/transition", {"to": "blocked", "idempotency_key": "k"}),
            ("POST", f"/v1/tasks/{ta}/assignments/claim",
             {"key": "k", "principal": "p", "attempt_no": 1}),
            ("POST", f"/v1/tasks/{ta}/leases/renew",
             {"key": "k", "holder": "p", "expected_lease_epoch": 1}),
            ("POST", f"/v1/tasks/{ta}/assignments/reclaim", {"key": "k"}),
            ("POST", f"/v1/tasks/{ta}/attempts",
             {"key": "k", "attempt_no": 1, "lease_epoch": 1}),
            ("POST", f"/v1/tasks/{ta}/results",
             {"key": "k", "attempt_id": 1, "lease_epoch": 1, "verdict": "pass"}),
            ("GET", f"/v1/tasks/{ta}/binding", None),
            ("GET", f"/v1/tasks/{ta}/evidence/lookup?kind=note&fence=f", None),
            ("GET", "/v1/users", None),
            ("POST", "/v1/users", {"username": "x", "token_hash": "h", "tenant": ACME}),
            ("POST", "/v1/users/amy/disable", {}),
            ("POST", "/v1/tenants", {"tenant": "new"}),
            ("POST", "/v1/projects", {"tenant": ACME, "project": "new"}),
            ("POST", "/v1/grants", {"scope": "s", "expires_at": "2999-01-01T00:00:00+00:00",
                                    "approved_by": "o"}),
        ]
        for method, path, body in denied:
            r = c.request(method, path, json=body, headers=amy)
            assert r.status_code == 403, (method, path, r.status_code, r.text)
        for method, path, body in [("GET", "/v1/tasks", None),
                                   ("POST", "/v1/tasks", {"title": "t"}),
                                   ("GET", "/v1/knowledge/search?q=x", None),
                                   ("GET", "/v1/grants/active?scope=s", None)]:
            r = c.request(method, path, json=body)
            assert r.status_code == 401, (method, path, r.status_code, r.text)
    finally:
        try:
            c.__exit__(None, None, None)
        finally:
            _restore_env(saved)


def test_list_endpoints_ignore_foreign_tenant_param(tmp_path, monkeypatch):
    saved = _apply_stack_env()
    c, stack, amy, gus = _seed(tmp_path)
    try:
        _task(c, amy, title="a-task")
        assert c.post("/v1/tasks", json={"title": "g-task", "intent": "i",
                                         "tenant": GLOBEX, "project": SITE},
                      headers=gus).status_code == 200
        mine = c.get("/v1/tasks", params={"tenant": ACME}, headers=gus).json()["items"]
        assert mine and all(t["tenant"] == GLOBEX for t in mine)
        projs = c.get("/v1/projects", params={"tenant": ACME}, headers=gus).json()["projects"]
        assert projs == [{"tenant": GLOBEX, "project": SITE, "title": None, "code": None,
                          "description": None, "domain": None, "archived": 0}]
        assert len(c.get("/v1/tasks", params={"tenant": ACME}, headers=stack).json()["items"]) == 1
        assert len(c.get("/v1/projects", headers=stack).json()["projects"]) == 2
    finally:
        try:
            c.__exit__(None, None, None)
        finally:
            _restore_env(saved)


# -- c2: cross-tenant fuzz ----------------------------------------------------

CANARY = "zzfuzzcanary"


def test_cross_tenant_fuzz_no_read_no_write(tmp_path, monkeypatch):
    saved = _apply_stack_env()
    c, stack, amy, gus = _seed(tmp_path)
    try:
        ta = _task(c, amy, title=f"task {CANARY}",
                   intent=f"intent {CANARY}").json()["id"]
        assert c.post(f"/v1/tasks/{ta}/contracts", json={"checks": [CANARY]},
                      headers=amy).status_code == 200
        assert c.post(f"/v1/tasks/{ta}/evidence",
                      json={"kind": "note", "body": {"text": CANARY}},
                      headers=amy).status_code == 200
        co = _entry(c, amy, title=f"company {CANARY}", body=f"body {CANARY}").json()
        re_ = _entry(c, amy, title=f"restricted {CANARY}", body=f"body {CANARY}",
                     visibility="restricted").json()
        rule = _entry(c, amy, kind="rule", title=f"rule {CANARY}",
                      body=f"always {CANARY}").json()
        assert c.post(f"/v1/knowledge/{rule['id']}/confirm", json={}, headers=amy).status_code == 200
        pub = _entry(c, amy, title="pub", body="pub body", visibility="public").json()
        assert c.post("/v1/grants", json={"scope": "probe:neutral",
                                          "expires_at": "2999-01-01T00:00:00+00:00",
                                          "approved_by": "owner"}, headers=stack).status_code == 200
        before_tasks = c.get(f"/v1/tasks/{ta}", headers=stack).json()
        before_co = c.get(f"/v1/knowledge/{co['id']}", headers=stack).json()
        n_tasks = len(c.get("/v1/tasks", headers=stack).json()["items"])
        n_know = len(c.get("/v1/knowledge/search", params={"q": CANARY, "n": 100},
                           headers=stack).json()["items"])

        denied = [
            ("GET", f"/v1/tasks/{ta}", None),
            ("POST", "/v1/tasks", {"title": "x", "intent": "i", "tenant": ACME, "project": SITE}),
            ("POST", "/v1/tasks_with_contract",
             {"title": "x", "intent": "i", "tenant": ACME, "project": SITE,
              "contract": {"project_id": "s", "description": "d", "acceptance_criteria": ["c"]}}),
            ("POST", f"/v1/tasks/{ta}/contracts", {"checks": ["x"]}),
            ("POST", f"/v1/tasks/{ta}/evidence", {"kind": "note", "body": {}}),
            ("POST", f"/v1/tasks/{ta}/transition", {"to": "blocked", "idempotency_key": "k"}),
            ("GET", f"/v1/tasks/{ta}/binding", None),
            ("POST", f"/v1/tasks/{ta}/assignments/claim",
             {"key": "k", "principal": "p", "attempt_no": 1}),
            ("POST", f"/v1/tasks/{ta}/leases/renew",
             {"key": "k", "holder": "p", "expected_lease_epoch": 1}),
            ("POST", f"/v1/tasks/{ta}/assignments/reclaim", {"key": "k"}),
            ("POST", f"/v1/tasks/{ta}/attempts", {"key": "k", "attempt_no": 1, "lease_epoch": 1}),
            ("POST", f"/v1/tasks/{ta}/results",
             {"key": "k", "attempt_id": 1, "lease_epoch": 1, "verdict": "pass"}),
            ("GET", f"/v1/tasks/{ta}/evidence/lookup?kind=note&fence=f", None),
            ("POST", "/v1/knowledge", {"tenant": ACME, "project": SITE, "kind": "note",
                                       "title": "x", "body": "y"}),
            ("GET", f"/v1/knowledge/{co['id']}", None),
            ("GET", f"/v1/knowledge/{re_['id']}", None),
            ("GET", f"/v1/knowledge/{co['slug']}", None),
            ("GET", f"/v1/knowledge/{co['slug']}?project={SITE}", None),
            ("GET", f"/v1/knowledge/{co['id']}/history", None),
            ("POST", f"/v1/knowledge/{co['id']}/versions", {"title": "x", "body": "y"}),
            ("POST", f"/v1/knowledge/{co['id']}/confirm", {}),
            ("POST", f"/v1/knowledge/{pub['id']}/versions", {"title": "x", "body": "y"}),
            ("POST", f"/v1/knowledge/{pub['id']}/confirm", {}),
            ("POST", "/v1/knowledge/store",
             {"tenant": ACME, "project": SITE,
              "items": [{"kind": "note", "title": "x", "body": "y"}]}),
            ("POST", "/v1/knowledge/compact", {"tenant": ACME, "project": SITE}),
            ("POST", "/v1/knowledge/snapshot", {"tenant": ACME, "project": SITE}),
            ("POST", "/v1/knowledge/store-and-close", {"tenant": ACME, "project": SITE, "items": []}),
            ("POST", "/v1/knowledge/curate", {"tenant": ACME, "project": SITE}),
            ("GET", "/v1/users", None),
            ("POST", "/v1/users", {"username": "mallory", "token_hash": "h", "tenant": ACME}),
            ("POST", "/v1/users/amy/disable", {}),
            ("POST", "/v1/tenants", {"tenant": "evil"}),
            ("POST", "/v1/projects", {"tenant": ACME, "project": "evil"}),
            ("POST", "/v1/grants", {"scope": "s", "expires_at": "2999-01-01T00:00:00+00:00",
                                    "approved_by": "o"}),
            ("POST", "/v1/onboard/complete", {"project_name": "p", "project_purpose": "u",
                                              "project_type": "software"}),
            ("PUT", f"/v1/tasks/{ta}", {"title": "pwn"}),
            ("DELETE", f"/v1/tasks/{ta}", None),
            ("PATCH", f"/v1/tasks/{ta}", {"title": "pwn"}),
            ("PUT", f"/v1/knowledge/{co['id']}", {"title": "pwn"}),
            ("DELETE", f"/v1/knowledge/{co['id']}", None),
        ]
        for method, path, body in denied:
            r = c.request(method, path, json=body, headers=gus)
            assert r.status_code in (400, 401, 403, 404, 405, 422), (method, path, r.status_code)
            assert CANARY not in r.text, (method, path, r.text)

        filtered = [
            ("GET", "/v1/tasks", None),
            ("GET", "/v1/tasks?tenant=acme", None),
            ("GET", f"/v1/knowledge/search?q={CANARY}&n=100", None),
            ("GET", f"/v1/knowledge/search?q={CANARY}&project={SITE}&n=100", None),
            ("GET", "/v1/knowledge-rules", None),
            ("GET", f"/v1/knowledge-rules?project={SITE}", None),
            ("GET", "/v1/projects", None),
            ("GET", "/v1/projects?tenant=acme", None),
            ("GET", "/v1/grants/active?scope=probe:neutral", None),
        ]
        for method, path, body in filtered:
            r = c.request(method, path, json=body, headers=gus)
            assert r.status_code == 200, (method, path, r.status_code, r.text)
            assert CANARY not in r.text, (method, path, r.text)
            assert f'"tenant": "{ACME}"' not in r.text and f'"tenant":"{ACME}"' not in r.text, \
                (method, path, r.text)

        assert c.get(f"/v1/tasks/{ta}", headers=stack).json() == before_tasks
        assert c.get(f"/v1/knowledge/{co['id']}", headers=stack).json() == before_co
        assert len(c.get("/v1/tasks", headers=stack).json()["items"]) == n_tasks
        assert len(c.get("/v1/knowledge/search", params={"q": CANARY, "n": 100},
                         headers=stack).json()["items"]) == n_know
    finally:
        try:
            c.__exit__(None, None, None)
        finally:
            _restore_env(saved)
