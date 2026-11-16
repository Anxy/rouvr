"""Coding delivery APIs: exec-spec ask, git-grant (no token echo), start guards."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ledger.app import make_app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUVR_INSECURE_DEV", "1")
    monkeypatch.delenv("ROUVR_LEDGER_TOKEN", raising=False)
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    monkeypatch.setenv("SPEC_ROOT", str(tmp_path / "specs"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspaces").mkdir(parents=True, exist_ok=True)
    (tmp_path / "hidden").mkdir(parents=True, exist_ok=True)
    c = TestClient(make_app(f"sqlite:///{tmp_path / 'l.db'}"))
    c.post("/v1/tenants", json={"tenant": "acme"})
    c.post("/v1/projects", json={"tenant": "acme", "project": "site"})
    return c


def _task_with_contract(c, *, sealed=True):
    import os
    r = c.post("/v1/tasks_with_contract", json={
        "tenant": "acme", "project": "site",
        "title": "Add hello", "intent": "print hello",
        "contract": {"acceptance_criteria": ["prints hello"], "checks": ["prints hello"]},
    })
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    if sealed:
        from rouvr.hiddentests import write_hidden_suite
        root = Path(os.environ["HIDDEN_TESTS_ROOT"])
        write_hidden_suite(
            root, task_id=str(tid),
            files={"test_rouvr_hidden_x.py": "def test_a():\n    assert True\n"},
            created_by="test_author",
        )
        ev = c.post(f"/v1/tasks/{tid}/evidence", json={
            "kind": "suite_sealed",
            "body": {"files": ["test_rouvr_hidden_x.py"], "sealed_by": "test_author"},
        })
        assert ev.status_code == 200, ev.text
    return tid


def test_exec_spec_put_get_and_placeholder_refused(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    bad = c.put("/v1/projects/site/exec-spec",
                json={"tenant": "acme", "repo": "OWNER/REPO"})
    assert bad.status_code == 422
    ok = c.put("/v1/projects/site/exec-spec",
               json={"tenant": "acme", "repo": "acme/site", "base_branch": "main"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["repo"] == "acme/site" and body["needs_spec"] is False
    got = c.get("/v1/projects/site/exec-spec", params={"tenant": "acme"})
    assert got.status_code == 200
    assert got.json()["repo"] == "acme/site"
    spec_file = tmp_path / "specs" / "acme" / "site.yaml"
    assert spec_file.is_file()
    assert "acme/site" in spec_file.read_text()


def test_git_grant_writes_0600_and_never_echoes_token(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    tid = _task_with_contract(c, sealed=False)
    secret = "ghp_test_secret_do_not_echo"
    r = c.post(f"/v1/tasks/{tid}/git-grant", json={"github_token": secret})
    assert r.status_code == 200, r.text
    assert r.json() == {"accepted": True, "task_id": tid}
    assert secret not in r.text
    grant = tmp_path / "grants" / f".git-grant-{tid}.json"
    assert grant.is_file()
    assert (grant.stat().st_mode & 0o777) == 0o600
    assert json.loads(grant.read_text())["github_token"] == secret
    detail = c.get(f"/v1/tasks/{tid}").json()
    blob = json.dumps(detail)
    assert secret not in blob


def test_start_refuses_without_repo_or_suite(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    tid = _task_with_contract(c, sealed=False)
    r = c.post(f"/v1/tasks/{tid}/start", json={})
    assert r.status_code == 409
    assert "needs_spec" in r.json()["detail"]
    c.put("/v1/projects/site/exec-spec",
          json={"tenant": "acme", "repo": "acme/site"})
    r2 = c.post(f"/v1/tasks/{tid}/start", json={})
    assert r2.status_code == 409
    assert "sealed" in r2.json()["detail"].lower()


def test_start_calls_run_start_when_ready(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    tid = _task_with_contract(c, sealed=True)
    c.put("/v1/projects/site/exec-spec",
          json={"tenant": "acme", "repo": "acme/site"})
    with patch("rouvr.starter.run_start", new_callable=AsyncMock) as start:
        start.return_value = f"wf-acme-{tid}"
        r = c.post(f"/v1/tasks/{tid}/start", json={"requested_by": "owner"})
    assert r.status_code == 200, r.text
    assert r.json()["workflow_id"] == f"wf-acme-{tid}"
    start.assert_awaited_once()
    detail = c.get(f"/v1/tasks/{tid}").json()
    assert any(x.get("action") == "workflow:start" for x in detail["receipts"])


def test_pr_url_and_waiting_git_grant_flags(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    tid = _task_with_contract(c, sealed=False)
    c.post(f"/v1/tasks/{tid}/evidence", json={
        "kind": "credential_request",
        "body": {"reason": "git credential needed"},
    })
    d = c.get(f"/v1/tasks/{tid}").json()
    assert d["waiting_git_grant"] is True
    assert d["pr_url"] is None
    c.post(f"/v1/tasks/{tid}/evidence", json={
        "kind": "pr_record",
        "body": {"url": "https://github.com/acme/site/pull/1", "head_sha": "abc"},
    })
    d2 = c.get(f"/v1/tasks/{tid}").json()
    assert d2["pr_url"] == "https://github.com/acme/site/pull/1"
    assert d2["waiting_git_grant"] is False


def test_owner_create_project_auto_ensures_tenant(tmp_path, monkeypatch):
    """First-run: session owner creates a project with no prior tenant seed."""
    monkeypatch.delenv("ROUVR_INSECURE_DEV", raising=False)
    monkeypatch.setenv("ROUVR_LEDGER_TOKEN", "stack-token-onboard-test")
    monkeypatch.setenv("ROUVR_ADMIN_PASSWORD", "onboard-admin-password-0123456789")
    monkeypatch.setenv("ROUVR_AUTH_SECRET", "onboard-test-secret-not-for-prod")
    monkeypatch.setenv("HIDDEN_TESTS_ROOT", str(tmp_path / "hidden"))
    monkeypatch.setenv("SPEC_ROOT", str(tmp_path / "specs"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    (tmp_path / "workspaces").mkdir(parents=True, exist_ok=True)
    app = make_app(f"sqlite:///{tmp_path / 'owner.db'}")
    login_client = TestClient(app)
    login = login_client.post("/auth/login", data={
        "username": "admin", "password": "onboard-admin-password-0123456789",
    })
    assert login.status_code == 200, login.text
    csrf = login.json()["csrf_token"]
    assert login.json()["tenant"] == "demo"
    # Cookie session (no x-rouvr-token) → owner identity, not stack.
    c = TestClient(
        app,
        cookies=dict(login.cookies),
        headers={"Origin": "http://testserver", "x-csrf-token": csrf},
    )
    # No POST /v1/tenants — owner path must insert the tenant row.
    r = c.post("/v1/projects", json={"project": "site", "title": "Site"})
    assert r.status_code == 200, r.text
    assert r.json()["tenant"] == "demo"
    assert r.json()["project"] == "site"
    listed = c.get("/v1/projects")
    assert listed.status_code == 200
    assert any(p["project"] == "site" for p in listed.json()["projects"])


def test_project_git_grant_writes_0600_and_never_echoes(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    secret = "ghp_project_default_secret"
    r = c.post(
        "/v1/projects/site/git-grant",
        json={"tenant": "acme", "github_token": secret},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"accepted": True, "tenant": "acme", "project": "site"}
    assert secret not in r.text
    grant = tmp_path / "grants" / ".git-grant-default-acme-site.json"
    assert grant.is_file()
    assert (grant.stat().st_mode & 0o777) == 0o600
    assert json.loads(grant.read_text())["github_token"] == secret


def test_read_git_grant_prefers_task_then_project_default(tmp_path):
    from rouvr.gitops import read_git_grant

    root = tmp_path / "workspaces"
    root.mkdir()
    gdir = tmp_path / "grants"
    gdir.mkdir()
    default = gdir / ".git-grant-default-acme-site.json"
    default.write_text(json.dumps({"github_token": "default-tok"}))
    default.chmod(0o600)
    assert read_git_grant(root, "9", tenant="acme", project="site") == "default-tok"
    task = gdir / ".git-grant-9.json"
    task.write_text(json.dumps({"github_token": "task-tok"}))
    task.chmod(0o600)
    assert read_git_grant(root, "9", tenant="acme", project="site") == "task-tok"
