"""R4: thin start endpoint. R6: AIX-mapping registry test."""
import json

import pytest

from rouvr.api import build_app
from rouvr.errors import Code, RouvrError
from rouvr.personas import registry_from_aix_mapping


async def _call(app, method, path, body=None, token="t0k"):
    sent = []
    raw = json.dumps(body).encode() if body is not None else b""
    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    async def send(m):
        sent.append(m)
    hdrs = [(b"authorization", f"Bearer {token}".encode())] if token else []
    await app({"type": "http", "method": method, "path": path, "headers": hdrs}, receive, send)
    return sent[0]["status"], json.loads(sent[1]["body"])


async def test_thin_start_endpoint_idempotent():
    seen: dict[str, int] = {}
    async def start(wf):
        wid = f"rouvr-task-{wf.task_id}"
        seen[wid] = seen.get(wid, 0) + 1
        return wid, seen[wid] == 1
    app = build_app(start=start, token="t0k", tenant_id="t", project_id="p")
    body = {"task_id": "7", "contract_rev": "r1", "requested_by": "ankur",
            "tenant": "t", "project": "p"}
    assert await _call(app, "POST", "/workflows/start", body) == (200, {"workflow_id": "rouvr-task-7", "started": True})
    assert await _call(app, "POST", "/workflows/start", body) == (200, {"workflow_id": "rouvr-task-7", "started": False})
    assert (await _call(app, "POST", "/workflows/start", body, token="bad"))[0] == 401
    assert (await _call(app, "POST", "/workflows/start", {"task_id": "7"}))[0] == 400
    big = {"task_id": "7", "contract_rev": "r1", "requested_by": "x" * 70000}
    assert (await _call(app, "POST", "/workflows/start", big))[0] == 400
    other = dict(body, tenant="elsewhere")
    assert (await _call(app, "POST", "/workflows/start", other))[0] == 403  # scope not served


async def test_default_app_resolves_spec_timeout(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from temporalio.client import Client
    from rouvr.api import _make_default_app
    spec_dir = tmp_path / "t"
    spec_dir.mkdir()
    (spec_dir / "p.yaml").write_text(
        "repo: org/name\nbase_branch: main\ntest_command: ['pytest']\ntimeout_s: 999\n")
    (tmp_path / "tok").write_text("t0k")
    fake = SimpleNamespace(temporal_address="x", temporal_namespace="ns", tenant_id="t",
                           project_id="p", max_rework=2, assign_start_to_close_s=1800,
                           spec_root=tmp_path, start_api_token_file=tmp_path / "tok")
    monkeypatch.setattr("rouvr.config.Settings.from_env", staticmethod(lambda: fake))
    seen = {}
    async def fake_start(client, wf, *, report=False):
        seen["wf"] = wf
        return ("wid-1", True)
    monkeypatch.setattr("rouvr.starter.start_task_workflow", fake_start)
    async def _connect(*a, **k):
        return object()
    monkeypatch.setattr(Client, "connect", lambda *a, **k: _connect())
    app = _make_default_app()
    status, body = await _call(app, "POST", "/workflows/start",
                               {"task_id": "7", "contract_rev": "r1", "requested_by": "ankur",
                                "tenant": "t", "project": "p"})
    assert (status, body) == (200, {"workflow_id": "wid-1", "started": True})
    assert seen["wf"].spec_timeout_s == 999


def test_aix_mapping_registry():
    reg = registry_from_aix_mapping({"roles": {
        "coder": {"principal": "svc-b", "creds_file": "/x.json"},
        "reviewer": {"principal": "svc-r", "creds_file": "/y.json"},
        "closer": {"principal": "svc-c", "creds_file": "/z.json"}}}, "t")
    b, v = reg.pair("t", "coder", "reviewer")
    assert (b.role, v.role, b.muse_model, v.muse_model) == ("coder", "reviewer", "muse-wonder-1.0", "muse-spark")
    with pytest.raises(RouvrError) as ei:
        registry_from_aix_mapping({"roles": {"coder": {"principal": "p"}}}, "t")
    assert ei.value.code is Code.CONTRACT
    with pytest.raises(RouvrError) as ei:
        registry_from_aix_mapping({"roles": {"coder": "svc-b"}}, "t")  # type: ignore[dict-item]
    assert ei.value.code is Code.CONTRACT
    with pytest.raises(RouvrError) as ei:
        registry_from_aix_mapping(["not-a-mapping"], "t")  # type: ignore[arg-type]
    assert ei.value.code is Code.CONTRACT
    same = registry_from_aix_mapping({"roles": {
        "coder": {"principal": "s", "creds_file": "/x"}, "reviewer": {"principal": "s", "creds_file": "/y"}}}, "t")
    with pytest.raises(RouvrError) as ei:
        same.pair("t", "coder", "reviewer")
    assert ei.value.code is Code.SELF_REVIEW
