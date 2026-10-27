"""Minimal ASGI app for the live-fixture items API (stdlib only)."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import parse_qs

from backend.tests.runtime.objective_cycle.live_fixtures.api import schemas, service


def _json_response(status: int, body: Any) -> dict[str, Any]:
    raw = json.dumps(body).encode("utf-8")
    return {
        "status": status,
        "headers": [(b"content-type", b"application/json")],
        "body": raw,
    }


def _read_json(scope: dict[str, Any], receive: Callable) -> dict[str, Any]:
    # Body is injected by the test client helper below.
    raw = scope.get("_body") or b"{}"
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not raw:
        return {}
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON object required")
    return data


async def app(scope: dict[str, Any], receive: Callable, send: Callable) -> None:
    if scope["type"] != "http":
        return
    method = str(scope.get("method") or "GET").upper()
    path = str(scope.get("path") or "/")
    query = parse_qs(str(scope.get("query_string", b""), "utf-8", "replace"))

    try:
        if method == "GET" and path == "/items":
            include = str((query.get("include_archived") or ["0"])[0]).lower() in {
                "1",
                "true",
                "yes",
            }
            items = service.STORE.list_items(include_archived=include)
            resp = _json_response(200, [schemas.serialize_item(i) for i in items])
        elif method == "POST" and path == "/items":
            payload = _read_json(scope, receive)
            parsed = schemas.parse_create_payload(payload)
            item = service.STORE.create(title=parsed["title"], body=parsed["body"])
            resp = _json_response(201, schemas.serialize_item(item))
        elif method == "POST" and path.startswith("/items/") and path.endswith("/archive"):
            item_id = int(path.split("/")[2])
            item = service.STORE.archive(item_id)
            if item is None:
                resp = _json_response(404, {"error": "not_found"})
            else:
                resp = _json_response(200, schemas.serialize_item(item))
        elif method == "GET" and path.startswith("/items/"):
            item_id = int(path.rsplit("/", 1)[-1])
            item = service.STORE.get(item_id)
            if item is None:
                resp = _json_response(404, {"error": "not_found"})
            else:
                resp = _json_response(200, schemas.serialize_item(item))
        else:
            resp = _json_response(404, {"error": "not_found", "path": path, "method": method})
    except ValueError as exc:
        resp = _json_response(400, {"error": str(exc)})
    except Exception as exc:  # pragma: no cover - defensive
        resp = _json_response(500, {"error": str(exc)})

    await send(
        {
            "type": "http.response.start",
            "status": int(resp["status"]),
            "headers": list(resp["headers"]),
        }
    )
    await send({"type": "http.response.body", "body": resp["body"]})


async def call_app(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    query: str = "",
) -> tuple[int, Any]:
    """Tiny ASGI caller used by focused tests (no external HTTP server)."""
    payload = b""
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
    scope: dict[str, Any] = {
        "type": "http",
        "method": method.upper(),
        "path": path,
        "query_string": query.encode("utf-8"),
        "_body": payload,
    }
    status_box: dict[str, int] = {"status": 500}
    body_box: dict[str, bytes] = {"body": b""}

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            status_box["status"] = int(message["status"])
        elif message["type"] == "http.response.body":
            body_box["body"] += message.get("body") or b""

    await app(scope, receive, send)
    raw = body_box["body"].decode("utf-8") if body_box["body"] else "null"
    return status_box["status"], json.loads(raw)
