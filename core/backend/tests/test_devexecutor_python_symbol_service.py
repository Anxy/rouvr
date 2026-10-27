from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.services.devexecutor_pyright_lsp_service import PyrightLspSymbolBounds
from backend.services.devexecutor_python_symbol_service import (
    AmbiguousPythonSymbolError,
    activate_python_symbol_session_cache,
    build_python_symbol_context,
    resolve_python_symbol_range,
    verify_symbol_anchor,
)

pytestmark = pytest.mark.enable_pyright_lsp


def test_resolve_python_symbol_range_module_level_async_function(tmp_path: Path) -> None:
    target = tmp_path / "router.py"
    target.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n\n"
        "@router.get('/demo')\n"
        "async def list_repo_relations(request):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    result = resolve_python_symbol_range(file_path=target, symbol_name="list_repo_relations")
    assert result.symbol_name == "list_repo_relations"
    assert result.qualified_name == "list_repo_relations"
    assert result.start_line == 4
    assert result.def_line == 5
    assert result.end_line == 6
    assert result.decorator_lines == (4,)
    assert "@router.get('/demo')" in result.symbol_source


def test_resolve_python_symbol_range_module_qualified_top_level_function(tmp_path: Path) -> None:
    target = tmp_path / "app" / "routes" / "health.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n\n"
        "@router.get('/live')\n"
        "async def live() -> dict:\n"
        "    return {'status': 'alive'}\n",
        encoding="utf-8",
    )
    result = resolve_python_symbol_range(file_path=target, symbol_name="app.routes.health.live")
    assert result.symbol_name == "live"
    assert result.qualified_name == "live"
    assert result.start_line == 4
    assert result.def_line == 5
    assert result.end_line == 6


def test_resolve_python_symbol_range_class_method(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = resolve_python_symbol_range(file_path=target, symbol_name="CodeintelQueryService.list_relations")
    assert result.qualified_name == "CodeintelQueryService.list_relations"
    assert result.start_line == 2
    assert result.def_line == 2
    assert result.def_col == 4
    assert result.end_line == 3


def test_resolve_python_symbol_range_module_qualified_class_method(tmp_path: Path) -> None:
    target = tmp_path / "backend" / "services" / "workhub_contract_service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "class WorkhubContractService:\n"
        "    async def _create_task_with_conn(self, conn, payload):\n"
        "        return {'ok': True}\n",
        encoding="utf-8",
    )
    result = resolve_python_symbol_range(
        file_path=target,
        symbol_name="backend.services.workhub_contract_service.WorkhubContractService._create_task_with_conn",
    )
    assert result.qualified_name == "WorkhubContractService._create_task_with_conn"
    assert result.start_line == 2
    assert result.def_line == 2
    assert result.end_line == 3


def test_resolve_python_symbol_range_nested_decorated_method(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class Outer:\n"
        "    class Inner:\n"
        "        @staticmethod\n"
        "        def run() -> int:\n"
        "            return 1\n",
        encoding="utf-8",
    )
    result = resolve_python_symbol_range(file_path=target, symbol_name="Inner.run")
    assert result.qualified_name == "Outer.Inner.run"
    assert result.start_line == 3
    assert result.def_line == 4
    assert result.end_line == 5
    assert result.decorator_lines == (3,)


def test_resolve_python_symbol_range_raises_on_missing_symbol(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text("def demo():\n    return 1\n", encoding="utf-8")
    with pytest.raises(LookupError, match="PYTHON_SYMBOL_NOT_FOUND"):
        resolve_python_symbol_range(file_path=target, symbol_name="missing_symbol")


def test_verify_symbol_anchor_matches_normalized_quote(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, relation_type=None):\n"
        "        if relation_type:\n"
        "            return {'items': [relation_type]}\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = verify_symbol_anchor(
        file_path=target,
        symbol_name="CodeintelQueryService.list_relations",
        anchor_quote="async def list_relations(self, tenant_id, project_id, repo_id, relation_type=None): if relation_type:",
    )
    assert result["matched"] is True
    assert result["reason_code"] == "MATCH"
    assert str(result["symbol_source_hash"]).startswith("sha256:")
    assert result["commit_sha"] is None
    assert str(result["file_blob_sha"]).startswith("sha256:")
    assert result["head_file_blob_sha"] is None


def test_verify_symbol_anchor_matches_module_qualified_top_level_function(tmp_path: Path) -> None:
    target = tmp_path / "app" / "routes" / "health.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n\n"
        "@router.get('/live')\n"
        "async def live() -> dict:\n"
        "    return {'status': 'alive'}\n",
        encoding="utf-8",
    )
    result = verify_symbol_anchor(
        file_path=target,
        symbol_name="app.routes.health.live",
        anchor_quote="async def live() -> dict:",
    )
    assert result["matched"] is True
    assert result["qualified_name"] == "live"
    assert str(result["symbol_source_hash"]).startswith("sha256:")
    assert str(result["file_blob_sha"]).startswith("sha256:")


def test_verify_symbol_anchor_matches_nested_decorated_method(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class Outer:\n"
        "    class Inner:\n"
        "        @staticmethod\n"
        "        def run() -> int:\n"
        "            return 1\n",
        encoding="utf-8",
    )
    result = verify_symbol_anchor(
        file_path=target,
        symbol_name="Inner.run",
        anchor_quote="def run() -> int:",
    )
    assert result["matched"] is True
    assert result["qualified_name"] == "Outer.Inner.run"


def test_verify_symbol_anchor_matches_module_qualified_class_method(tmp_path: Path) -> None:
    target = tmp_path / "backend" / "services" / "workhub_contract_service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "class WorkhubContractService:\n"
        "    async def _create_task_with_conn(self, conn, payload):\n"
        "        return {'ok': True}\n",
        encoding="utf-8",
    )
    result = verify_symbol_anchor(
        file_path=target,
        symbol_name="backend.services.workhub_contract_service.WorkhubContractService._create_task_with_conn",
        anchor_quote="async def _create_task_with_conn(self, conn, payload):",
    )
    assert result["matched"] is True
    assert result["qualified_name"] == "WorkhubContractService._create_task_with_conn"


def test_resolve_python_symbol_range_raises_on_ambiguous_symbol(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class First:\n"
        "    def same(self):\n"
        "        return 1\n\n"
        "class Second:\n"
        "    def same(self):\n"
        "        return 2\n",
        encoding="utf-8",
    )
    with pytest.raises(AmbiguousPythonSymbolError, match="PYTHON_SYMBOL_AMBIGUOUS"):
        resolve_python_symbol_range(file_path=target, symbol_name="same")


def test_build_python_symbol_context_includes_imports_and_class_init(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "from dataclasses import dataclass\n\n"
        "class CodeintelQueryService:\n"
        "    def __init__(self, dsn: str) -> None:\n"
        "        self._dsn = dsn\n\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    with activate_python_symbol_session_cache():
        context = build_python_symbol_context(file_path=target, symbol_name="CodeintelQueryService.list_relations")
    assert "async def list_relations" in context.symbol_excerpt
    assert "from dataclasses import dataclass" in context.imports_excerpt
    assert "def __init__(self, dsn: str)" in context.class_init_excerpt


def test_symbol_session_cache_reuses_parsed_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    read_count = 0
    original_read_text = Path.read_text

    def counting_read_text(self: Path, *args, **kwargs):
        nonlocal read_count
        if self == target:
            read_count += 1
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    monkeypatch.setattr(
        "backend.services.devexecutor_python_symbol_service.resolve_python_symbol_bounds_via_lsp",
        lambda **_kwargs: None,
    )
    with activate_python_symbol_session_cache():
        resolve_python_symbol_range(file_path=target, symbol_name="CodeintelQueryService.list_relations")
        build_python_symbol_context(file_path=target, symbol_name="CodeintelQueryService.list_relations")
    assert read_count == 1


def test_resolve_python_symbol_range_uses_tree_sitter_without_ast_parse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )

    def fail_ast_parse(*_args, **_kwargs):
        raise AssertionError("ast.parse should not be used when tree-sitter is available")

    monkeypatch.setattr(ast, "parse", fail_ast_parse)

    result = resolve_python_symbol_range(file_path=target, symbol_name="CodeintelQueryService.list_relations")

    assert result.qualified_name == "CodeintelQueryService.list_relations"
    assert result.start_line == 2


def test_resolve_python_symbol_range_prefers_lsp_bounds_when_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class Demo:\n" "    def run(self) -> int:\n" "        return 1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "backend.services.devexecutor_python_symbol_service.resolve_python_symbol_bounds_via_lsp",
        lambda **_kwargs: PyrightLspSymbolBounds(
            symbol_name="run",
            qualified_name="Demo.run",
            symbol_type="method",
            start_line=2,
            def_line=2,
            def_col=4,
            end_line=3,
            symbol_source="def run(self) -> int:\n    return 1",
            semantic_valid=True,
            diagnostics=(),
        ),
    )

    result = resolve_python_symbol_range(file_path=target, symbol_name="Demo.run")

    assert result.qualified_name == "Demo.run"
    assert result.resolution_source == "pyright_lsp"
    assert result.semantic_valid is True


def test_resolve_python_symbol_range_logs_fallback_when_lsp_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class Demo:\n" "    def run(self) -> int:\n" "        return 1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "backend.services.devexecutor_python_symbol_service.resolve_python_symbol_bounds_via_lsp",
        lambda **_kwargs: None,
    )

    with caplog.at_level("WARNING"):
        result = resolve_python_symbol_range(file_path=target, symbol_name="Demo.run")

    assert result.resolution_source in {"tree_sitter", "ast"}
    assert "falling back to tree-sitter/AST symbol resolution" in caplog.text
