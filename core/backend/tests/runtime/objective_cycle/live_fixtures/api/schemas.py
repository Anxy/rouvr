"""Request/response shapes for the live-fixture items API."""

from __future__ import annotations

from typing import Any


# Machine-readable authority for the public routes exercised by the live backend
# fixtures and production objective-cycle contract.  Mutation authority is
# intentionally limited to the objective-cycle command path.
ENDPOINT_AUTHORITY_MATRIX: dict[str, Any] = {
    "version": 1,
    "categories": [
        "CANONICAL_COMMAND",
        "QUERY_PROJECTION",
        "COMPATIBILITY_ADAPTER",
        "ADMIN",
        "EXPERIMENTAL",
        "RETIRE",
    ],
    "routes": [
        {"method": "POST", "path": "/items", "authority": "RETIRE"},
        {"method": "GET", "path": "/items", "authority": "QUERY_PROJECTION"},
        {"method": "GET", "path": "/items/{item_id}", "authority": "QUERY_PROJECTION"},
        {
            "method": "POST",
            "path": "/items/{item_id}/archive",
            "authority": "RETIRE",
        },
        {
            "method": "POST",
            "path": "/aix/objectives/admit",
            "authority": "CANONICAL_COMMAND",
        },
        {
            "method": "POST",
            "path": "/aix/objectives/{objective_id}/cycles",
            "authority": "CANONICAL_COMMAND",
        },
        {
            "method": "POST",
            "path": "/aix/cycles/{cycle_id}/approval-reference",
            "authority": "CANONICAL_COMMAND",
        },
        {
            "method": "POST",
            "path": "/aix/cycles/{cycle_id}/reject",
            "authority": "CANONICAL_COMMAND",
        },
        {
            "method": "POST",
            "path": "/aix/cycles/{cycle_id}/block",
            "authority": "CANONICAL_COMMAND",
        },
        {
            "method": "POST",
            "path": "/aix/cycles/{cycle_id}/cancel",
            "authority": "CANONICAL_COMMAND",
        },
        {"method": "GET", "path": "/ready", "authority": "ADMIN"},
    ],
    "ready_follow_on_tasks": [
        "Remove remaining legacy mutation mounts after downstream clients migrate.",
        "Emit route-authority decisions in the command audit ledger.",
        "Add an integration assertion that every mounted mutation route has one writer.",
        "Migrate compatibility reads to explicit projection contracts.",
        "Run staging authorization and replay checks before production cutover.",
    ],
}


def _route_entry(method: str, path: str) -> dict[str, str] | None:
    normalized_method = str(method).upper()
    for route in ENDPOINT_AUTHORITY_MATRIX["routes"]:
        if route["method"] != normalized_method:
            continue
        template = route["path"]
        prefix, _, suffix = template.partition("{item_id}")
        if suffix and path.startswith(prefix) and path.endswith(suffix):
            return route
        if not suffix and template == path:
            return route
        if template == "/aix/objectives/{objective_id}/cycles" and path.startswith(
            "/aix/objectives/"
        ) and path.endswith("/cycles"):
            return route
        if template.startswith("/aix/cycles/{cycle_id}/") and path.startswith(
            "/aix/cycles/"
        ) and path.endswith(template.rsplit("/", 1)[-1]):
            return route
    return None


def assert_mutation_route_allowed(method: str, path: str) -> dict[str, str]:
    """Fail closed unless the route is an explicitly canonical command."""
    route = _route_entry(method, path)
    if route is None or route["authority"] != "CANONICAL_COMMAND":
        raise PermissionError("route is not an authorized canonical command")
    return route


def parse_create_payload(payload: dict[str, Any]) -> dict[str, str]:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("title is required")
    body = str(payload.get("body") or "")
    return {"title": title, "body": body}


def serialize_item(item: Any) -> dict[str, Any]:
    return {
        "id": int(item.id),
        "title": str(item.title),
        "body": str(item.body),
        "archived": bool(getattr(item, "archived", False)),
    }
