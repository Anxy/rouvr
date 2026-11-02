"""Onboarding (hackathon cut): register via hyeve-auth (person-first),
ledger verifies Hyeve JWTs via JWKS, complete creates ledger
user/tenant/project plus user+project profiles as Knowledge entries.
Upstream hyeve calls are monkeypatched at ledger.hyeve_auth."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-secret")
    monkeypatch.setenv("HYEVE_AUTH_BASE_URL", "https://auth.test")
    monkeypatch.setenv("HYEVE_AUTH_TENANT", "rouvr-demo")
    import ledger.hyeve_auth as ha
    monkeypatch.setattr(ha, "create_person", lambda **kw: "person-123")
    monkeypatch.setattr(ha, "create_user", lambda **kw: {"user_id": "user-1",
                                                        "person_id": "person-123",
                                                        "username": kw["username"]})
    monkeypatch.setattr(ha, "login", lambda **kw: {"access_token": "jwt-amy"})
    monkeypatch.setattr(ha, "admin_token", lambda: "adm-tok")
    monkeypatch.setattr(ha, "ensure_rouvr_rbac", lambda: {"role": "rouvr-user"})
    assigned = {}

    def fake_assign(**kw):
        assigned.update(kw)
        return {"ok": True}

    monkeypatch.setattr(ha, "assign_role", fake_assign)
    monkeypatch.setattr(ha, "me", lambda token: {"username": "amy"})
    claims = {"sub": "hyeve-sub-amy", "tenant_id": "rouvr-demo"}

    def fake_verify(token):
        if token != "jwt-amy":
            raise ha.HyeveAuthError("HYEVE_TOKEN_INVALID:fake")
        return dict(claims)

    monkeypatch.setattr(ha, "verify_hyeve_jwt", fake_verify)
    holder = {}
    real_assign = ha.assign_role

    def spy_assign(**kw):
        holder.update(kw)
        return real_assign(**kw)

    monkeypatch.setattr(ha, "assign_role", spy_assign)
    from ledger.app import make_app
    app = make_app(f"sqlite:///{tmp_path}/o.db")
    with TestClient(app) as c:
        yield c, ha, holder


REG = {"username": "amy", "email": "amy@acme.example.com", "password": "pw-123456",
       "given_name": "Amy", "family_name": "Ng"}
FORM = {"display_name": "Amy Ng", "role_title": "Founder", "organisation": "Acme Legal",
        "use_case": "Contract review ops", "project_name": "contracts",
        "project_purpose": "Review contracts faster", "project_type": "operations",
        "north_star": "Same-day turnaround", "standing_rules": "Cite every clause."}
AMY_JWT = {"Authorization": "Bearer jwt-amy"}


def test_register_then_complete_onboards(ctx):
    c, _ha, _holder = ctx
    r = c.post("/v1/onboard/register", json=REG)  # no ledger token: pre-account path
    assert r.status_code == 200, r.text
    assert r.json()["access_token"] == "jwt-amy"
    r = c.post("/v1/onboard/complete", json=FORM, headers=AMY_JWT)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["username"], out["tenant"], out["project"]) == ("amy", "acme-legal", "contracts")
    assert out["rouvr_token"]  # issued once, for CLI use
    assert out["user_profile"].startswith("knowledge:")
    assert out["project_profile"].startswith("knowledge:")


def test_jwt_scopes_knowledge_to_onboarded_tenant(ctx):
    c, _ha, _holder = ctx
    c.post("/v1/onboard/complete", json=FORM, headers=AMY_JWT)
    hits = c.get("/v1/knowledge/search", params={"q": "clause", "full": "true"},
                 headers=AMY_JWT).json()["items"]
    assert hits and hits[0]["tenant"] == "acme-legal"
    prof = c.get("/v1/knowledge/search", params={"q": "turnaround", "full": "true"},
                 headers=AMY_JWT).json()["items"]
    assert prof and prof[0]["kind"] == "project_profile" and prof[0]["verified"] == 1


def test_complete_is_idempotent(ctx):
    c, _ha, _holder = ctx
    first = c.post("/v1/onboard/complete", json=FORM, headers=AMY_JWT).json()
    second = c.post("/v1/onboard/complete", json=FORM, headers=AMY_JWT).json()
    assert (second["tenant"], second["project"]) == (first["tenant"], first["project"])
    assert not second.get("rouvr_token")  # token shown once
    profiles = c.get("/v1/knowledge/search", params={"q": "profile", "full": "true"},
                     headers={"X-Rouvr-Token": "stack-secret"}).json()["items"]
    kinds = sorted(p["kind"] for p in profiles if "profile" in p["kind"])
    assert kinds == ["project_profile", "user_profile"]  # no duplicates


def test_forged_jwt_rejected(ctx):
    c, _ha, _holder = ctx
    assert c.post("/v1/onboard/complete", json=FORM,
                  headers={"Authorization": "Bearer forged"}).status_code == 401
    assert c.get("/v1/knowledge/search", params={"q": "x"},
                 headers={"Authorization": "Bearer forged"}).status_code == 401
    # a valid JWT for a never-onboarded subject gets no tenant access
    assert c.get("/v1/knowledge/search", params={"q": "x"},
                 headers=AMY_JWT).status_code == 403


def test_person_service_unconfigured_fails_named(ctx, monkeypatch):
    c, ha, _holder = ctx
    monkeypatch.setenv("HYEVE_PERSON_MODE", "person_service")

    def no_person(**kw):
        raise ha.HyeveAuthError("PERSON_SERVICE_NOT_CONFIGURED")

    monkeypatch.setattr(ha, "create_person", no_person)
    r = c.post("/v1/onboard/register", json=REG)
    assert r.status_code == 503 and "PERSON_SERVICE_NOT_CONFIGURED" in r.text
    # a presenter-supplied person_id bypasses the person service entirely
    r = c.post("/v1/onboard/register", json={**REG, "person_id": "person-pre"})
    assert r.status_code == 200, r.text


def test_register_inline_person_mode(ctx, monkeypatch):
    """VPS1 build shape: auth receives the inline person object and
    returns the person_id it created/linked."""
    c, ha, _holder = ctx
    seen = {}

    def capture(**kw):
        seen.update(kw)
        return {"id": "u1", "person_id": "person-inline-1"}

    monkeypatch.setattr(ha, "create_user", capture)
    r = c.post("/v1/onboard/register", json=REG)
    assert r.status_code == 200, r.text
    assert seen["person"] == {"person_type": "Customer", "given_name": "Amy",
                              "family_name": "Ng", "display_name": "Amy Ng"}
    assert seen["person_id"] == ""
    assert r.json()["person_id"] == "person-inline-1"


def test_demo_page_served(ctx):
    c, _ha, _holder = ctx
    r = c.get("/demo/onboard")
    assert r.status_code == 200 and "Register" in r.text


def test_register_bootstraps_rbac_and_assigns_role(ctx, monkeypatch):
    c, ha, holder = ctx
    r = c.post("/v1/onboard/register", json=REG)
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == "user-1"
    assert holder == {"user_id": "user-1"}  # role_name defaults to rouvr-user


def test_register_without_admin_config_fails_named(ctx, monkeypatch):
    c, ha, _holder = ctx

    def no_admin():
        raise ha.HyeveAuthError("ADMIN_NOT_CONFIGURED:x")

    monkeypatch.setattr(ha, "admin_token", no_admin)
    r = c.post("/v1/onboard/register", json=REG)
    assert r.status_code == 503 and "ADMIN_NOT_CONFIGURED" in r.text


def test_username_comes_from_me_not_claims(ctx, monkeypatch):
    c, ha, _holder = ctx
    monkeypatch.setattr(ha, "me", lambda token: {"username": "me-amy"})
    r = c.post("/v1/onboard/complete", json=FORM, headers=AMY_JWT)
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "me-amy"


def test_me_normalizes_current_nested_auth_shape(monkeypatch):
    import ledger.hyeve_auth as ha

    monkeypatch.setenv("HYEVE_AUTH_BASE_URL", "https://auth.test")
    monkeypatch.setenv("HYEVE_AUTH_TENANT", "rouvr-demo")

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"tenant_id": "rouvr-demo", "user": {"username": "nested-amy"}}

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def get(_url, headers):
            assert headers["X-Hyeve-Tenant-Id"] == "rouvr-demo"
            return Response()

    monkeypatch.setattr(ha.httpx, "Client", lambda timeout: Client())
    result = ha.me("jwt-amy")

    assert result["username"] == "nested-amy"
    assert result["user"]["username"] == "nested-amy"
