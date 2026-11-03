"""Rouvr Lite router: wrap, don't duplicate (FL-A3).

Exposes every existing ``ledger/app.py`` handler a second time under the
stable ``/api/v1`` prefix used by the WorkHub D19 contract
(``APIRouter(prefix=\"/api/v1\")`` on the full WorkHub service). The Lite
routes reuse the *same endpoint function objects* — no copied handler
bodies, no divergent validation — so ``/v1/*`` stays the canonical
implementation and ``/api/v1/*`` is a pure alias.

D19 note: Lite stays behind the existing WorkHub interface (same
``/api/v1`` prefix) so the same contract can later run on full WorkHub.
Ledger request/response shapes are retained; a WorkHub migration only
re-prefixes project scoping, it does not change handler semantics.
"""
from __future__ import annotations

LITE_PREFIX = "/api"


def lite_path(ledger_path: str) -> str | None:
    """Ledger path -> Lite path. ``/v1/*`` shifts to ``/api/v1/*``; ``/health``
    gains an ``/api/v1/health`` alias. Anything else (e.g. ``/demo/onboard``,
    docs mounts) is intentionally unaliased -> None."""
    if ledger_path == "/health":
        return "/api/v1/health"
    if ledger_path.startswith("/v1"):
        return LITE_PREFIX + ledger_path
    return None


# Static mapping table (code source of truth; mirrored by
# ledger/LITE_API_MAPPING.md). Method, ledger path, handler function
# name in ledger/app.py::make_app.
MAPPING: list[tuple[str, str, str]] = [
    ("GET", "/health", "health"),
    ("GET", "/v1/version", "version"),
    ("POST", "/v1/users", "create_user"),
    ("GET", "/v1/users", "list_users"),
    ("POST", "/v1/tenants", "create_tenant"),
    ("POST", "/v1/projects", "create_project"),
    ("GET", "/v1/projects", "list_projects"),
    ("POST", "/v1/users/{username}/disable", "disable_user"),
    ("POST", "/v1/onboard/register", "onboard_register"),
    ("POST", "/v1/onboard/login", "onboard_login"),
    ("POST", "/v1/onboard/complete", "onboard_complete"),
    ("POST", "/v1/knowledge", "create_knowledge"),
    ("GET", "/v1/knowledge/search", "search_knowledge"),
    ("GET", "/v1/knowledge/{key}", "get_knowledge"),
    ("GET", "/v1/knowledge/{kid}/history", "knowledge_history"),
    ("POST", "/v1/knowledge/{kid}/versions", "version_knowledge"),
    ("POST", "/v1/knowledge/{kid}/confirm", "confirm_knowledge"),
    ("POST", "/v1/knowledge/store", "knowledge_store"),
    ("POST", "/v1/knowledge/compact", "knowledge_compact"),
    ("POST", "/v1/knowledge/snapshot", "knowledge_snapshot"),
    ("POST", "/v1/knowledge/store-and-close", "knowledge_store_and_close"),
    ("GET", "/v1/knowledge-rules", "knowledge_verified_rules"),
    ("POST", "/v1/knowledge/curate", "knowledge_curate"),
    ("POST", "/v1/tasks", "create_task"),
    ("POST", "/v1/tasks_with_contract", "create_task_with_contract"),
    ("GET", "/v1/tasks", "list_tasks"),
    ("GET", "/v1/tasks/{tid}", "get_task"),
    ("POST", "/v1/tasks/{tid}/contracts", "add_contract"),
    ("POST", "/v1/grants", "create_grant"),
    ("GET", "/v1/grants/active", "active_grant"),
    ("GET", "/v1/tasks/{tid}/evidence/lookup", "lookup_evidence"),
    ("POST", "/v1/tasks/{tid}/evidence", "add_evidence"),
    ("POST", "/v1/tasks/{tid}/transition", "transition"),
    ("GET", "/v1/tasks/{tid}/binding", "get_binding"),
    ("POST", "/v1/tasks/{tid}/assignments/claim", "claim_assignment"),
    ("POST", "/v1/tasks/{tid}/leases/renew", "renew_lease"),
    ("POST", "/v1/tasks/{tid}/assignments/reclaim", "reclaim_assignment"),
    ("POST", "/v1/tasks/{tid}/attempts", "open_attempt"),
    ("POST", "/v1/tasks/{tid}/results", "record_result"),
    ("POST", "/v1/workers/heartbeat", "worker_heartbeat"),
    ("GET", "/v1/events", "events"),
    ("POST", "/v1/chat", "chat"),
]


def mount_lite_routes(app) -> None:
    """Add ``/api/v1`` aliases reusing the same endpoint objects.

    Idempotent: calling twice does not duplicate routes.
    """
    from fastapi.routing import APIRoute

    existing: set[tuple[str, str]] = set()
    for r in app.routes:
        rp = getattr(r, "path", None)
        for m in (getattr(r, "methods", None) or set()) - {"HEAD", "OPTIONS"}:
            existing.add((m, rp))
    for route in list(app.routes):
        if not isinstance(route, APIRoute):
            continue
        lite = lite_path(route.path)
        if lite is None:
            continue
        methods = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
        if not methods or all((m, lite) in existing for m in methods):
            continue
        app.add_api_route(
            lite,
            route.endpoint,
            methods=methods or None,
            name=f"{route.name}_lite" if route.name else None,
            include_in_schema=True,
        )
        for m in methods:
            existing.add((m, lite))
