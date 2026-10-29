"""rouvr/api.py — thin start endpoint (owner decision 2, 29 Oct 2026).

Rouvr starts a workflow by calling this endpoint. It does only admission: validate, auth, start the
TaskWorkflow with a deterministic id, return. No business logic, no outbox (outbox-driven start is later/optional).

Contract (proposal):
  POST /workflows/start
    Headers: Authorization: Bearer <token>   (token from START_API_TOKEN_FILE)
             Idempotency-Key: <opaque>       (optional; the workflow id is the real dedupe key)
    Body:    {"task_id": "123", "contract_rev": "r1", "requested_by": "ankur"}
    200:     {"workflow_id": "...", "started": true|false}   # false = duplicate, same id returned
    400:     {"error": "E_BAD_REQUEST", "message": ...}
    401:     {"error": "E_AUTHZ", "message": ...}
  GET /healthz -> 200 {"ok": true}
Run: uvicorn-compatible ASGI app, e.g. `uvicorn rouvr.api:app` (uvicorn not a dependency; any ASGI server).
"""
from __future__ import annotations

import hmac
import json
from typing import Any, Awaitable, Callable

from rouvr.contracts import TaskWorkflowInput

StartFn = Callable[[TaskWorkflowInput], Awaitable[tuple[str, bool]]]
REQUIRED = ("task_id", "contract_rev", "requested_by")


def build_app(*, start: StartFn, token: str, tenant_id: str, project_id: str, max_rework: int = 2):
    async def send_json(send, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": raw})

    async def app(scope: dict, receive, send) -> None:
        if scope["type"] != "http":
            return
        method, path = scope["method"], scope["path"]
        if method == "GET" and path == "/healthz":
            return await send_json(send, 200, {"ok": True})
        if not (method == "POST" and path == "/workflows/start"):
            return await send_json(send, 404, {"error": "E_BAD_REQUEST", "message": "not found"})
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        if not token or not hmac.compare_digest(auth, f"Bearer {token}"):
            return await send_json(send, 401, {"error": "E_AUTHZ", "message": "bad token"})
        body = b""
        while True:
            m = await receive()
            body += m.get("body", b"")
            if not m.get("more_body"):
                break
        try:
            data: Any = json.loads(body or b"{}")
            missing = [k for k in REQUIRED if not isinstance(data.get(k), str) or not data[k]]
        except (ValueError, AttributeError):
            missing = list(REQUIRED)
        if missing:
            return await send_json(send, 400, {"error": "E_BAD_REQUEST", "message": f"missing: {missing}"})
        wf = TaskWorkflowInput(tenant_id=tenant_id, project_id=project_id, task_id=data["task_id"],
                               contract_rev=data["contract_rev"], requested_by=data["requested_by"],
                               max_rework=max_rework)
        wid, started = await start(wf)
        return await send_json(send, 200, {"workflow_id": wid, "started": started})

    return app


def _make_default_app():  # pragma: no cover - wiring
    from temporalio.client import Client
    from rouvr.config import Settings
    from rouvr.starter import start_task_workflow
    s = Settings.from_env()
    holder: dict = {}

    async def start(wf: TaskWorkflowInput) -> tuple[str, bool]:
        if "c" not in holder:
            holder["c"] = await Client.connect(s.temporal_address, namespace=s.temporal_namespace)
        return await start_task_workflow(holder["c"], wf, report=True)

    return build_app(start=start, token=s.start_api_token_file.read_text().strip(),
                     tenant_id=s.tenant_id, project_id=s.project_id, max_rework=s.max_rework)


def __getattr__(name: str):  # lazy `rouvr.api:app` so imports in tests need no env
    if name == "app":
        return _make_default_app()
    raise AttributeError(name)
