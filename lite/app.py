"""Rouvr Lite application (FL-A3): wrap, don't duplicate.

Every ``/api/v1`` row reuses the ledger handler function objects, so the
``/v1/*`` paths stay the canonical implementation and ``/api/v1/*`` is a
pure alias. Session scope keeps coming from the ledger auth layer, which
sees the aliased paths in canonical form.
"""
from __future__ import annotations

import tempfile

from fastapi import FastAPI
from fastapi.routing import APIRoute

from ledger.app import make_app as make_ledger_app

LITE_PREFIX = "/api"

MAPPING: list = []


def _lite_path(ledger_path: str) -> str | None:
    if ledger_path == "/health":
        return LITE_PREFIX + "/v1/health"
    if ledger_path.startswith("/v1"):
        return LITE_PREFIX + ledger_path
    return None


def make_lite_app(db_url=None):
    """Build the Lite app on its own store handle.

    Mounts every ledger row under ``/api/v1`` (health included) reusing the
    same endpoint objects, then records the mapping table of
    ``(lite path, methods, handler)`` rows covering the mounted rows.
    """
    if db_url is None:
        fh = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        fh.close()
        db_url = "sqlite:///" + fh.name
    app = make_ledger_app(db_url)
    have: dict = {}
    for r in app.routes:
        if isinstance(r, APIRoute):
            for m in (r.methods or set()) - {"HEAD", "OPTIONS"}:
                have.setdefault((m, r.path), r.endpoint)
    rows = []
    for r in list(app.routes):
        if not isinstance(r, APIRoute):
            continue
        lite = _lite_path(r.path or "")
        if lite is None:
            continue
        methods = sorted((r.methods or set()) - {"HEAD", "OPTIONS"})
        if not methods:
            continue
        if not all((m, lite) in have for m in methods):
            app.add_api_route(
                lite, r.endpoint, methods=methods,
                name=(r.name + "_lite") if r.name else None,
                include_in_schema=True,
            )
            for m in methods:
                have[(m, lite)] = r.endpoint
        rows.append((lite, list(methods), have[(methods[0], lite)]))
    MAPPING.clear()
    MAPPING.extend(rows)
    return app
