"""S022: tenant/project partitions. Separate mandatory fields, no defaults;
a caller sees only its own tenant's projects and tasks; binding fails closed."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("ROUVR_LEDGER_TOKEN", raising=False)
    from ledger.app import make_app
    return make_app(f"sqlite:///{tmp_path}/t.db")


@pytest.fixture()
def c(app):
    with TestClient(app) as client:
        yield client


def _tenant(c, name, headers=None):
    r = c.post("/v1/tenants", json={"tenant": name}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _project(c, tenant, project, headers=None):
    r = c.post("/v1/projects", json={"tenant": tenant, "project": project}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_project_requires_tenant_first(c):
    assert c.post("/v1/projects", json={"tenant": "acme", "project": "site"}).status_code == 404
    _tenant(c, "acme")
    _project(c, "acme", "site")
    # idempotent re-creation returns the same rows, never duplicates
    assert _tenant(c, "acme")["tenant"] == "acme"
    assert _project(c, "acme", "site")["project"] == "site"
    assert c.post("/v1/tenants", json={}).status_code == 422
    assert c.post("/v1/projects", json={"tenant": "acme"}).status_code == 422


def test_task_create_requires_scope(c):
    _tenant(c, "acme")
    _project(c, "acme", "site")
    assert c.post("/v1/tasks", json={"title": "t", "intent": "i"}).status_code == 422
    assert c.post("/v1/tasks", json={"title": "t", "intent": "i",
                                     "tenant": "acme"}).status_code == 422
    assert c.post("/v1/tasks", json={"title": "t", "intent": "i", "tenant": "acme",
                                     "project": "ghost"}).status_code == 404
    assert c.post("/v1/tasks_with_contract",
                  json={"title": "t", "intent": "i",
                        "contract": {"project_id": "x", "description": "d",
                                     "acceptance_criteria": ["c"]}}).status_code == 422
    r = c.post("/v1/tasks", json={"title": "t", "intent": "i", "tenant": "acme",
                                  "project": "site"})
    assert r.status_code == 200, r.text
    assert (r.json()["tenant"], r.json()["project"]) == ("acme", "site")
    r = c.post("/v1/tasks_with_contract",
               json={"title": "t2", "intent": "i", "tenant": "acme", "project": "site",
                     "contract": {"project_id": "site", "description": "d",
                                  "acceptance_criteria": ["c"]}})
    assert r.status_code == 200, r.text
    assert (r.json()["tenant"], r.json()["project"]) == ("acme", "site")


def _user(c, stack, name, token, tenant):
    import hashlib
    h = hashlib.sha256(token.encode()).hexdigest()
    r = c.post("/v1/users", json={"username": name, "token_hash": h, "tenant": tenant},
               headers=stack)
    assert r.status_code == 200, r.text


def test_tenant_visibility_and_cross_tenant_denied(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    from ledger.app import make_app
    app = make_app(f"sqlite:///{tmp_path}/v.db")
    stack = {"X-Rouvr-Token": "stack-secret"}
    with TestClient(app) as c:
        _tenant(c, "acme", headers=stack)
        _tenant(c, "globex", headers=stack)
        _project(c, "acme", "site", headers=stack)
        _project(c, "globex", "site", headers=stack)
        _user(c, stack, "amy", "amy-tok", "acme")
        _user(c, stack, "gus", "gus-tok", "globex")
        amy = {"X-Rouvr-Token": "amy-tok"}
        ta = c.post("/v1/tasks", json={"title": "a", "intent": "i", "tenant": "acme",
                                       "project": "site"}, headers=amy).json()
        # cross-tenant create refused even with a registered scope
        assert c.post("/v1/tasks", json={"title": "x", "intent": "i", "tenant": "globex",
                                         "project": "site"}, headers=amy).status_code == 403
        a2 = c.post("/v1/tasks", json={"title": "a2", "intent": "i", "tenant": "acme",
                                             "project": "site"}, headers=stack)
        assert a2.status_code == 200, a2.text
        mine = c.get("/v1/tasks", headers=amy).json()["items"]
        assert [(t["tenant"], t["project"]) for t in mine] == [("acme", "site")] * 2
        assert len(c.get("/v1/tasks", headers=stack).json()["items"]) == 2
        assert len(c.get("/v1/tasks", params={"tenant": "acme"},
                         headers=stack).json()["items"]) == 2
        assert c.get("/v1/tasks", params={"tenant": "globex"},
                     headers=stack).json()["items"] == []
        gus = {"X-Rouvr-Token": "gus-tok"}
        r = c.get(f"/v1/tasks/{ta['id']}", headers=gus)
        assert r.status_code == 403 and "acme" in r.json()["detail"]
        assert c.get("/v1/projects", headers=amy).json()["projects"] == [
            {"tenant": "acme", "project": "site"}]
        assert len(c.get("/v1/projects", headers=stack).json()["projects"]) == 2


def test_users_require_tenant(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    from ledger.app import make_app
    app = make_app(f"sqlite:///{tmp_path}/u.db")
    stack = {"X-Rouvr-Token": "stack-secret"}
    import hashlib
    with TestClient(app) as c:
        h = hashlib.sha256(b"x").hexdigest()
        assert c.post("/v1/users", json={"username": "u", "token_hash": h},
                      headers=stack).status_code == 422  # no default tenant
        _tenant(c, "acme", headers=stack)
        _user(c, stack, "u", "u-tok", "acme")
        names = [u["username"] for u in c.get("/v1/users", headers=stack).json()["items"]]
        assert names == ["u"]


def test_binding_rejects_scope_mismatch():
    import asyncio
    from types import SimpleNamespace
    from temporalio.exceptions import ApplicationError
    from rouvr.activities import Activities
    from rouvr.contracts import TaskWorkflowInput

    class FakeWH:
        async def get_task(self, task_id):
            return {"id": task_id, "contract_rev": "r1", "record_version": 3,
                    "tenant": "acme", "project": "site"}

    class Pair:
        name = "p"

    personas = SimpleNamespace(role_pair=lambda tenant: (Pair(), Pair()))
    acts = Activities(SimpleNamespace(workhub_for=lambda p: FakeWH(), personas=personas))
    b = asyncio.run(acts.verify_binding(TaskWorkflowInput("acme", "site", "1", "r1", "dev")))
    assert b.task_id == "1"
    for tenant, project in (("globex", "site"), ("acme", "other"), ("", "site")):
        try:
            asyncio.run(acts.verify_binding(TaskWorkflowInput(tenant, project, "1", "r1", "dev")))
        except ApplicationError as e:
            assert "scope" in str(e).lower()
        else:  # pragma: no cover - fail closed means this must never happen
            raise AssertionError(f"scope mismatch accepted: {tenant}/{project}")


def test_schema_comments_hold_no_semicolons():
    # make_app splits schema.sql on ";" before stripping "--" lines.
    schema = Path(__file__).resolve().parent.parent / "ledger" / "schema.sql"
    bad = [l for l in schema.read_text().splitlines()
           if l.strip().startswith("--") and ";" in l]
    assert not bad, bad
