from __future__ import annotations

import difflib
import subprocess
from pathlib import Path

import pytest

from backend.services.devexecutor_mutation_tools_service import (
    edit_file,
    update_call_kwarg,
    write_file,
)
from backend.services.devexecutor_patch_engine_service import apply_patch_with_outcome
from backend.services.devexecutor_python_symbol_service import resolve_python_symbol_range

pytestmark = pytest.mark.enable_pyright_lsp


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True, capture_output=True)


def _commit_all(root: Path, message: str = "init") -> None:
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", message], check=True, capture_output=True)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _patch_for_file(relative_path: str, before: str, after: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{relative_path}",
        tofile=f"b/{relative_path}",
        lineterm="",
    )
    lines = [f"diff --git a/{relative_path} b/{relative_path}", *list(diff)]
    return "\n".join(lines) + "\n"


def test_edit_stress_multi_file_patch_updates_impl_and_consumer_with_touched_file_validation(
    tmp_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    worker = tmp_path / "worker.py"
    consumer = tmp_path / "consumer.py"
    worker_before = "def calc(value: int) -> int:\n    return value + 1\n"
    consumer_before = "from worker import calc\n\nRESULT: int = calc(1)\n"
    worker_after = (
        "def normalize(value: int) -> int:\n"
        "    return value\n\n"
        "def calc(value: int) -> int:\n"
        "    return normalize(value) + 1\n"
    )
    consumer_after = "from worker import calc, normalize\n\nRESULT: int = calc(normalize(1))\n"
    _write(worker, worker_before)
    _write(consumer, consumer_before)
    _commit_all(tmp_path)

    patch = _patch_for_file("worker.py", worker_before, worker_after) + _patch_for_file(
        "consumer.py", consumer_before, consumer_after
    )
    execution_context = {"ayrpack": {"spine": []}}

    result = apply_patch_with_outcome(
        workspace_root=tmp_path,
        patch_text=patch,
        execution_context=execution_context,
    )

    assert result.status == "applied"
    assert result.reason_code == "ok"
    assert result.semantic_precheck_passed is True
    assert result.pyright_passed is True
    assert result.changed_files == ["worker.py", "consumer.py"]
    assert result.post_apply_validation_scope == "touched_files"
    assert result.post_apply_validation_targets == ["worker.py", "consumer.py"]
    assert [item.path for item in result.post_apply_validation_results] == ["worker.py", "consumer.py"]
    assert worker.read_text(encoding="utf-8") == worker_after
    assert consumer.read_text(encoding="utf-8") == consumer_after
    assert execution_context["ayrpack"]["spine"][-1]["kind"] == "attestation"
    assert execution_context["ayrpack"]["spine"][-1]["status"] == "passed"


def test_edit_stress_edit_file_handles_multiple_blocks_in_one_python_file(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "maths.py"
    before = "def total(a: int, b: int) -> int:\n    return a + b\n"
    after = (
        "def _normalize(value: int) -> int:\n"
        "    return value\n\n"
        "def total(a: int, b: int) -> int:\n"
        "    return _normalize(a) + _normalize(b)\n"
    )
    _write(target, before)
    _commit_all(tmp_path)

    result = edit_file(
        workspace_root=tmp_path,
        relative_path="maths.py",
        search_replace_blocks=(
            "<<<<<<< SEARCH\n"
            "def total(a: int, b: int) -> int:\n"
            "=======\n"
            "def _normalize(value: int) -> int:\n"
            "    return value\n\n"
            "def total(a: int, b: int) -> int:\n"
            ">>>>>>> REPLACE\n"
            "<<<<<<< SEARCH\n"
            "    return a + b\n"
            "=======\n"
            "    return _normalize(a) + _normalize(b)\n"
            ">>>>>>> REPLACE\n"
        ),
    )

    assert result["operation"] == "edit_file"
    assert result["adapter"] == "git_apply"
    assert result["block_count"] == 2
    assert result["changed_files"] == ["maths.py"]
    assert target.read_text(encoding="utf-8") == after


def test_edit_stress_update_call_kwarg_modifies_only_the_target_callsite(tmp_path: Path) -> None:
    target = tmp_path / "service.py"
    before = (
        "class DemoService:\n"
        "    async def fetch(self, path: str) -> dict[str, object]:\n"
        "        return await self._request(\n"
        "            path=path,\n"
        "            timeout=5,\n"
        "        )\n"
    )
    _write(target, before)

    result = update_call_kwarg(
        workspace_root=tmp_path,
        relative_path="service.py",
        symbol="DemoService.fetch",
        callee="self._request",
        kwarg="timeout",
        new_value="15",
    )

    assert result["operation"] == "update_call_kwarg"
    assert result["changed_files"] == ["service.py"]
    updated = target.read_text(encoding="utf-8")
    assert "            timeout=15,\n" in updated
    assert "        return await self._request(\n" in updated


def test_edit_stress_write_file_can_add_new_module_and_lsp_resolves_new_symbol(tmp_path: Path) -> None:
    target = tmp_path / "router.py"

    result = write_file(
        workspace_root=tmp_path,
        relative_path="router.py",
        content=(
            "def route(path: str):\n"
            "    def _decorator(fn):\n"
            "        return fn\n"
            "    return _decorator\n\n"
            "@route('/health')\n"
            "async def health_check() -> dict[str, str]:\n"
            "    return {'status': 'ok'}\n"
        ),
    )
    symbol = resolve_python_symbol_range(file_path=target, symbol_name="health_check")

    assert result["operation"] == "write_file"
    assert result["changed_files"] == ["router.py"]
    assert symbol.qualified_name == "health_check"
    assert symbol.resolution_source == "pyright_lsp"
    assert symbol.decorator_lines == (6,)
    assert symbol.semantic_valid is True


def test_edit_stress_precheck_rejects_syntax_break_without_touching_disk(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "demo.py"
    original = "value: int = 1\n"
    _write(target, original)
    updated = "value: int =\n"

    patch = _patch_for_file("demo.py", original, updated)
    execution_context = {"ayrpack": {"spine": []}}

    result = apply_patch_with_outcome(
        workspace_root=tmp_path,
        patch_text=patch,
        execution_context=execution_context,
    )

    assert result.status == "failed"
    assert result.reason_code == "pyright_precheck_failed"
    assert result.semantic_precheck_passed is False
    assert target.read_text(encoding="utf-8") == original
    assert execution_context["ayrpack"]["spine"][-1]["kind"] == "attestation"
    assert execution_context["ayrpack"]["spine"][-1]["status"] == "failed"


def test_edit_stress_precheck_rejects_type_break_without_touching_disk(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "demo.py"
    original = "value: int = 1\n"
    _write(target, original)

    patch = _patch_for_file("demo.py", original, 'value: int = "bad"\n')
    execution_context = {"ayrpack": {"spine": []}}

    result = apply_patch_with_outcome(
        workspace_root=tmp_path,
        patch_text=patch,
        execution_context=execution_context,
    )

    assert result.status == "failed"
    assert result.reason_code == "pyright_precheck_failed"
    assert result.semantic_precheck_passed is False
    assert target.read_text(encoding="utf-8") == original
    assert execution_context["ayrpack"]["spine"][-1]["kind"] == "attestation"
    assert execution_context["ayrpack"]["spine"][-1]["status"] == "failed"


def test_edit_stress_postapply_pyright_reject_rolls_back_cleanly(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "demo.py"
    original = "value: int = 1\n"
    _write(target, original)
    _commit_all(tmp_path)

    patch = _patch_for_file("demo.py", original, 'value: int = "bad"\n')
    execution_context = {"ayrpack": {"spine": []}}

    result = apply_patch_with_outcome(
        workspace_root=tmp_path,
        patch_text=patch,
        execution_context=execution_context,
        semantic_validator=lambda **_kwargs: (True, ""),
    )

    assert result.status == "failed"
    assert result.reason_code == "pyright_failed"
    assert result.pyright_passed is False
    assert result.post_apply_validation_scope == "touched_files"
    assert result.post_apply_validation_targets == ["demo.py"]
    assert target.read_text(encoding="utf-8") == original
    assert execution_context["ayrpack"]["spine"][-1]["status"] == "failed"


def test_edit_stress_missing_terminal_newline_fallback_is_audited(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "demo.py"
    _write(target, "value = 1")
    execution_context = {"ayrpack": {"spine": []}}

    result = edit_file(
        workspace_root=tmp_path,
        relative_path="demo.py",
        search_replace_blocks="<<<<<<< SEARCH\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n",
        execution_context=execution_context,
    )

    assert result["adapter"] == "write_file_fallback"
    assert result["fallback_reason"] == "missing_terminal_newline"
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert execution_context["ayrpack"]["spine"][-1]["kind"] == "decision"
    assert execution_context["ayrpack"]["spine"][-1]["summary"] == "fallback edit primitive used"


def test_edit_stress_zero_match_rejects_without_mutating_bytes(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "demo.py"
    original = "def demo() -> int:\n    return 1\n"
    _write(target, original)

    with pytest.raises(ValueError, match="SEARCH_REPLACE_NOT_FOUND"):
        edit_file(
            workspace_root=tmp_path,
            relative_path="demo.py",
            search_replace_blocks=(
                "<<<<<<< SEARCH\n" "    return 2\n" "=======\n" "    return 3\n" ">>>>>>> REPLACE\n"
            ),
        )

    assert target.read_text(encoding="utf-8") == original


def test_edit_stress_ambiguous_match_rejects_without_mutating_bytes(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "dup.py"
    original = "value = 1\nvalue = 1\n"
    _write(target, original)

    with pytest.raises(ValueError, match="SEARCH_REPLACE_AMBIGUOUS"):
        edit_file(
            workspace_root=tmp_path,
            relative_path="dup.py",
            search_replace_blocks=("<<<<<<< SEARCH\n" "value = 1\n" "=======\n" "value = 2\n" ">>>>>>> REPLACE\n"),
        )

    assert target.read_text(encoding="utf-8") == original
