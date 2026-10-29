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
    body = {"task_id": "7", "contract_rev": "r1", "requested_by": "ankur"}
    assert await _call(app, "POST", "/workflows/start", body) == (200, {"workflow_id": "rouvr-task-7", "started": True})
    assert await _call(app, "POST", "/workflows/start", body) == (200, {"workflow_id": "rouvr-task-7", "started": False})
    assert (await _call(app, "POST", "/workflows/start", body, token="bad"))[0] == 401
    assert (await _call(app, "POST", "/workflows/start", {"task_id": "7"}))[0] == 400


def test_aix_mapping_registry():
    reg = registry_from_aix_mapping({"roles": {
        "builder": {"principal": "svc-b", "creds_file": "/x.json"},
        "reviewer": {"principal": "svc-r", "creds_file": "/y.json"},
        "closer": {"principal": "svc-c", "creds_file": "/z.json"}}}, "t")
    b, v = reg.pair("t", "builder", "reviewer")
    assert (b.role, v.role, b.muse_model, v.muse_model) == ("builder", "verifier", "muse-wonder-1.0", "muse-spark")
    with pytest.raises(RouvrError) as ei:
        registry_from_aix_mapping({"roles": {"builder": {"principal": "p"}}}, "t")
    assert ei.value.code is Code.CONTRACT
    same = registry_from_aix_mapping({"roles": {
        "builder": {"principal": "s", "creds_file": "/x"}, "reviewer": {"principal": "s", "creds_file": "/y"}}}, "t")
    with pytest.raises(RouvrError) as ei:
        same.pair("t", "builder", "reviewer")
    assert ei.value.code is Code.SELF_REVIEW
