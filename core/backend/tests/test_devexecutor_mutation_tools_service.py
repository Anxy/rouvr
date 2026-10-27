from __future__ import annotations

import ast
import json
import py_compile
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.services.devexecutor_mutation_runtime_service import MutationRuntimeError
from backend.services.devexecutor_mutation_tools_service import (
    MutationDivergenceError,
    apply_patch,
    apply_staged_delivery_slice,
    edit_file,
    edit_symbol,
    extend_python_signature,
    insert_after_symbol,
    insert_at_anchor,
    insert_before_return,
    replace_function,
    replace_in_function,
    replace_range,
    update_call_kwarg,
    validate_replace_function_candidate,
    write_file,
)
from backend.services.devexecutor_python_symbol_service import verify_symbol_anchor
from backend.services.devexecutor_readonly_tools_service import WorkspaceSecurityError


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)


def test_write_file_writes_and_emits_policy_hooks(tmp_path: Path) -> None:
    events: list[tuple[str, dict]] = []

    def hook(evt: str, payload: dict) -> None:
        events.append((evt, payload))

    result = write_file(
        workspace_root=tmp_path,
        relative_path="src/demo.txt",
        content="hello\n",
        policy_hook=hook,
    )
    assert (tmp_path / "src" / "demo.txt").read_text(encoding="utf-8") == "hello\n"
    assert result["operation"] == "write_file"
    assert result["atomic_write"] is True
    assert result["relative_path"] == "src/demo.txt"
    assert result["changed_files"] == ["src/demo.txt"]
    assert "formatter" in result
    assert events[0][0] == "pre_write_file"
    assert events[-1][0] == "post_write_file"


def test_write_file_blocks_workspace_escape(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceSecurityError):
        write_file(workspace_root=tmp_path, relative_path="../escape.txt", content="x")


def test_write_file_returns_already_applied_when_content_matches(tmp_path: Path) -> None:
    target = tmp_path / "src" / "demo.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"hello\n")

    result = write_file(
        workspace_root=tmp_path,
        relative_path="src/demo.txt",
        content="hello\n",
    )

    assert result["code"] == "ALREADY_APPLIED"
    assert result["applied"] is False
    assert result["skipped"] is True
    assert result["changed_files"] == []


def test_apply_patch_enforces_attempt_budget(tmp_path: Path) -> None:
    tracker = {"patch_attempts": 1}
    with pytest.raises(RuntimeError, match="PATCH_ATTEMPT_LIMIT_EXCEEDED"):
        apply_patch(
            workspace_root=tmp_path,
            patch_text="",
            attempt_tracker=tracker,
            max_attempts=1,
        )


def test_apply_patch_succeeds_on_git_repo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    file_path = tmp_path / "demo.txt"
    file_path.write_text("hello\n", encoding="utf-8")
    patch = """diff --git a/demo.txt b/demo.txt
index ce01362..94954ab 100644
--- a/demo.txt
+++ b/demo.txt
@@ -1 +1 @@
-hello
+hello world
"""
    events: list[str] = []

    def hook(evt: str, _payload: dict) -> None:
        events.append(evt)

    monkeypatch.setattr(
        "backend.services.devexecutor_patch_engine_service._run_pyright_on_files",
        lambda **_kwargs: (True, ""),
    )
    result = apply_patch(workspace_root=tmp_path, patch_text=patch, policy_hook=hook)
    assert "post_apply_patch" in events
    assert result["operation"] == "apply_patch"
    assert "demo.txt" in result["changed_files"]
    assert result["retry_count"] >= 0
    assert result["retry_strategy"] in {"single_apply", "apply_then_3way"}
    assert result["adapter"] == "git_apply"
    assert isinstance(result["adapter_chain"], list)
    assert file_path.read_text(encoding="utf-8") == "hello world\n"


def test_apply_patch_bad_patch_raises(tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init"], check=True, capture_output=True)
    bad_patch = "diff --git a/missing.txt b/missing.txt\n--- a/missing.txt\n+++ b/missing.txt\n@@ -1 +1 @@\n-a\n+b\n"
    with pytest.raises(RuntimeError, match="PATCH_APPLY_FAILED"):
        apply_patch(workspace_root=tmp_path, patch_text=bad_patch)


def test_replace_range_replaces_line_span_atomically(tmp_path: Path) -> None:
    path = tmp_path / "demo.txt"
    path.write_text("a\nb\nc\nd\n", encoding="utf-8")
    result = replace_range(
        workspace_root=tmp_path,
        relative_path="demo.txt",
        start_line=2,
        end_line=3,
        replacement="x\ny",
    )
    assert result["operation"] == "replace_range"
    assert result["changed_files"] == ["demo.txt"]
    assert path.read_text(encoding="utf-8") == "a\nx\ny\nd\n"


def test_edit_file_applies_search_replace_blocks_atomically(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    path = tmp_path / "demo.py"
    path.write_text("def demo():\n    return 'old'\n", encoding="utf-8")

    monkeypatch.setattr(
        "backend.services.devexecutor_patch_engine_service._run_pyright_on_files",
        lambda **_kwargs: (True, ""),
    )
    result = edit_file(
        workspace_root=tmp_path,
        relative_path="demo.py",
        search_replace_blocks="<<<<<<< SEARCH\n    return 'old'\n=======\n    return 'new'\n>>>>>>> REPLACE\n",
    )

    assert result["operation"] == "edit_file"
    assert result["block_count"] == 1
    assert result["changed_files"] == ["demo.py"]
    assert path.read_text(encoding="utf-8") == "def demo():\n    return 'new'\n"


def test_edit_file_rejects_ambiguous_search_match(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    path = tmp_path / "demo.py"
    path.write_text("value = 1\nvalue = 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SEARCH_REPLACE_AMBIGUOUS"):
        edit_file(
            workspace_root=tmp_path,
            relative_path="demo.py",
            search_replace_blocks="<<<<<<< SEARCH\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n",
        )


def test_edit_file_returns_already_applied_for_no_op_block(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    path = tmp_path / "demo.py"
    path.write_text("value = 1\n", encoding="utf-8")

    result = edit_file(
        workspace_root=tmp_path,
        relative_path="demo.py",
        search_replace_blocks="<<<<<<< SEARCH\nvalue = 1\n=======\nvalue = 1\n>>>>>>> REPLACE\n",
    )

    assert result["code"] == "ALREADY_APPLIED"
    assert result["changed_files"] == []
    assert result["skipped"] is True


def test_edit_file_matches_file_without_trailing_newline(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    path = tmp_path / "demo.py"
    path.write_text("value = 1", encoding="utf-8")
    execution_context: dict[str, Any] = {"ayrpack": {"spine": []}}

    result = edit_file(
        workspace_root=tmp_path,
        relative_path="demo.py",
        search_replace_blocks="<<<<<<< SEARCH\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n",
        execution_context=execution_context,
    )

    assert result["operation"] == "edit_file"
    assert result["adapter"] == "write_file_fallback"
    assert result["fallback_reason"] == "missing_terminal_newline"
    assert path.read_text(encoding="utf-8") == "value = 2\n"
    assert execution_context["ayrpack"]["spine"][-1]["kind"] == "decision"
    assert execution_context["ayrpack"]["spine"][-1]["summary"] == "fallback edit primitive used"
    assert "missing_terminal_newline" in execution_context["ayrpack"]["spine"][-1]["title"]


def test_edit_file_rejects_mid_line_substring_match(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    path = tmp_path / "demo.py"
    path.write_text("def f():\n    return 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="SEARCH_REPLACE_NOT_FOUND"):
        edit_file(
            workspace_root=tmp_path,
            relative_path="demo.py",
            search_replace_blocks="<<<<<<< SEARCH\n  return 1\n=======\n  return 2\n>>>>>>> REPLACE\n",
        )


def test_edit_file_accepts_fuzzy_whitespace_match(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    path = tmp_path / "demo.py"
    path.write_text("def demo() -> str:\n    if True:\n        return 'value'\n", encoding="utf-8")

    result = edit_file(
        workspace_root=tmp_path,
        relative_path="demo.py",
        search_replace_blocks=(
            "<<<<<<< SEARCH\ndef demo() -> str:\n    if True:\n    return 'value'\n=======\ndef demo() -> str:\n    if True:\n        return 'updated'\n>>>>>>> REPLACE\n"
        ),
    )

    assert result["operation"] == "edit_file"
    assert result["blocks"][0]["match_mode"] == "fuzzy_whitespace"
    assert "return 'updated'" in path.read_text(encoding="utf-8")


def test_edit_file_accepts_confirmed_symbol_style_deindented_header_match(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    path = tmp_path / "demo.py"
    path.write_text(
        "class ExampleService:\n"
        "    async def mutate_task(self, task_id: str) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n",
        encoding="utf-8",
    )

    result = edit_file(
        workspace_root=tmp_path,
        relative_path="demo.py",
        search_replace_blocks=(
            "<<<<<<< SEARCH\n"
            "async def mutate_task(self, task_id: str) -> dict[str, str]:\n"
            "=======\n"
            "async def mutate_task(self, task_id: str, target_project_id: str | None = None) -> dict[str, str]:\n"
            ">>>>>>> REPLACE\n"
        ),
    )

    assert result["operation"] == "edit_file"
    assert result["blocks"][0]["match_mode"] == "normalized_indentation"
    assert "    async def mutate_task(self, task_id: str, target_project_id: str | None = None)" in path.read_text(
        encoding="utf-8"
    )


def test_insert_at_anchor_inserts_after_unique_anchor(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    path = tmp_path / "demo.py"
    path.write_text("from pathlib import Path\n\nvalue = 1\n", encoding="utf-8")

    result = insert_at_anchor(
        workspace_root=tmp_path,
        relative_path="demo.py",
        anchor_text="from pathlib import Path\n",
        new_content="from typing import Any\n",
    )

    assert result["operation"] == "insert_at_anchor"
    assert result["changed_files"] == ["demo.py"]
    assert path.read_text(encoding="utf-8").startswith("from pathlib import Path\nfrom typing import Any\n")


def test_insert_at_anchor_matches_anchor_indentation(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    path = tmp_path / "demo.py"
    path.write_text("def demo():\n    value = 1\n    return value\n", encoding="utf-8")

    insert_at_anchor(
        workspace_root=tmp_path,
        relative_path="demo.py",
        anchor_text="    value = 1\n",
        new_content="extra = 2\n",
    )

    assert "    extra = 2\n" in path.read_text(encoding="utf-8")


def test_insert_at_anchor_surfaces_pyright_precheck_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    path = tmp_path / "demo.py"
    path.write_text("value = 1\n", encoding="utf-8")

    class _FailedResult:
        status = "failed"
        reason_code = "pyright_precheck_failed"
        patch_attempt = 1
        stderr = ""
        stdout = ""
        semantic_precheck_output = "demo.py:1:7 - error: NameError"

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_tools_service.apply_patch_with_outcome",
        lambda **_kwargs: _FailedResult(),
    )

    with pytest.raises(RuntimeError, match="demo.py:1:7 - error: NameError"):
        insert_at_anchor(
            workspace_root=tmp_path,
            relative_path="demo.py",
            anchor_text="value = 1\n",
            new_content="broken = missing_name\n",
        )


def test_edit_file_uses_container_runtime_when_required(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_execute_mutation_in_runtime(**kwargs):
        captured.update(kwargs)
        return {"operation": "edit_file", "changed_files": ["demo.py"], "atomic_write": True}

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_tools_service.execute_mutation_in_runtime",
        fake_execute_mutation_in_runtime,
    )

    result = edit_file(
        workspace_root=tmp_path,
        relative_path="demo.py",
        search_replace_blocks="<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE\n",
        runtime={"mode": "docker", "container_required": True, "image": "runtime:latest"},
    )

    assert result["operation"] == "edit_file"
    assert captured["tool"] == "edit_file"


def test_edit_file_routes_through_git_apply_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    path = tmp_path / "demo.py"
    path.write_text("value = 1\n", encoding="utf-8")
    captured: dict[str, object] = {}

    class _Result:
        status = "applied"
        reason_code = "ok"
        patch_sha1 = "abc123"
        changed_files = ["demo.py"]
        patch_bytes = 42
        approx_diff_bytes = 12
        patch_attempt = 1
        policy_violations: list[str] = []
        retry_count = 0
        retry_strategy = "apply_then_3way"
        stderr = ""
        stdout = ""

    def fake_apply_patch_with_outcome(**kwargs):
        captured.update(kwargs)
        path.write_text("value = 2\n", encoding="utf-8")
        return _Result()

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_tools_service.apply_patch_with_outcome",
        fake_apply_patch_with_outcome,
    )

    result = edit_file(
        workspace_root=tmp_path,
        relative_path="demo.py",
        search_replace_blocks="<<<<<<< SEARCH\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n",
    )

    assert result["operation"] == "edit_file"
    assert result["adapter"] == "git_apply"
    assert "--- a/demo.py" in str(captured["patch_text"])
    assert "+++ b/demo.py" in str(captured["patch_text"])
    assert path.read_text(encoding="utf-8") == "value = 2\n"


def test_apply_patch_fallback_to_replace_range_when_enabled(tmp_path: Path) -> None:
    path = tmp_path / "demo.txt"
    path.write_text("line1\nline2\nline3\n", encoding="utf-8")
    bad_patch = "diff --git a/missing.txt b/missing.txt\n--- a/missing.txt\n+++ b/missing.txt\n@@ -1 +1 @@\n-a\n+b\n"
    execution_context: dict[str, Any] = {"ayrpack": {"spine": []}}
    result = apply_patch(
        workspace_root=tmp_path,
        patch_text=bad_patch,
        allow_replace_range_fallback=True,
        execution_context=execution_context,
        fallback_replace={
            "relative_path": "demo.txt",
            "start_line": 2,
            "end_line": 2,
            "replacement": "line2-updated",
        },
    )
    assert result["operation"] == "replace_range"
    assert result["fallback"]["from"] == "apply_patch"
    assert "reason_code" in result["fallback"]
    assert path.read_text(encoding="utf-8") == "line1\nline2-updated\nline3\n"
    assert execution_context["ayrpack"]["spine"][-1]["kind"] == "decision"
    assert execution_context["ayrpack"]["spine"][-1]["summary"] == "fallback edit primitive used"
    assert "replace_range" in execution_context["ayrpack"]["spine"][-1]["title"]


def test_write_file_uses_container_runtime_when_required(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_execute_mutation_in_runtime(**kwargs):
        captured.update(kwargs)
        return {"operation": "write_file", "changed_files": ["src/demo.txt"], "atomic_write": True}

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_tools_service.execute_mutation_in_runtime",
        fake_execute_mutation_in_runtime,
    )
    result = write_file(
        workspace_root=tmp_path,
        relative_path="src/demo.txt",
        content="hello\n",
        runtime={"mode": "docker", "container_required": True, "image": "runtime:latest"},
    )
    assert result["operation"] == "write_file"
    assert captured["tool"] == "write_file"
    assert captured["workspace_root"] == tmp_path


def test_apply_patch_container_runtime_required_rejects_host(tmp_path: Path) -> None:
    with pytest.raises(MutationRuntimeError, match="CONTAINER_MUTATION_RUNTIME_REQUIRED"):
        apply_patch(
            workspace_root=tmp_path,
            patch_text="diff --git a/a.txt b/a.txt\n",
            runtime={"mode": "host", "container_required": True},
        )


def test_replace_in_function_replaces_symbol_block(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = replace_in_function(
        workspace_root=tmp_path,
        relative_path="service.py",
        function_name="CodeintelQueryService.list_relations",
        replacement=(
            "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
            "        if entity_id:\n"
            "            return {'items': [entity_id]}\n"
            "        return {'items': []}\n"
        ),
    )
    assert result["operation"] == "replace_in_function"
    assert result["symbol_range"]["qualified_name"] == "CodeintelQueryService.list_relations"
    assert result["symbol_range"]["def_col"] == 4
    assert str(result["grounded_symbol"]["symbol_source_hash"]).startswith("sha256:")
    assert "\n    async def list_relations" in target.read_text(encoding="utf-8")
    assert "if entity_id" in target.read_text(encoding="utf-8")


def test_replace_function_replaces_symbol_block_without_fallback_gate(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = replace_function(
        workspace_root=tmp_path,
        relative_path="service.py",
        qualified_symbol="CodeintelQueryService.list_relations",
        replacement=(
            "async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
            "    if entity_id:\n"
            "        return {'items': [entity_id]}\n"
            "    return {'items': []}\n"
        ),
    )
    assert result["operation"] == "replace_function"
    assert result["symbol_range"]["qualified_name"] == "CodeintelQueryService.list_relations"
    assert "if entity_id" in target.read_text(encoding="utf-8")


def test_replace_function_uses_runtime_replace_in_function_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_execute_mutation_in_runtime(**kwargs):
        captured.update(kwargs)
        return {
            "operation": "replace_function",
            "relative_path": "service.py",
            "symbol_name": "CodeintelQueryService.list_relations",
            "changed_files": ["service.py"],
        }

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_tools_service.execute_mutation_in_runtime",
        fake_execute_mutation_in_runtime,
    )

    result = replace_function(
        workspace_root=tmp_path,
        relative_path="service.py",
        qualified_symbol="CodeintelQueryService.list_relations",
        replacement=(
            "async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
            "    return {'items': [entity_id] if entity_id else []}\n"
        ),
        runtime={"mode": "docker", "container_required": True, "image": "runtime:latest"},
    )

    assert result["operation"] == "replace_function"
    assert captured["tool"] == "replace_in_function"
    assert captured["args"] == {
        "relative_path": "service.py",
        "function_name": "CodeintelQueryService.list_relations",
        "replacement": (
            "async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
            "    return {'items': [entity_id] if entity_id else []}\n"
        ),
        "dry_run": False,
        "expected_base_commit_sha": None,
        "expected_file_blob_sha": None,
        "expected_symbol_source_hash": None,
    }


def test_replace_in_function_emits_deprecated_fallback_decision_and_metric(
    tmp_path: Path,
) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    execution_context: dict[str, Any] = {"ayrpack": {"spine": []}}
    events: list[tuple[str, dict[str, object]]] = []

    def hook(evt: str, payload: dict[str, object]) -> None:
        events.append((evt, payload))

    result = replace_in_function(
        workspace_root=tmp_path,
        relative_path="service.py",
        function_name="CodeintelQueryService.list_relations",
        replacement=(
            "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
            "        return {'items': ['x']}\n"
        ),
        policy_hook=hook,
        execution_context=execution_context,
    )

    assert result["operation"] == "replace_in_function"
    assert execution_context["ayrpack"]["spine"][-1]["kind"] == "decision"
    assert execution_context["ayrpack"]["spine"][-1]["summary"] == "fallback edit primitive used"
    assert execution_context["ayrpack"]["spine"][-1]["details"]["primitive"] == "replace_in_function"
    assert execution_context["ayrpack"]["spine"][-1]["details"]["scope"]["relative_path"] == "service.py"
    assert any(evt == "deprecated_mutation_tool_used" for evt, _payload in events)
    assert any(evt == "mutation_fallback_used" for evt, _payload in events)


def test_replace_in_function_can_be_disabled_by_feature_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AIX_ENABLE_REPLACE_IN_FUNCTION_FALLBACK", "0")

    with pytest.raises(RuntimeError, match="DEPRECATED_MUTATION_TOOL_DISABLED primitive=replace_in_function"):
        replace_in_function(
            workspace_root=tmp_path,
            relative_path="service.py",
            function_name="CodeintelQueryService.list_relations",
            replacement=(
                "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
                "        return {'items': ['x']}\n"
            ),
            execution_context={"ayrpack": {"spine": []}},
        )


def test_replace_in_function_preserves_fastapi_route_decorator(tmp_path: Path) -> None:
    target = tmp_path / "router.py"
    target.write_text(
        "from fastapi import APIRouter, Query\n"
        "router = APIRouter()\n\n"
        "@router.get('/file')\n"
        "def read_workspace_file(path: str = Query(...)) -> dict:\n"
        "    return {'path': path}\n",
        encoding="utf-8",
    )

    replace_in_function(
        workspace_root=tmp_path,
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


def test_replace_in_function_rejects_symbol_divergence_after_grounding(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    grounding = verify_symbol_anchor(
        file_path=target,
        symbol_name="CodeintelQueryService.list_relations",
        anchor_quote="async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):",
    )
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        rows = [entity_id] if entity_id else []\n"
        "        return {'items': rows}\n",
        encoding="utf-8",
    )
    with pytest.raises(MutationDivergenceError, match="SYMBOL_DIVERGED"):
        replace_in_function(
            workspace_root=tmp_path,
            relative_path="service.py",
            function_name="CodeintelQueryService.list_relations",
            replacement=(
                "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
                "        return {'items': [entity_id] if entity_id else []}\n"
            ),
            expected_base_commit_sha=str(grounding.get("commit_sha") or "") or None,
            expected_file_blob_sha=str(grounding.get("file_blob_sha") or ""),
            expected_symbol_source_hash=str(grounding.get("symbol_source_hash") or ""),
        )


def test_replace_in_function_rejects_base_divergence_after_grounding(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    grounding = verify_symbol_anchor(
        file_path=target,
        symbol_name="CodeintelQueryService.list_relations",
        anchor_quote="async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):",
    )
    target.write_text(
        target.read_text(encoding="utf-8") + "\n\nHELPER_FLAG = True\n",
        encoding="utf-8",
    )
    with pytest.raises(MutationDivergenceError, match="BASE_DIVERGED"):
        replace_in_function(
            workspace_root=tmp_path,
            relative_path="service.py",
            function_name="CodeintelQueryService.list_relations",
            replacement=(
                "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
                "        return {'items': [entity_id] if entity_id else []}\n"
            ),
            expected_base_commit_sha=str(grounding.get("commit_sha") or "") or None,
            expected_file_blob_sha=str(grounding.get("file_blob_sha") or ""),
            expected_symbol_source_hash=str(grounding.get("symbol_source_hash") or ""),
        )


def test_replace_in_function_libcst_reindents_column_zero_replacement(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    replace_in_function(
        workspace_root=tmp_path,
        relative_path="service.py",
        function_name="CodeintelQueryService.list_relations",
        replacement=(
            "async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
            "    if entity_id:\n"
            "        return {'items': [entity_id]}\n"
            "    return {'items': []}\n"
        ),
    )
    text = target.read_text(encoding="utf-8")
    assert "\n    async def list_relations" in text
    assert "\n            return {'items': [entity_id]}" in text


def test_replace_in_function_rejects_fragment_only_replacement(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="MUTATION_FRAGMENT_REJECTED"):
        replace_in_function(
            workspace_root=tmp_path,
            relative_path="service.py",
            function_name="CodeintelQueryService.list_relations",
            replacement="async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):",
        )


def test_replace_function_rejects_fragment_only_replacement(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="MUTATION_FRAGMENT_REJECTED"):
        replace_function(
            workspace_root=tmp_path,
            relative_path="service.py",
            qualified_symbol="CodeintelQueryService.list_relations",
            replacement="async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):",
        )


def test_validate_replace_function_candidate_accepts_valid_async_method(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )

    result = validate_replace_function_candidate(
        workspace_root=tmp_path,
        relative_path="service.py",
        qualified_symbol="CodeintelQueryService.list_relations",
        replacement=(
            "async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
            "    if entity_id:\n"
            "        return {'items': [entity_id]}\n"
            "    return {'items': []}\n"
        ),
    )

    assert result["validated"] is True
    assert result["dry_run_failures"] == []
    assert result["symbol_range"]["qualified_name"] == "CodeintelQueryService.list_relations"


def test_validate_replace_function_candidate_reports_async_mismatch(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )

    result = validate_replace_function_candidate(
        workspace_root=tmp_path,
        relative_path="service.py",
        qualified_symbol="CodeintelQueryService.list_relations",
        replacement=(
            "def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
            "    return {'items': []}\n"
        ),
    )

    assert result["validated"] is False
    assert result["dry_run_failures"][0]["failure_code"] == "MUTATION_INVALID_FUNCTION_SHAPE"
    assert "Preserve async def" in result["dry_run_failures"][0]["suggested_fix"]


def test_validate_replace_function_candidate_reports_symbol_name_mismatch(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )

    result = validate_replace_function_candidate(
        workspace_root=tmp_path,
        relative_path="service.py",
        qualified_symbol="CodeintelQueryService.list_relations",
        replacement=(
            "async def other_name(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
            "    return {'items': []}\n"
        ),
    )

    assert result["validated"] is False
    assert result["dry_run_failures"][0]["failure_code"] == "MUTATION_INVALID_FUNCTION_SHAPE"
    assert "exact grounded function name" in result["dry_run_failures"][0]["suggested_fix"]
    assert result["replacement_shape_debug"]["parsed_replacement_symbol_name"] is None


def test_validate_replace_function_candidate_accepts_preserved_method_receiver(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id):\n"
        "        return {'items': [tenant_id]}\n",
        encoding="utf-8",
    )

    result = validate_replace_function_candidate(
        workspace_root=tmp_path,
        relative_path="service.py",
        qualified_symbol="CodeintelQueryService.list_relations",
        replacement=(
            "async def list_relations(self, tenant_id):\n"
            "    if tenant_id:\n"
            "        return {'items': [tenant_id]}\n"
            "    return {'items': []}\n"
        ),
    )

    assert result["validated"] is True
    assert result["dry_run_failures"] == []
    assert result["replacement_shape_debug"]["parsed_replacement_symbol_name"] == "list_relations"


def test_replace_in_function_rejects_method_shape_drift(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="MUTATION_INVALID_FUNCTION_SHAPE"):
        replace_in_function(
            workspace_root=tmp_path,
            relative_path="service.py",
            function_name="CodeintelQueryService.list_relations",
            replacement=(
                "def list_relations(tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
                "    return {'items': []}\n"
            ),
        )


def test_replace_in_function_dry_run_reports_structured_failure_payload(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = replace_in_function(
        workspace_root=tmp_path,
        relative_path="service.py",
        function_name="CodeintelQueryService.list_relations",
        replacement="def list_relations(tenant_id, project_id, repo_id, entity_id, limit, offset):\n    return {'items': []}\n",
        dry_run=True,
    )
    assert result["operation"] == "replace_in_function"
    assert result["dry_run"] is True
    assert result["validated"] is False
    assert result["changed_files"] == []
    assert result["dry_run_failures"][0]["tool"] == "replace_in_function"
    assert result["dry_run_failures"][0]["symbol"] == "CodeintelQueryService.list_relations"
    assert result["dry_run_failures"][0]["failure_code"] == "MUTATION_INVALID_FUNCTION_SHAPE"
    assert "Preserve async def" in result["dry_run_failures"][0]["suggested_fix"]
    assert "async def list_relations" in target.read_text(encoding="utf-8")


def test_replace_in_function_dry_run_uses_container_runtime_when_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )

    calls: list[dict[str, object]] = []

    def fake_execute_mutation_in_runtime(**kwargs):
        calls.append({"tool": kwargs.get("tool"), "args": kwargs.get("args"), "runtime": kwargs.get("runtime")})
        return {
            "operation": "replace_in_function",
            "dry_run": True,
            "dry_run_failures": [],
            "validated": True,
            "changed_files": [],
            "grounded_symbol": {
                "commit_sha": "runtime-commit",
                "file_blob_sha": "runtime-blob",
                "symbol_source_hash": "sha256:runtime",
            },
        }

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_tools_service.execute_mutation_in_runtime",
        fake_execute_mutation_in_runtime,
    )

    result = replace_in_function(
        workspace_root=tmp_path,
        relative_path="service.py",
        function_name="CodeintelQueryService.list_relations",
        replacement=(
            "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
            "        return {'items': [entity_id] if entity_id else []}\n"
        ),
        dry_run=True,
        runtime={"mode": "docker", "container_required": True, "image": "runtime:latest"},
    )

    assert result["dry_run"] is True
    assert result["validated"] is True
    assert result["dry_run_failures"] == []
    assert "return {'items': []}" in target.read_text(encoding="utf-8")
    assert calls == [
        {
            "tool": "replace_in_function",
            "args": {
                "relative_path": "service.py",
                "function_name": "CodeintelQueryService.list_relations",
                "replacement": (
                    "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
                    "        return {'items': [entity_id] if entity_id else []}\n"
                ),
                "dry_run": True,
                "expected_base_commit_sha": None,
                "expected_file_blob_sha": None,
                "expected_symbol_source_hash": None,
            },
            "runtime": {"mode": "docker", "container_required": True, "image": "runtime:latest"},
        }
    ]


def test_update_call_kwarg_updates_existing_kwarg_without_touching_indentation(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def answer_callers(self, *, tenant_id: str, project_id: str, query: str, limit: int = 10) -> dict[str, object]:\n"
        "        symbol_result = await self.list_entities(\n"
        "            tenant_id=tenant_id,\n"
        "            project_id=project_id,\n"
        "            repo_id=None,\n"
        "            query=query,\n"
        "            limit=10,\n"
        "        )\n"
        "        return {'items': symbol_result}\n",
        encoding="utf-8",
    )

    result = update_call_kwarg(
        workspace_root=tmp_path,
        relative_path="service.py",
        symbol="CodeintelQueryService.answer_callers",
        callee="self.list_entities",
        kwarg="repo_id",
        new_value="repo_id",
    )

    text = target.read_text(encoding="utf-8")
    assert result["operation"] == "update_call_kwarg"
    assert result["changed_files"] == ["service.py"]
    assert "            repo_id=repo_id,\n" in text
    assert "        symbol_result = await self.list_entities(\n" in text
    assert "        return {'items': symbol_result}\n" in text


def test_update_call_kwarg_dry_run_rejects_missing_kwarg(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def answer_callers(self, *, tenant_id: str, project_id: str, query: str, limit: int = 10) -> dict[str, object]:\n"
        "        symbol_result = await self.list_entities(\n"
        "            tenant_id=tenant_id,\n"
        "            project_id=project_id,\n"
        "            query=query,\n"
        "            limit=10,\n"
        "        )\n"
        "        return {'items': symbol_result}\n",
        encoding="utf-8",
    )

    result = update_call_kwarg(
        workspace_root=tmp_path,
        relative_path="service.py",
        symbol="CodeintelQueryService.answer_callers",
        callee="self.list_entities",
        kwarg="repo_id",
        new_value="repo_id",
        dry_run=True,
    )

    assert result["operation"] == "update_call_kwarg"
    assert result["dry_run"] is True
    assert result["validated"] is False
    assert result["changed_files"] == []
    assert result["dry_run_failures"][0]["tool"] == "update_call_kwarg"
    assert result["dry_run_failures"][0]["failure"] == "CALL_KWARG_NOT_FOUND"


def test_insert_after_symbol_is_removed_without_mutating_file(tmp_path: Path) -> None:
    target = tmp_path / "router.py"
    original = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n\n"
        "@router.get('/repos/{repo_id}/relations')\n"
        "async def list_repo_relations(request):\n"
        "    return {'ok': True}\n"
    )
    target.write_text(original, encoding="utf-8")
    with pytest.raises(RuntimeError, match="REMOVED_MUTATION_TOOL primitive=insert_after_symbol"):
        insert_after_symbol(
            workspace_root=tmp_path,
            relative_path="router.py",
            symbol_name="list_repo_relations",
            new_content="async def helper_route():\n    return {'ok': False}\n",
        )
    assert target.read_text(encoding="utf-8") == original


def test_insert_before_return_is_removed_without_mutating_file(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    original = (
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        rows = []\n"
        "        return {'items': rows}\n"
    )
    target.write_text(original, encoding="utf-8")
    with pytest.raises(RuntimeError, match="REMOVED_MUTATION_TOOL primitive=insert_before_return"):
        insert_before_return(
            workspace_root=tmp_path,
            relative_path="service.py",
            function_name="CodeintelQueryService.list_relations",
            new_content="        if entity_id:\n            rows = [entity_id]\n",
        )
    assert target.read_text(encoding="utf-8") == original


def test_apply_staged_delivery_slice_supports_bounded_impl_router_test_flow(tmp_path: Path) -> None:
    service_path = tmp_path / "backend" / "services" / "demo_service.py"
    service_path.parent.mkdir(parents=True)
    service_path.write_text(
        "class DemoService:\n" "    async def list_items(self) -> list[str]:\n" "        return []\n",
        encoding="utf-8",
    )
    router_path = tmp_path / "backend" / "routers" / "demo_router.py"
    router_path.parent.mkdir(parents=True)
    router_path.write_text(
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n"
        "class DemoClient:\n"
        "    async def list_items(self, *, limit: int = 10) -> list[str]:\n"
        "        return []\n\n"
        "client = DemoClient()\n\n"
        "@router.get('/health')\n"
        "async def health() -> dict[str, object]:\n"
        "    items = await client.list_items(limit=10)\n"
        "    return {'ok': True, 'items': items}\n",
        encoding="utf-8",
    )
    test_path = tmp_path / "tests" / "test_demo_router.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "def test_health_route() -> None:\n" "    assert True\n",
        encoding="utf-8",
    )

    result = apply_staged_delivery_slice(
        workspace_root=tmp_path,
        stages=[
            {
                "tool": "replace_in_function",
                "args": {
                    "relative_path": "backend/services/demo_service.py",
                    "function_name": "DemoService.list_items",
                    "replacement": ("    async def list_items(self) -> list[str]:\n" "        return ['demo']\n"),
                },
                "required_symbols": ["DemoService.list_items"],
            },
            {
                "tool": "update_call_kwarg",
                "args": {
                    "relative_path": "backend/routers/demo_router.py",
                    "symbol": "health",
                    "callee": "client.list_items",
                    "kwarg": "limit",
                    "new_value": "25",
                },
                "required_symbols": ["health"],
            },
            {
                "tool": "replace_in_function",
                "args": {
                    "relative_path": "tests/test_demo_router.py",
                    "function_name": "test_health_route",
                    "replacement": "def test_health_route() -> None:\n    assert 'health'\n",
                },
                "required_symbols": ["test_health_route"],
            },
        ],
    )

    assert result["operation"] == "apply_staged_delivery_slice"
    assert result["stage_count"] == 3
    assert result["file_count"] == 3
    assert result["changed_files"] == [
        "backend/routers/demo_router.py",
        "backend/services/demo_service.py",
        "tests/test_demo_router.py",
    ]
    assert "return ['demo']" in service_path.read_text(encoding="utf-8")
    router_text = router_path.read_text(encoding="utf-8")
    assert "limit=25" in router_text
    assert "await client.list_items(limit=25)" in router_text
    assert "assert 'health'" in test_path.read_text(encoding="utf-8")


def test_apply_staged_delivery_slice_rejects_phase_backtracking(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="STAGED_DELIVERY_ORDER_INVALID"):
        apply_staged_delivery_slice(
            workspace_root=tmp_path,
            stages=[
                {
                    "tool": "replace_in_function",
                    "args": {
                        "relative_path": "tests/test_demo_router.py",
                        "function_name": "test_health_route",
                        "replacement": "def test_health_route() -> None:\n    assert True\n",
                    },
                },
                {
                    "tool": "replace_in_function",
                    "args": {
                        "relative_path": "backend/services/demo_service.py",
                        "function_name": "DemoService.list_items",
                        "replacement": "    async def list_items(self) -> list[str]:\n        return ['demo']\n",
                    },
                },
            ],
        )


def test_apply_staged_delivery_slice_rejects_missing_required_import(tmp_path: Path) -> None:
    router_path = tmp_path / "backend" / "routers" / "demo_router.py"
    router_path.parent.mkdir(parents=True)
    router_path.write_text(
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n"
        "@router.get('/health')\n"
        "async def health() -> dict[str, bool]:\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="STAGED_IMPORT_CONSISTENCY_FAILED"):
        apply_staged_delivery_slice(
            workspace_root=tmp_path,
            stages=[
                {
                    "tool": "replace_in_function",
                    "args": {
                        "relative_path": "backend/routers/demo_router.py",
                        "function_name": "health",
                        "replacement": (
                            "async def health() -> dict[str, object]:\n"
                            "    return {'ok': True, 'response': Response(content='ok')}\n"
                        ),
                    },
                    "required_imports": ["Response"],
                    "required_symbols": ["health"],
                }
            ],
        )


def test_apply_staged_delivery_slice_uses_container_runtime_when_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_execute_mutation_in_runtime(**kwargs):
        captured.update(kwargs)
        return {
            "operation": "apply_staged_delivery_slice",
            "stage_count": 1,
            "file_count": 1,
            "dry_run": False,
            "validated": True,
            "changed_files": ["backend/services/demo_service.py"],
            "stages": [],
        }

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_tools_service.execute_mutation_in_runtime",
        fake_execute_mutation_in_runtime,
    )

    result = apply_staged_delivery_slice(
        workspace_root=tmp_path,
        stages=[
            {
                "tool": "replace_in_function",
                "args": {
                    "relative_path": "backend/services/demo_service.py",
                    "function_name": "DemoService.list_items",
                    "replacement": "    async def list_items(self) -> list[str]:\n        return ['demo']\n",
                },
            }
        ],
        runtime={"mode": "docker", "container_required": True, "image": "runtime:latest"},
    )

    assert result["operation"] == "apply_staged_delivery_slice"
    assert captured["tool"] == "apply_staged_delivery_slice"
    assert captured["args"] == {
        "stages": [
            {
                "tool": "replace_in_function",
                "args": {
                    "relative_path": "backend/services/demo_service.py",
                    "function_name": "DemoService.list_items",
                    "replacement": "    async def list_items(self) -> list[str]:\n        return ['demo']\n",
                },
            }
        ],
        "dry_run": False,
        "max_stages": 4,
        "max_files": 3,
    }


def test_edit_symbol_signature_updates_method_signature_only(tmp_path: Path) -> None:
    target = tmp_path / "meta_test_fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        conn = await self._connect()\n"
        "        try:\n"
        "            rows = await conn.fetch('select 1', tenant_id, limit, offset)\n"
        "        finally:\n"
        "            await conn.close()\n"
        "        return {'items': [dict(r) for r in rows]}\n",
        encoding="utf-8",
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="meta_test_fixture.py",
        symbol="FixtureService.list_items",
        mode="signature",
        new_signature=(
            "async def list_items("
            "self, tenant_id: str, limit: int, offset: int, item_type: str | None = None"
            ") -> dict"
        ),
    )
    text = target.read_text(encoding="utf-8")
    assert result["operation"] == "edit_symbol"
    assert "item_type: str | None = None" in text
    assert "conn = await self._connect()" in text
    assert result["symbol"] == "FixtureService.list_items"


def test_edit_symbol_dry_run_reports_invalid_signature_order(tmp_path: Path) -> None:
    target = tmp_path / "meta_test_fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="meta_test_fixture.py",
        symbol="FixtureService.list_items",
        mode="signature",
        new_signature="async def list_items(self, tenant_id: str, item_type: str | None = None, limit: int, offset: int) -> dict",
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["validated"] is False
    assert result["dry_run_failures"][0]["failure"] == "INVALID_SIGNATURE_SYNTAX"


def test_edit_symbol_dry_run_rejects_async_signature_drift(tmp_path: Path) -> None:
    target = tmp_path / "meta_test_fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        conn = await self._connect()\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="meta_test_fixture.py",
        symbol="FixtureService.list_items",
        mode="signature",
        new_signature="def list_items(self, tenant_id: str, limit: int, offset: int, item_type: str | None = None) -> dict",
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["validated"] is False
    assert result["dry_run_failures"][0]["failure"] == "MUTATION_INVALID_FUNCTION_SHAPE"
    assert result["dry_run_failures"][0]["detail"] == "async_mismatch"


def test_edit_symbol_dry_run_uses_container_runtime_when_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "meta_test_fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def fake_execute_mutation_in_runtime(**kwargs):
        calls.append({"tool": kwargs.get("tool"), "args": kwargs.get("args"), "runtime": kwargs.get("runtime")})
        return {
            "operation": "edit_symbol",
            "dry_run": True,
            "dry_run_failures": [],
            "validated": True,
            "changed_files": [],
            "grounded_symbol": {
                "commit_sha": "runtime-commit",
                "file_blob_sha": "runtime-blob",
                "symbol_source_hash": "sha256:runtime",
            },
        }

    monkeypatch.setattr(
        "backend.services.devexecutor_mutation_tools_service.execute_mutation_in_runtime",
        fake_execute_mutation_in_runtime,
    )

    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="meta_test_fixture.py",
        symbol="FixtureService.list_items",
        mode="signature",
        new_signature=(
            "async def list_items("
            "self, tenant_id: str, limit: int, offset: int, item_type: str | None = None"
            ") -> dict"
        ),
        dry_run=True,
        runtime={"mode": "docker", "container_required": True, "image": "runtime:latest"},
    )

    assert result["dry_run"] is True
    assert result["validated"] is True
    assert calls == [
        {
            "tool": "edit_symbol",
            "args": {
                "relative_path": "meta_test_fixture.py",
                "symbol": "FixtureService.list_items",
                "mode": "signature",
                "new_signature": (
                    "async def list_items("
                    "self, tenant_id: str, limit: int, offset: int, item_type: str | None = None"
                    ") -> dict"
                ),
                "anchor_pattern": None,
                "position": None,
                "new_code": None,
                "dry_run": True,
                "expected_base_commit_sha": None,
                "expected_file_blob_sha": None,
                "expected_symbol_source_hash": None,
            },
            "runtime": {"mode": "docker", "container_required": True, "image": "runtime:latest"},
        }
    ]


def test_extend_python_signature_updates_keyword_only_method_signature(tmp_path: Path) -> None:
    target = tmp_path / "meta_test_fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n",
        encoding="utf-8",
    )

    result = extend_python_signature(
        workspace_root=tmp_path,
        relative_path="meta_test_fixture.py",
        symbol="FixtureService.mutate_task",
        parameter_name="target_project_id",
        parameter_type="str | None",
        default_value="None",
        parameter_kind="auto",
    )

    text = target.read_text(encoding="utf-8")
    assert result["operation"] == "extend_python_signature"
    assert (
        "async def mutate_task(self, *, task_id: str, reason: str | None = None, target_project_id: str | None = None)"
        in text
    )
    assert "return {'task_id': task_id}" in text


def test_extend_python_signature_dry_run_rejects_duplicate_parameter(tmp_path: Path) -> None:
    target = tmp_path / "meta_test_fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def mutate_task(self, *, task_id: str, target_project_id: str | None = None) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n",
        encoding="utf-8",
    )

    result = extend_python_signature(
        workspace_root=tmp_path,
        relative_path="meta_test_fixture.py",
        symbol="FixtureService.mutate_task",
        parameter_name="target_project_id",
        parameter_type="str | None",
        default_value="None",
        parameter_kind="keyword_only",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["validated"] is False
    assert result["dry_run_failures"][0]["failure"] == "PARAMETER_ALREADY_PRESENT"


def test_extend_python_signature_accepts_optional_parameter_kind(tmp_path: Path) -> None:
    target = tmp_path / "meta_test_fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def mutate_task(self, *, task_id: str, reason: str | None = None) -> dict[str, str]:\n"
        "        return {'task_id': task_id}\n",
        encoding="utf-8",
    )

    result = extend_python_signature(
        workspace_root=tmp_path,
        relative_path="meta_test_fixture.py",
        symbol="FixtureService.mutate_task",
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


def test_extend_python_signature_preserves_multiline_keyword_only_signature(tmp_path: Path) -> None:
    target = tmp_path / "meta_test_fixture.py"
    target.write_text(
        "class FixtureService:\n"
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

    result = extend_python_signature(
        workspace_root=tmp_path,
        relative_path="meta_test_fixture.py",
        symbol="FixtureService.mutate_task",
        parameter_name="target_project_id",
        parameter_type="str | None",
        default_value="None",
        parameter_kind="optional",
    )

    text = target.read_text(encoding="utf-8")
    assert result["operation"] == "extend_python_signature"
    assert "target_project_id: str | None = None" in text
    py_compile.compile(str(target), doraise=True)


def test_edit_symbol_body_insert_inserts_near_anchor(tmp_path: Path) -> None:
    target = tmp_path / "meta_test_fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        conn = await self._connect()\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="meta_test_fixture.py",
        symbol="FixtureService.list_items",
        mode="body_insert",
        anchor_pattern="conn = await self._connect()",
        position="after",
        new_code="if limit < 0:\n    raise ValueError('limit must be non-negative')",
    )
    text = target.read_text(encoding="utf-8")
    assert result["operation"] == "edit_symbol"
    assert "raise ValueError('limit must be non-negative')" in text


def test_edit_symbol_body_insert_uses_innermost_matching_block(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        conn = await self._connect()\n"
        "        try:\n"
        "            if entity_id:\n"
        "                total = await conn.fetchval(\n"
        '                    "SELECT COUNT(1) FROM rel WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3",\n'
        "                    tenant_id, project_id, repo_id,\n"
        "                )\n"
        "            else:\n"
        "                total = 0\n"
        "        finally:\n"
        "            await conn.close()\n"
        "        return {'total': total}\n",
        encoding="utf-8",
    )
    edit_symbol(
        workspace_root=tmp_path,
        relative_path="service.py",
        symbol="CodeintelQueryService.list_relations",
        mode="body_insert",
        anchor_pattern="WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3",
        position="after",
        new_code="if entity_id:\n    total = total + 1",
    )
    text = target.read_text(encoding="utf-8")
    assert "total = total + 1" in text
    assert text.index("total = total + 1") > text.index("total = await conn.fetchval(")
    assert text.index("total = total + 1") < text.index("finally:")


def test_edit_symbol_body_insert_rejects_undefined_local_references(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id, project_id, repo_id, entity_id, limit, offset):\n"
        "        conn = await self._connect()\n"
        "        try:\n"
        "            rows = await conn.fetch('select 1', tenant_id, project_id, repo_id)\n"
        "        finally:\n"
        "            await conn.close()\n"
        "        return {'items': rows}\n",
        encoding="utf-8",
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="service.py",
        symbol="CodeintelQueryService.list_relations",
        mode="body_insert",
        anchor_pattern="rows = await conn.fetch",
        position="after",
        new_code="count_sql = count_sql.replace('a', 'b')\nrows_sql = rows_sql.replace('a', 'b')",
        dry_run=True,
    )
    assert result["validated"] is False
    assert result["dry_run_failures"][0]["failure"] == "UNDEFINED_LOCAL_REFERENCE"
    assert "count_sql" in result["dry_run_failures"][0]["detail"]
    assert "rows_sql" in result["dry_run_failures"][0]["detail"]


# #1842 – Anchor compatibility shim
def test_anchor_not_found_failure_includes_shim_event_and_migration_hint(tmp_path: Path) -> None:
    """ANCHOR_NOT_FOUND failure must carry shim_event telemetry and direct toward replace_in_function."""
    target = tmp_path / "service.py"
    target.write_text(
        "class SomeService:\n"
        "    def do_work(self, payload):\n"
        "        result = self._process(payload)\n"
        "        return result\n",
        encoding="utf-8",
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="service.py",
        symbol="SomeService.do_work",
        mode="body_insert",
        anchor_pattern="this_line_does_not_exist_in_the_function",
        position="after",
        new_code="extra = 1",
        dry_run=True,
    )
    assert result["validated"] is False
    failure = result["dry_run_failures"][0]
    assert failure["failure"] == "ANCHOR_NOT_FOUND"
    assert failure.get("shim_event") == "anchor_not_found_compat"
    assert "replace_in_function" in failure["suggested_fix"]
    assert "write_file" in failure["suggested_fix"]
    assert "legacy compatibility shim" in failure["suggested_fix"]


def test_anchor_not_found_emits_policy_hook_event(tmp_path: Path) -> None:
    """edit_symbol with a missing anchor must emit anchor_not_found_compat hook event."""
    target = tmp_path / "service.py"
    target.write_text(
        "class SomeService:\n"
        "    def do_work(self, payload):\n"
        "        result = self._process(payload)\n"
        "        return result\n",
        encoding="utf-8",
    )
    events: list[tuple[str, dict]] = []

    def capture_hook(event: str, payload: dict) -> None:
        events.append((event, payload))

    try:
        edit_symbol(
            workspace_root=tmp_path,
            relative_path="service.py",
            symbol="SomeService.do_work",
            mode="body_insert",
            anchor_pattern="nonexistent_anchor_xyzzy",
            position="after",
            new_code="extra = 1",
            policy_hook=capture_hook,
        )
    except ValueError:
        pass

    emitted = [e for e in events if e[0] == "anchor_not_found_compat"]
    assert emitted, "anchor_not_found_compat hook event not emitted"
    evt_payload = emitted[0][1]
    assert evt_payload["shim_event"] == "anchor_not_found_compat"
    assert "nonexistent_anchor_xyzzy" in evt_payload["anchor_pattern"]


def test_edit_symbol_full_replaces_method_preserving_async_shape(tmp_path: Path) -> None:
    target = tmp_path / "meta_test_fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        conn = await self._connect()\n"
        "        try:\n"
        "            rows = await conn.fetch('select 1', tenant_id, limit, offset)\n"
        "        finally:\n"
        "            await conn.close()\n"
        "        return {'items': [dict(r) for r in rows]}\n",
        encoding="utf-8",
    )
    replacement = (
        "async def list_items(self, tenant_id: str, limit: int, offset: int, item_type: str | None = None) -> dict:\n"
        "    conn = await self._connect()\n"
        "    try:\n"
        "        rows = await conn.fetch('select 1', tenant_id, limit, offset)\n"
        "    finally:\n"
        "        await conn.close()\n"
        "    return {'items': [dict(r) for r in rows]}\n"
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="meta_test_fixture.py",
        symbol="FixtureService.list_items",
        mode="full",
        new_code=replacement,
    )
    text = target.read_text(encoding="utf-8")
    assert result["operation"] == "edit_symbol"
    assert "item_type: str | None = None" in text
    assert "async def list_items" in text
    assert "await conn.close()" in text


def test_edit_symbol_full_rejects_method_shape_drift(tmp_path: Path) -> None:
    target = tmp_path / "meta_test_fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="meta_test_fixture.py",
        symbol="FixtureService.list_items",
        mode="full",
        new_code="def list_items(tenant_id: str, limit: int, offset: int) -> dict:\n    return {'items': []}\n",
        dry_run=True,
    )
    assert result["validated"] is False
    assert result["dry_run_failures"][0]["failure"] == "MUTATION_INVALID_FUNCTION_SHAPE"


def test_edit_symbol_full_rejects_sql_semantic_drift(tmp_path: Path) -> None:
    target = tmp_path / "codeintel_query_service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id: str, project_id: str, repo_id: str, entity_id: str | None, limit: int, offset: int) -> dict:\n"
        '        total_sql = """\n'
        "        SELECT COUNT(1)\n"
        "        FROM hyeve_codeintel.relations\n"
        "        WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND source_entity_id = $4\n"
        '        """\n'
        '        rows_sql = """\n'
        "        SELECT id, relation_type, source_entity_id\n"
        "        FROM hyeve_codeintel.relations\n"
        "        WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND source_entity_id = $4\n"
        "        ORDER BY created_at DESC LIMIT $5 OFFSET $6\n"
        '        """\n'
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    replacement = (
        "async def list_relations(self, tenant_id: str, project_id: str, repo_id: str, entity_id: str | None, limit: int, offset: int, relation_type: str | None = None) -> dict:\n"
        '    total_sql = "SELECT count(*) FROM relations WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND entity_id = $4"\n'
        '    rows_sql = "SELECT * FROM relations WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND entity_id = $4 ORDER BY created_at DESC LIMIT $$5 OFFSET $$6"\n'
        "    return {'items': []}\n"
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="codeintel_query_service.py",
        symbol="CodeintelQueryService.list_relations",
        mode="full",
        new_code=replacement,
        dry_run=True,
    )
    assert result["validated"] is False
    assert result["dry_run_failures"][0]["failure"] == "SQL_PRESERVATION_VIOLATED"


def test_edit_symbol_full_rejects_function_name_drift_as_out_of_scope(tmp_path: Path) -> None:
    target = tmp_path / "meta_test_fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="meta_test_fixture.py",
        symbol="FixtureService.list_items",
        mode="full",
        new_code="async def list_items_v2(self, tenant_id: str, limit: int, offset: int) -> dict:\n    return {'items': []}\n",
        dry_run=True,
    )
    assert result["validated"] is False
    assert result["dry_run_failures"][0]["failure"] == "OUT_OF_SCOPE_MUTATION"
    assert result["dry_run_failures"][0]["detail"] == "function_name_changed:list_items_v2"


def test_edit_symbol_full_rejects_local_variable_rename_as_out_of_scope(tmp_path: Path) -> None:
    target = tmp_path / "meta_test_fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        conn = await self._connect()\n"
        "        rows = await conn.fetch('select 1', tenant_id, limit, offset)\n"
        "        return {'items': [dict(r) for r in rows]}\n",
        encoding="utf-8",
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="meta_test_fixture.py",
        symbol="FixtureService.list_items",
        mode="full",
        new_code=(
            "async def list_items(self, tenant_id: str, limit: int, offset: int, item_type: str | None = None) -> dict:\n"
            "    connection = await self._connect()\n"
            "    rows = await connection.fetch('select 1', tenant_id, limit, offset)\n"
            "    return {'items': [dict(r) for r in rows]}\n"
        ),
        dry_run=True,
    )
    assert result["validated"] is False
    assert result["dry_run_failures"][0]["failure"] == "OUT_OF_SCOPE_MUTATION"
    assert result["dry_run_failures"][0]["detail"] == "local_names_missing:conn"


def test_edit_symbol_full_rejects_try_finally_removal_as_out_of_scope(tmp_path: Path) -> None:
    target = tmp_path / "meta_test_fixture.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        conn = await self._connect()\n"
        "        try:\n"
        "            rows = await conn.fetch('select 1', tenant_id, limit, offset)\n"
        "        finally:\n"
        "            await conn.close()\n"
        "        return {'items': [dict(r) for r in rows]}\n",
        encoding="utf-8",
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="meta_test_fixture.py",
        symbol="FixtureService.list_items",
        mode="full",
        new_code=(
            "async def list_items(self, tenant_id: str, limit: int, offset: int, item_type: str | None = None) -> dict:\n"
            "    conn = await self._connect()\n"
            "    rows = await conn.fetch('select 1', tenant_id, limit, offset)\n"
            "    return {'items': [dict(r) for r in rows]}\n"
        ),
        dry_run=True,
    )
    assert result["validated"] is False
    assert result["dry_run_failures"][0]["failure"] == "OUT_OF_SCOPE_MUTATION"
    assert result["dry_run_failures"][0]["detail"] == "control_flow_missing:try, finally"


def test_edit_symbol_sql_blocks_rewrites_selected_sql_literals(tmp_path: Path) -> None:
    target = tmp_path / "codeintel_query_service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id: str, project_id: str, repo_id: str, entity_id: str | None, limit: int, offset: int) -> dict:\n"
        '        total_sql = """\n'
        "        SELECT COUNT(1)\n"
        "        FROM hyeve_codeintel.relations\n"
        "        WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND source_entity_id = $4\n"
        '        """\n'
        '        rows_sql = """\n'
        "        SELECT id, relation_type, source_entity_id\n"
        "        FROM hyeve_codeintel.relations\n"
        "        WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND source_entity_id = $4\n"
        "        ORDER BY created_at DESC LIMIT $5 OFFSET $6\n"
        '        """\n'
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    payload = (
        '{"blocks":['
        '{"index":1,"sql":"SELECT COUNT(1) FROM hyeve_codeintel.relations WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND source_entity_id = $4 AND relation_type = $5"},'
        '{"index":2,"sql":"SELECT id, relation_type, source_entity_id FROM hyeve_codeintel.relations WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND source_entity_id = $4 AND relation_type = $5 ORDER BY created_at DESC LIMIT $6 OFFSET $7"}'
        "]}"
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="codeintel_query_service.py",
        symbol="CodeintelQueryService.list_relations",
        mode="sql_blocks",
        new_code=payload,
    )
    assert result["operation"] == "edit_symbol"
    content = target.read_text(encoding="utf-8")
    assert "relation_type = $5" in content
    assert "hyeve_codeintel.relations" in content
    assert "SELECT id, relation_type, source_entity_id" in content


def test_edit_symbol_sql_blocks_predicate_mode_threads_relation_type(tmp_path: Path) -> None:
    target = tmp_path / "codeintel_query_service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id: str, project_id: str, repo_id: str, entity_id: str | None, limit: int, offset: int) -> dict:\n"
        "        rows = await conn.fetch(\n"
        '            """\n'
        "            SELECT id, relation_type, source_entity_id\n"
        "            FROM hyeve_codeintel.relations\n"
        "            WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND source_entity_id = $4\n"
        "            ORDER BY created_at DESC LIMIT $5 OFFSET $6\n"
        '            """,\n'
        "            tenant_id,\n"
        "            project_id,\n"
        "            repo_id,\n"
        "            entity_id,\n"
        "            limit,\n"
        "            offset,\n"
        "        )\n"
        "        return {'items': [dict(r) for r in rows]}\n",
        encoding="utf-8",
    )
    payload = '{"blocks":[{"index":1,"conditions":[{"column":"relation_type","operator":"=","param_variable":"relation_type"}]}]}'
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="codeintel_query_service.py",
        symbol="CodeintelQueryService.list_relations",
        mode="sql_blocks",
        new_code=payload,
    )
    assert result["operation"] == "edit_symbol"
    content = target.read_text(encoding="utf-8")
    assert "relation_type = $5" in content
    assert "LIMIT $6 OFFSET $7" in content
    assert "entity_id" in content
    assert "relation_type" in content
    assert "limit" in content
    assert "offset" in content
    assert "AND AND" not in content
    assert "ORDER BY created_at DESC LIMIT $6 OFFSET $7" in " ".join(content.split())


def test_edit_symbol_sql_blocks_strips_leading_and_and_preserves_sql_shape(tmp_path: Path) -> None:
    target = tmp_path / "codeintel_query_service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id: str, project_id: str, repo_id: str, entity_id: str | None, limit: int, offset: int) -> dict:\n"
        "        rows = await conn.fetch(\n"
        '            """\n'
        "            SELECT id, relation_type, source_entity_id, target_entity_id\n"
        "            FROM hyeve_codeintel.relations\n"
        "            WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND source_entity_id = $4\n"
        "            ORDER BY created_at DESC, id ASC\n"
        "            LIMIT $5 OFFSET $6\n"
        '            """,\n'
        "            tenant_id,\n"
        "            project_id,\n"
        "            repo_id,\n"
        "            entity_id,\n"
        "            limit,\n"
        "            offset,\n"
        "        )\n"
        "        return {'items': [dict(r) for r in rows]}\n",
        encoding="utf-8",
    )
    payload = '{"blocks":[{"index":1,"conditions":[{"column":"relation_type","operator":"=","param_variable":"relation_type"}]}]}'
    edit_symbol(
        workspace_root=tmp_path,
        relative_path="codeintel_query_service.py",
        symbol="CodeintelQueryService.list_relations",
        mode="sql_blocks",
        new_code=payload,
    )
    content = target.read_text(encoding="utf-8")
    normalized = " ".join(content.split())
    parsed = ast.parse(content)
    sql_literal = next(
        node.value
        for node in ast.walk(parsed)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "FROM hyeve_codeintel.relations" in node.value
    )
    sql_normalized = " ".join(sql_literal.split())
    assert "AND AND" not in normalized
    assert "FROM hyeve_codeintel.relations" in normalized
    assert "SELECT id, relation_type, source_entity_id, target_entity_id" in normalized
    assert "ORDER BY created_at DESC, id ASC LIMIT $6 OFFSET $7" in sql_normalized
    assert "source_entity_id = $4 AND relation_type = $5" in sql_normalized


def test_edit_symbol_sql_blocks_preserves_real_list_relations_shape(tmp_path: Path) -> None:
    target = tmp_path / "codeintel_query_service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id: str, project_id: str, repo_id: str, entity_id: str | None, limit: int, offset: int) -> dict:\n"
        "        conn = await self._connect()\n"
        "        try:\n"
        "            if entity_id:\n"
        "                total = await conn.fetchval(\n"
        '                    """\n'
        "                    SELECT COUNT(1)\n"
        "                    FROM hyeve_codeintel.relations\n"
        "                    WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3\n"
        "                      AND source_entity_id = $4\n"
        '                    """,\n'
        "                    tenant_id,\n"
        "                    project_id,\n"
        "                    repo_id,\n"
        "                    entity_id,\n"
        "                )\n"
        "                rows = await conn.fetch(\n"
        '                    """\n'
        "                    SELECT id, relation_type, source_entity_id, target_entity_id, source_file_id, target_file_id, created_at\n"
        "                    FROM hyeve_codeintel.relations\n"
        "                    WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3\n"
        "                      AND source_entity_id = $4\n"
        "                    ORDER BY created_at DESC, id ASC\n"
        "                    LIMIT $5 OFFSET $6\n"
        '                    """,\n'
        "                    tenant_id,\n"
        "                    project_id,\n"
        "                    repo_id,\n"
        "                    entity_id,\n"
        "                    limit,\n"
        "                    offset,\n"
        "                )\n"
        "            else:\n"
        "                total = await conn.fetchval(\n"
        '                    """\n'
        "                    SELECT COUNT(1)\n"
        "                    FROM hyeve_codeintel.relations\n"
        "                    WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3\n"
        '                    """,\n'
        "                    tenant_id,\n"
        "                    project_id,\n"
        "                    repo_id,\n"
        "                )\n"
        "                rows = await conn.fetch(\n"
        '                    """\n'
        "                    SELECT id, relation_type, source_entity_id, target_entity_id, source_file_id, target_file_id, created_at\n"
        "                    FROM hyeve_codeintel.relations\n"
        "                    WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3\n"
        "                    ORDER BY created_at DESC, id ASC\n"
        "                    LIMIT $4 OFFSET $5\n"
        '                    """,\n'
        "                    tenant_id,\n"
        "                    project_id,\n"
        "                    repo_id,\n"
        "                    limit,\n"
        "                    offset,\n"
        "                )\n"
        "        finally:\n"
        "            await conn.close()\n"
        "        return {'total': int(total or 0), 'items': [dict(row) for row in rows]}\n",
        encoding="utf-8",
    )
    payload = (
        '{"blocks":['
        '{"index":1,"conditions":[{"column":"relation_type","operator":"=","param_variable":"relation_type"}]},'
        '{"index":2,"conditions":[{"column":"relation_type","operator":"=","param_variable":"relation_type"}]},'
        '{"index":3,"conditions":[{"column":"relation_type","operator":"=","param_variable":"relation_type"}]},'
        '{"index":4,"conditions":[{"column":"relation_type","operator":"=","param_variable":"relation_type"}]}'
        "]}"
    )
    edit_symbol(
        workspace_root=tmp_path,
        relative_path="codeintel_query_service.py",
        symbol="CodeintelQueryService.list_relations",
        mode="sql_blocks",
        new_code=payload,
    )
    content = target.read_text(encoding="utf-8")
    sql_literals = [
        node.value
        for node in ast.walk(ast.parse(content))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "FROM hyeve_codeintel.relations" in node.value
    ]
    normalized_literals = [" ".join(item.split()) for item in sql_literals]
    assert len(normalized_literals) == 4
    assert (
        "SELECT COUNT(1) FROM hyeve_codeintel.relations WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND source_entity_id = $4 AND relation_type = $5"
        in normalized_literals
    )
    assert (
        "SELECT id, relation_type, source_entity_id, target_entity_id, source_file_id, target_file_id, created_at FROM hyeve_codeintel.relations WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND source_entity_id = $4 AND relation_type = $5 ORDER BY created_at DESC, id ASC LIMIT $6 OFFSET $7"
        in normalized_literals
    )
    assert (
        "SELECT COUNT(1) FROM hyeve_codeintel.relations WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND relation_type = $4"
        in normalized_literals
    )
    assert (
        "SELECT id, relation_type, source_entity_id, target_entity_id, source_file_id, target_file_id, created_at FROM hyeve_codeintel.relations WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND relation_type = $4 ORDER BY created_at DESC, id ASC LIMIT $5 OFFSET $6"
        in normalized_literals
    )


def test_edit_symbol_sql_blocks_conditions_require_full_block_coverage(tmp_path: Path) -> None:
    target = tmp_path / "codeintel_query_service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id: str, project_id: str, repo_id: str, entity_id: str | None, limit: int, offset: int) -> dict:\n"
        "        total = await conn.fetchval(\n"
        '            """\n'
        "            SELECT COUNT(1)\n"
        "            FROM hyeve_codeintel.relations\n"
        "            WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3\n"
        '            """,\n'
        "            tenant_id, project_id, repo_id,\n"
        "        )\n"
        "        rows = await conn.fetch(\n"
        '            """\n'
        "            SELECT id, relation_type\n"
        "            FROM hyeve_codeintel.relations\n"
        "            WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3\n"
        "            ORDER BY created_at DESC LIMIT $4 OFFSET $5\n"
        '            """,\n'
        "            tenant_id, project_id, repo_id, limit, offset,\n"
        "        )\n"
        "        return {'items': [dict(r) for r in rows], 'total': int(total or 0)}\n",
        encoding="utf-8",
    )
    payload = '{"blocks":[{"index":1,"conditions":[{"column":"relation_type","operator":"=","param_variable":"relation_type"}]}]}'
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="codeintel_query_service.py",
        symbol="CodeintelQueryService.list_relations",
        mode="sql_blocks",
        new_code=payload,
        dry_run=True,
    )
    assert result["validated"] is False
    assert any(item["failure"] == "SQL_BLOCKS_INCOMPLETE" for item in result["dry_run_failures"])


def test_edit_symbol_sql_blocks_top_level_conditions_apply_to_all_blocks(tmp_path: Path) -> None:
    target = tmp_path / "codeintel_query_service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id: str, project_id: str, repo_id: str, entity_id: str | None, limit: int, offset: int) -> dict:\n"
        "        total = await conn.fetchval(\n"
        '            """\n'
        "            SELECT COUNT(1)\n"
        "            FROM hyeve_codeintel.relations\n"
        "            WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND source_entity_id = $4\n"
        '            """,\n'
        "            tenant_id, project_id, repo_id, entity_id,\n"
        "        )\n"
        "        rows = await conn.fetch(\n"
        '            """\n'
        "            SELECT id, relation_type\n"
        "            FROM hyeve_codeintel.relations\n"
        "            WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND source_entity_id = $4\n"
        "            ORDER BY created_at DESC LIMIT $5 OFFSET $6\n"
        '            """,\n'
        "            tenant_id, project_id, repo_id, entity_id, limit, offset,\n"
        "        )\n"
        "        total_all = await conn.fetchval(\n"
        '            """\n'
        "            SELECT COUNT(1)\n"
        "            FROM hyeve_codeintel.relations\n"
        "            WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3\n"
        '            """,\n'
        "            tenant_id, project_id, repo_id,\n"
        "        )\n"
        "        rows_all = await conn.fetch(\n"
        '            """\n'
        "            SELECT id, relation_type\n"
        "            FROM hyeve_codeintel.relations\n"
        "            WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3\n"
        "            ORDER BY created_at DESC LIMIT $4 OFFSET $5\n"
        '            """,\n'
        "            tenant_id, project_id, repo_id, limit, offset,\n"
        "        )\n"
        "        return {'items': [dict(r) for r in rows], 'total': int(total or 0), 'items_all': [dict(r) for r in rows_all], 'total_all': int(total_all or 0)}\n",
        encoding="utf-8",
    )
    payload = '{"conditions":[{"column":"relation_type","operator":"=","param_variable":"relation_type"}]}'
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="codeintel_query_service.py",
        symbol="CodeintelQueryService.list_relations",
        mode="sql_blocks",
        new_code=payload,
        dry_run=True,
    )
    assert result["validated"] is True
    edit_symbol(
        workspace_root=tmp_path,
        relative_path="codeintel_query_service.py",
        symbol="CodeintelQueryService.list_relations",
        mode="sql_blocks",
        new_code=payload,
    )
    content = target.read_text(encoding="utf-8")
    assert content.count("relation_type = $5") == 2
    assert content.count("relation_type = $4") == 2


def test_edit_symbol_sql_blocks_preserves_triple_quoted_sql_style(tmp_path: Path) -> None:
    target = tmp_path / "codeintel_query_service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id: str, project_id: str, repo_id: str, entity_id: str | None, limit: int, offset: int) -> dict:\n"
        "        rows = await conn.fetch(\n"
        '            """\n'
        "            SELECT id, relation_type, source_entity_id\n"
        "            FROM hyeve_codeintel.relations\n"
        "            WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND source_entity_id = $4\n"
        "            ORDER BY created_at DESC LIMIT $5 OFFSET $6\n"
        '            """,\n'
        "            tenant_id,\n"
        "            project_id,\n"
        "            repo_id,\n"
        "            entity_id,\n"
        "            limit,\n"
        "            offset,\n"
        "        )\n"
        "        return {'items': [dict(r) for r in rows]}\n",
        encoding="utf-8",
    )
    payload = '{"blocks":[{"index":1,"conditions":[{"column":"relation_type","operator":"=","param_variable":"relation_type"}]}]}'
    edit_symbol(
        workspace_root=tmp_path,
        relative_path="codeintel_query_service.py",
        symbol="CodeintelQueryService.list_relations",
        mode="sql_blocks",
        new_code=payload,
    )
    content = target.read_text(encoding="utf-8")
    assert '"""' in content
    assert "\\n                    SELECT" not in content
    assert "relation_type = $5" in content


def test_edit_symbol_sql_blocks_conditions_rejects_template_placeholder_drift(tmp_path: Path) -> None:
    target = tmp_path / "codeintel_query_service.py"
    target.write_text(
        "class CodeintelQueryService:\n"
        "    async def list_relations(self, tenant_id: str, project_id: str, repo_id: str, entity_id: str | None, limit: int, offset: int) -> dict:\n"
        "        rows = await conn.fetch(\n"
        '            """\n'
        "            SELECT id, relation_type, source_entity_id\n"
        "            FROM hyeve_codeintel.relations\n"
        "            WHERE tenant_id = $1 AND project_id = $2 AND repo_id = $3 AND source_entity_id = $4\n"
        "            ORDER BY created_at DESC LIMIT $5 OFFSET $6\n"
        '            """,\n'
        "            tenant_id,\n"
        "            project_id,\n"
        "            repo_id,\n"
        "            entity_id,\n"
        "            limit,\n"
        "            offset,\n"
        "        )\n"
        "        return {'items': [dict(r) for r in rows]}\n",
        encoding="utf-8",
    )
    payload = '{"blocks":[{"index":1,"conditions":[{"column":"relation_type","operator":"=","param_variable":"relation_type"}]}]}'
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="codeintel_query_service.py",
        symbol="CodeintelQueryService.list_relations",
        mode="sql_blocks",
        new_code=payload,
    )
    assert result["operation"] == "edit_symbol"
    assert result["changed_files"] == ["codeintel_query_service.py"]
    content = target.read_text(encoding="utf-8")
    assert "${" not in content
    assert "relation_type = $5" in content


def test_edit_symbol_refactor_symbol_optional_filter_asyncpg_query(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        conn = await self._connect()\n"
        "        try:\n"
        '            count_sql = "SELECT COUNT(1) FROM app.items WHERE tenant_id = $1"\n'
        '            rows_sql = "SELECT id FROM app.items WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3"\n'
        "            count_params = [tenant_id]\n"
        "            rows_params = [tenant_id, limit, offset]\n"
        "            total = await conn.fetchval(count_sql, *count_params)\n"
        "            rows = await conn.fetch(rows_sql, *rows_params)\n"
        "        finally:\n"
        "            await conn.close()\n"
        "        return {'items': [dict(r) for r in rows], 'total': int(total or 0)}\n",
        encoding="utf-8",
    )
    payload = json.dumps(
        {
            "pattern": "optional_filter_asyncpg_query",
            "parameter_name": "item_type",
            "column_name": "item_type",
            "sql_variable_names": ["count_sql", "rows_sql"],
            "params_variable_names": ["count_params", "rows_params"],
        }
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="service.py",
        symbol="FixtureService.list_items",
        mode="refactor_symbol",
        new_code=payload,
    )
    assert result["operation"] == "edit_symbol"
    content = target.read_text(encoding="utf-8")
    assert "item_type: str | None = None" in content
    assert "if item_type is not None:" in content
    assert 'count_sql = count_sql + f" AND item_type = ${len(count_params) + 1}"' in content
    assert "count_params.append(item_type)" in content
    assert "try:" in content
    assert "finally:" in content


def test_edit_symbol_refactor_symbol_rejects_invalid_payload(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    target.write_text(
        "class FixtureService:\n"
        "    async def list_items(self, tenant_id: str, limit: int, offset: int) -> dict:\n"
        "        return {'items': []}\n",
        encoding="utf-8",
    )
    result = edit_symbol(
        workspace_root=tmp_path,
        relative_path="service.py",
        symbol="FixtureService.list_items",
        mode="refactor_symbol",
        new_code='{"pattern":"optional_filter_asyncpg_query"}',
        dry_run=True,
    )
    assert result["validated"] is False
    assert result["dry_run_failures"][0]["failure"] == "INVALID_REFACTOR_SYMBOL_PAYLOAD"
