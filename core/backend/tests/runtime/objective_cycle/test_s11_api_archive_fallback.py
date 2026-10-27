"""Regression: API archive endpoint offline fallback (S11)."""

from __future__ import annotations

from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.executor.source_patch_codegen import (
    local_fallback_api_archive_endpoint,
)


def test_api_archive_fallback_wires_service_and_route() -> None:
    app_src = (
        "async def app(scope, receive, send):\n"
        "    method = scope['method']\n"
        "    path = scope['path']\n"
        "    if method == 'GET' and path == '/items':\n"
        "        resp = _json_response(200, [])\n"
        "    else:\n"
        "        resp = _json_response(404, {'error': 'not_found'})\n"
    )
    service_src = (
        "class ItemStore:\n"
        "    def get(self, item_id: int):\n"
        "        return None\n"
        "    def list_items(self, *, include_archived: bool = False):\n"
        "        return []\n"
        "\n"
        "STORE = ItemStore()\n"
    )
    report = DiscoveryReport()
    report.config_files = {
        "backend/tests/.../live_fixtures/api/app.py": app_src,
        "backend/tests/.../live_fixtures/api/service.py": service_src,
    }
    result = local_fallback_api_archive_endpoint(
        report,
        "Add archive support to the live-fixture items API endpoint.",
    )
    assert result is not None
    paths = {e.path for e in result.edits}
    assert any(p.endswith("service.py") for p in paths)
    assert any(p.endswith("app.py") for p in paths)
    service_edit = next(e for e in result.edits if e.path.endswith("service.py"))
    app_edit = next(e for e in result.edits if e.path.endswith("app.py"))
    assert "def archive(" in (service_edit.new_content or "")
    assert "/archive" in (app_edit.new_content or "")
    assert "STORE.archive" in (app_edit.new_content or "")
