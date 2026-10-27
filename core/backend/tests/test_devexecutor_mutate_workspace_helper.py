from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import py_compile
import subprocess
import sys
from pathlib import Path


def _load_mutate_workspace_module():
    module_path = Path(__file__).resolve().parents[1] / "docker" / "devexecutor-runtime" / "mutate_workspace.py"
    spec = importlib.util.spec_from_file_location("devexecutor_mutate_workspace_helper", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load mutate_workspace module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)


def test_mutate_workspace_run_git_marks_repo_as_safe(monkeypatch) -> None:
    module = _load_mutate_workspace_module()
    captured: dict[str, object] = {}
    repo_root = Path("/workspace/hyeve")

    class FakeCompleted:
        returncode = 0
        stdout = "abc123\n"
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        return FakeCompleted()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = module._run_git(repo_root=repo_root, args=["rev-parse", "HEAD"])

    assert result == "abc123"
    assert captured["command"] == [
        "git",
        "-c",
        f"safe.directory={repo_root}",
        "-C",
        str(repo_root),
        "rev-parse",
        "HEAD",
    ]


def test_mutate_workspace_run_git_returns_none_on_git_failure(monkeypatch) -> None:
    module = _load_mutate_workspace_module()

    class FakeCompleted:
        returncode = 128
        stdout = ""
        stderr = "fatal: detected dubious ownership"

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: FakeCompleted())

    result = module._run_git(repo_root=Path("/workspace/hyeve"), args=["rev-parse", "HEAD"])

    assert result is None


def test_mutate_workspace_replace_in_function_dry_run_does_not_mutate_file(tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    target = tmp_path / "service.py"
    original = (
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n"
    )
    target.write_text(original, encoding="utf-8")

    result = module._replace_in_function(
        workspace_root=str(tmp_path),
        relative_path="service.py",
        function_name="CodeintelQueryService.list_relations",
        replacement=(
            "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
            "        return {'items': [entity_id] if entity_id else []}\n"
        ),
        dry_run=True,
    )

    assert result["operation"] == "replace_in_function"
    assert result["dry_run"] is True
    assert result["validated"] is True
    assert result["changed_files"] == []
    assert target.read_text(encoding="utf-8") == original


def test_mutate_workspace_replace_in_function_preserves_decorators(tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    target = tmp_path / "router.py"
    target.write_text(
        "from fastapi import APIRouter, Query\n"
        "router = APIRouter()\n\n"
        "@router.get('/file')\n"
        "def read_workspace_file(path: str = Query(...)) -> dict:\n"
        "    return {'path': path}\n",
        encoding="utf-8",
    )

    module._replace_in_function(
        workspace_root=str(tmp_path),
        relative_path="router.py",
        function_name="read_workspace_file",
        replacement=(
            "def read_workspace_file(path: str = Query(...), start_line: int = 1) -> dict:\n"
            "    return {'path': path, 'start_line': start_line}\n"
        ),
    )

    text = target.read_text(encoding="utf-8")
    assert "@router.get('/file')" in text
    assert "start_line: int = 1" in text


def test_mutate_workspace_replace_in_function_uses_injected_base_commit_when_git_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    module = _load_mutate_workspace_module()
    target = tmp_path / "wh.py"
    original = "def _route_run() -> str:\n" "    return 'old'\n"
    target.write_text(original, encoding="utf-8")
    monkeypatch.setenv("AIX_WORKSPACE_BASE_COMMIT_SHA", "abc123base")
    monkeypatch.setattr(module, "_git_grounding_for_path", lambda **_kwargs: (None, None))

    result = module._replace_in_function(
        workspace_root=str(tmp_path),
        relative_path="wh.py",
        function_name="_route_run",
        replacement=("def _route_run() -> str:\n" "    return 'new'\n"),
        expected_base_commit_sha="abc123base",
        dry_run=True,
    )

    assert result["operation"] == "replace_in_function"
    assert result["dry_run"] is True
    assert result["grounded_symbol"]["commit_sha"] == "abc123base"
    assert result["grounded_symbol"]["diagnostic_git_commit_sha"] is None


def test_mutate_workspace_replace_in_function_rejects_async_mismatch(tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id):\n"
        "        return {'items': [tenant_id]}\n",
        encoding="utf-8",
    )

    try:
        module._replace_in_function(
            workspace_root=str(tmp_path),
            relative_path="service.py",
            function_name="CodeintelQueryService.list_relations",
            replacement="def list_relations(self, tenant_id):\n    return {'items': []}\n",
            dry_run=True,
        )
    except ValueError as exc:
        assert "MUTATION_INVALID_FUNCTION_SHAPE async_mismatch" in str(exc)
    else:
        raise AssertionError("expected async_mismatch")


def test_mutate_workspace_replace_in_function_rejects_symbol_name_mismatch(tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id):\n"
        "        return {'items': [tenant_id]}\n",
        encoding="utf-8",
    )

    try:
        module._replace_in_function(
            workspace_root=str(tmp_path),
            relative_path="service.py",
            function_name="CodeintelQueryService.list_relations",
            replacement="async def other_name(self, tenant_id):\n    return {'items': []}\n",
            dry_run=True,
        )
    except ValueError as exc:
        assert "MUTATION_INVALID_FUNCTION_SHAPE symbol_name_mismatch" in str(exc)
    else:
        raise AssertionError("expected symbol_name_mismatch")


def test_mutate_workspace_main_dispatches_replace_in_function(tmp_path: Path, monkeypatch) -> None:
    module = _load_mutate_workspace_module()
    captured: dict[str, object] = {}

    def fake_replace_in_function(**kwargs):
        captured.update(kwargs)
        return {"operation": "replace_in_function", "changed_files": ["service.py"]}

    monkeypatch.setattr(module, "_replace_in_function", fake_replace_in_function)
    monkeypatch.setattr(module, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "tool": "replace_in_function",
                    "args": {
                        "relative_path": "service.py",
                        "function_name": "DemoService.list_items",
                        "replacement": "def list_items(self):\n    return ['demo']\n",
                        "dry_run": True,
                        "expected_base_commit_sha": "base123",
                        "expected_file_blob_sha": "blob123",
                        "expected_symbol_source_hash": "sha256:symbol123",
                    },
                }
            )
        ),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    with contextlib.redirect_stdout(stdout):
        exit_code = module.main()

    assert exit_code == 0
    assert captured == {
        "workspace_root": str(tmp_path),
        "relative_path": "service.py",
        "function_name": "DemoService.list_items",
        "replacement": "def list_items(self):\n    return ['demo']\n",
        "dry_run": True,
        "expected_base_commit_sha": "base123",
        "expected_file_blob_sha": "blob123",
        "expected_symbol_source_hash": "sha256:symbol123",
    }
    assert json.loads(stdout.getvalue()) == {"operation": "replace_in_function", "changed_files": ["service.py"]}


def test_mutate_workspace_symbol_resolution_uses_tree_sitter_without_ast_parse(monkeypatch, tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )

    def fail_ast_parse(*_args, **_kwargs):
        raise AssertionError("ast.parse should not be used for symbol lookup when tree-sitter is available")

    monkeypatch.setattr(module.ast, "parse", fail_ast_parse)

    start_line, end_line, def_col, qualified_name, symbol_source = module._resolve_python_symbol_range(
        file_path=target,
        symbol_name="CodeintelQueryService.list_relations",
    )

    assert start_line == 2
    assert end_line == 3
    assert def_col == 4
    assert qualified_name == "CodeintelQueryService.list_relations"
    assert "async def list_relations" in symbol_source


def test_mutate_workspace_edit_file_applies_search_replace_blocks(tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    _init_git_repo(tmp_path)
    target = tmp_path / "demo.py"
    target.write_text("def demo():\n    return 'old'\n", encoding="utf-8")

    result = module._edit_file(
        workspace_root=str(tmp_path),
        relative_path="demo.py",
        search_replace_blocks="<<<<<<< SEARCH\n    return 'old'\n=======\n    return 'new'\n>>>>>>> REPLACE\n",
    )

    assert result["operation"] == "edit_file"
    assert result["changed_files"] == ["demo.py"]
    assert "return 'new'" in target.read_text(encoding="utf-8")


def test_mutate_workspace_edit_file_rejects_ambiguous_search(tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    _init_git_repo(tmp_path)
    target = tmp_path / "demo.py"
    target.write_text("value = 1\nvalue = 1\n", encoding="utf-8")

    try:
        module._edit_file(
            workspace_root=str(tmp_path),
            relative_path="demo.py",
            search_replace_blocks="<<<<<<< SEARCH\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n",
        )
    except ValueError as exc:
        assert "SEARCH_REPLACE_AMBIGUOUS" in str(exc)
    else:
        raise AssertionError("expected SEARCH_REPLACE_AMBIGUOUS")


def test_mutate_workspace_edit_file_matches_without_trailing_newline(tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    _init_git_repo(tmp_path)
    target = tmp_path / "demo.py"
    target.write_text("value = 1", encoding="utf-8")

    result = module._edit_file(
        workspace_root=str(tmp_path),
        relative_path="demo.py",
        search_replace_blocks="<<<<<<< SEARCH\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n",
    )

    assert result["operation"] == "edit_file"
    assert result["adapter"] == "write_file_fallback"
    assert result["fallback_reason"] == "missing_terminal_newline"
    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_mutate_workspace_edit_file_rejects_mid_line_substring_match(tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    _init_git_repo(tmp_path)
    target = tmp_path / "demo.py"
    target.write_text("def f():\n    return 1\n", encoding="utf-8")

    try:
        module._edit_file(
            workspace_root=str(tmp_path),
            relative_path="demo.py",
            search_replace_blocks="<<<<<<< SEARCH\n  return 1\n=======\n  return 2\n>>>>>>> REPLACE\n",
        )
    except ValueError as exc:
        assert "SEARCH_REPLACE_NOT_FOUND" in str(exc)
    else:
        raise AssertionError("expected SEARCH_REPLACE_NOT_FOUND")


def test_mutate_workspace_edit_file_accepts_fuzzy_whitespace_match(tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    _init_git_repo(tmp_path)
    target = tmp_path / "demo.py"
    target.write_text("def demo() -> str:\n    if True:\n        return 'value'\n", encoding="utf-8")

    result = module._edit_file(
        workspace_root=str(tmp_path),
        relative_path="demo.py",
        search_replace_blocks=(
            "<<<<<<< SEARCH\ndef demo() -> str:\n    if True:\n    return 'value'\n=======\ndef demo() -> str:\n    if True:\n        return 'updated'\n>>>>>>> REPLACE\n"
        ),
    )

    assert result["operation"] == "edit_file"
    assert "return 'updated'" in target.read_text(encoding="utf-8")


def test_mutate_workspace_insert_at_anchor_inserts_after_anchor(tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    _init_git_repo(tmp_path)
    target = tmp_path / "demo.py"
    target.write_text("from pathlib import Path\n\nvalue = 1\n", encoding="utf-8")

    result = module._insert_at_anchor(
        workspace_root=str(tmp_path),
        relative_path="demo.py",
        anchor_text="from pathlib import Path\n",
        new_content="from typing import Any\n",
    )

    assert result["operation"] == "insert_at_anchor"
    assert "from typing import Any" in target.read_text(encoding="utf-8")


def test_mutate_workspace_edit_file_uses_git_apply_engine(monkeypatch, tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    _init_git_repo(tmp_path)
    target = tmp_path / "demo.py"
    target.write_text("value = 1\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_apply_patch(**kwargs):
        captured.update(kwargs)
        target.write_text("value = 2\n", encoding="utf-8")
        return {
            "operation": "apply_patch",
            "patch_attempt": 1,
            "patch_bytes": 42,
            "patch_sha1": "abc123",
            "approx_diff_bytes": 12,
            "changed_files": ["demo.py"],
            "retry_count": 0,
            "retry_strategy": "apply_then_3way",
            "adapter": "git_apply",
            "adapter_chain": [{"name": "git_apply", "available": True}],
        }

    monkeypatch.setattr(module, "_apply_patch", fake_apply_patch)

    result = module._edit_file(
        workspace_root=str(tmp_path),
        relative_path="demo.py",
        search_replace_blocks="<<<<<<< SEARCH\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n",
    )

    assert result["operation"] == "edit_file"
    assert result["adapter"] == "git_apply"
    assert "--- a/demo.py" in str(captured["patch_text"])
    assert "+++ b/demo.py" in str(captured["patch_text"])


def test_mutate_workspace_extend_python_signature_updates_method_signature(tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    target = tmp_path / "service.py"
    target.write_text(
        "class WorkhubContractService:\n"
        "    async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n",
        encoding="utf-8",
    )

    result = module._extend_python_signature(
        workspace_root=str(tmp_path),
        relative_path="service.py",
        symbol="WorkhubContractService.mutate_task",
        parameter_name="target_project_id",
        parameter_type="str | None",
        default_value="None",
        parameter_kind="auto",
    )

    assert result["operation"] == "extend_python_signature"
    assert result["changed_files"] == ["service.py"]
    assert "target_project_id: str | None = None" in target.read_text(encoding="utf-8")


def test_mutate_workspace_extend_python_signature_accepts_optional_parameter_kind(tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    target = tmp_path / "service.py"
    target.write_text(
        "class WorkhubContractService:\n"
        "    async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n",
        encoding="utf-8",
    )

    result = module._extend_python_signature(
        workspace_root=str(tmp_path),
        relative_path="service.py",
        symbol="WorkhubContractService.mutate_task",
        parameter_name="target_project_id",
        parameter_type="str | None",
        default_value="None",
        parameter_kind="optional",
    )

    assert result["operation"] == "extend_python_signature"
    assert result["parameter_kind"] == "keyword_only"
    text = target.read_text(encoding="utf-8")
    assert "target_project_id: str | None = None" in text
    py_compile.compile(str(target), doraise=True)


def test_mutate_workspace_extend_python_signature_preserves_multiline_keyword_only_signature(tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    target = tmp_path / "service.py"
    target.write_text(
        "class WorkhubContractService:\n"
        "    async def mutate_task(\n"
        "        self,\n"
        "        *,\n"
        "        tenant_id: str,\n"
        "        project_id: str,\n"
        "        task_id: str,\n"
        "        reason: str | None = None,\n"
        "    ) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n",
        encoding="utf-8",
    )

    result = module._extend_python_signature(
        workspace_root=str(tmp_path),
        relative_path="service.py",
        symbol="WorkhubContractService.mutate_task",
        parameter_name="target_project_id",
        parameter_type="str | None",
        default_value="None",
        parameter_kind="optional",
    )

    text = target.read_text(encoding="utf-8")
    assert result["operation"] == "extend_python_signature"
    assert "target_project_id: str | None = None" in text
    py_compile.compile(str(target), doraise=True)


def test_mutate_workspace_apply_patch_retries_with_3way(monkeypatch, tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    commands: list[list[str]] = []

    class FakeCompleted:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        if len(commands) == 1:
            return FakeCompleted(1, stderr="error: patch failed: demo.py:1")
        return FakeCompleted(0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = module._apply_patch(
        workspace_root=str(tmp_path),
        patch_text="--- a/demo.py\n+++ b/demo.py\n@@ -1 +1 @@\n-old\n+new\n",
    )

    assert result["operation"] == "apply_patch"
    assert result["retry_count"] == 1
    assert "--3way" not in commands[0]
    assert "--3way" in commands[1]
    assert "--ignore-space-change" in commands[0]
    assert "--ignore-whitespace" in commands[0]


def test_mutate_workspace_insert_before_return_is_removed(tmp_path: Path) -> None:
    module = _load_mutate_workspace_module()
    target = tmp_path / "wh.py"
    original = "def _run_fire_promotion_preflight() -> dict:\n" "    value = {'ok': True}\n" "    return value\n"
    target.write_text(
        original,
        encoding="utf-8",
    )

    try:
        module._insert_before_return(
            workspace_root=str(tmp_path),
            relative_path="wh.py",
            function_name="scripts.wh._run_fire_promotion_preflight",
            new_content="    value['ready'] = True\n",
        )
    except RuntimeError as exc:
        assert "REMOVED_MUTATION_TOOL primitive=insert_before_return" in str(exc)
    else:
        raise AssertionError("expected REMOVED_MUTATION_TOOL")
    assert target.read_text(encoding="utf-8") == original
