"""AIX-DEV-LANE-ROBUST-001: bounded repair on red validation.

WIDEN-001 exposed three AIX-owned gaps in `run_multi_step_mutation`:

1. A no-edit round (MODEL_RETURNED_NO_EDIT) raised terminally without ever
   running validation -- a model "nothing to do" claim stood even when the
   suite was red (stagnation, no repair).
2. A deterministic already_satisfied claim was accepted without validation.
3. A deterministic-path edit followed by test failure raised
   COMPLETION_GATES_FAILED immediately -- the bounded repair loop never ran.

These tests prove, against the real round loop with stubbed codegen only:

- T1: no-edit + red suite -> repair continues; a later round fixing the
  failure completes (no terminal, bounded rounds).
- T2: satisfied-claim + red suite -> claim rejected, repair continues.
- T3: deterministic-apply + red suite -> bounded repair loop runs (rounds >
  1) and still fails closed explicitly (COMPLETION_GATES_FAILED) when the
  failure persists -- never silently, never unbounded.
- T4: satisfied-claim + green suite -> still accepted (no false repair loop).
- T5: a `local`/`local-fallback` usage identity never satisfies governed
  executor enforcement (INT-D11 review: AIX side stays fail-closed).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates
from backend.aix_core.runtime.executor.multi_step_execute import (
    MultiStepMutationError,
    run_multi_step_mutation,
)
from backend.aix_core.runtime.executor.read_file_execute import (
    _looks_like_a_file,
    extract_target_path,
    normalize_repo_relative_path,
    resolve_relative_path,
)
from backend.aix_core.runtime.executor.real_execute import _infer_test_command
from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolbox
from backend.aix_core.runtime.executor.source_patch_codegen import (
    OpenRouterUsage,
    PatchCodegenResult,
    PatchEdit,
    _build_unified_diff,
)
from backend.aix_core.runtime.objective.contracts import RequiredEffectV1
from backend.aix_core.runtime.executor.execution_route import (
    ExecutionKernelRepositoryError,
    ModelExecutionContext,
    contract_from_model_routes,
    enforce_executor_model,
)


_CALC_BUGGY = (
    '"""Freight arithmetic helpers."""\n'
    "\n"
    "\n"
    "def double(x: int) -> int:\n"
    '    """Double the input."""\n'
    "    return x + 2\n"
)

_CALC_FIXED = (
    '"""Freight arithmetic helpers."""\n'
    "\n"
    "\n"
    "def double(x: int) -> int:\n"
    '    """Double the input."""\n'
    "    return x * 2\n"
)

_TEST_CALC = (
    "from calc import double\n"
    "\n"
    "\n"
    "def test_double_multiplies() -> None:\n"
    "    assert double(21) == 42\n"
)


@pytest.fixture(autouse=True)
def _no_bytecode_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub rounds land within one mtime second, so timestamp-based .pyc
    validation would reuse pre-fix bytecode and fake a red suite. Disable
    bytecode caching (in-process and in the inner pytest subprocess)."""
    import sys

    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def _calc_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text(_CALC_BUGGY, encoding="utf-8", newline="\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(_TEST_CALC, encoding="utf-8", newline="\n")
    _init_git_repo(repo)
    return repo


_OBJECTIVE = (
    "In calc.py fix double to multiply its input by 2 (currently adds 2). "
    "Verify with pytest."
)


def _run_kwargs(repo: Path, toolbox: RepositoryToolbox, events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "objective": _OBJECTIVE,
        "toolbox": toolbox,
        "repository_root": repo,
        "settings": None,
        "model_override": None,
        "budget_mgr": None,
        "on_event": lambda *a, **k: None,
        "events": events,
        "allowed_files": ("calc.py", "tests/test_calc.py"),
        "task_family": "single_file_helper",
        "evaluate_completion_gates": evaluate_completion_gates,
        "infer_test_command": _infer_test_command,
        "normalize_repo_relative_path": normalize_repo_relative_path,
        "looks_like_a_file": _looks_like_a_file,
        "extract_target_path": extract_target_path,
        "resolve_relative_path": resolve_relative_path,
        "max_rounds": 3,
        "required_effects": (RequiredEffectV1(kind="LITERAL_REPLACE"),),
    }


def _fix_edit(current: str) -> PatchEdit:
    return PatchEdit(
        path="calc.py",
        unified_diff=_build_unified_diff(
            path="calc.py", old_content=current, new_content=_CALC_FIXED
        ),
        new_content=_CALC_FIXED,
        reason="stub repair: multiply instead of add",
    )


def _current_calc(kwargs: dict[str, Any]) -> str:
    discovery = kwargs.get("discovery")
    if discovery is not None:
        current = (discovery.config_files or {}).get("calc.py")
        if current:
            return current
    return _CALC_BUGGY


def _stub_usage() -> OpenRouterUsage:
    return OpenRouterUsage(model="stub", provider="test", ok=True, detail="stub")


@pytest.mark.asyncio
async def test_noedit_with_red_validation_repairs_instead_of_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """T1: MODEL_RETURNED_NO_EDIT on a red suite must fuel repair, not terminal."""
    repo = _calc_repo(tmp_path)
    calls = {"n": 0}

    async def _fake_codegen(**kwargs: Any) -> PatchCodegenResult:
        calls["n"] += 1
        if calls["n"] == 1:
            return PatchCodegenResult(
                edits=[],
                no_files_identified=False,
                failure_code="MODEL_RETURNED_NO_EDIT",
                reason="stub: nothing to change, already correct",
                usage=_stub_usage(),
            )
        return PatchCodegenResult(edits=[_fix_edit(_current_calc(kwargs))], usage=_stub_usage())

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.multi_step_execute.generate_source_patch",
        _fake_codegen,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.multi_step_execute.infer_required_effects",
        lambda **_kwargs: (),
    )

    events: list[dict[str, Any]] = []
    toolbox = RepositoryToolbox(repository_root=repo, on_event=lambda *a, **k: None)
    result = await run_multi_step_mutation(**_run_kwargs(repo, toolbox, events))

    assert result.ok is True
    assert len(result.rounds) >= 2
    assert calls["n"] >= 2
    assert "model_returned_no_edit_with_red_validation" in result.rounds[0].gate_reasons
    assert (repo / "calc.py").read_text(encoding="utf-8") == _CALC_FIXED


@pytest.mark.asyncio
async def test_satisfied_claim_with_red_validation_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """T2: already_satisfied on a red suite must not be accepted."""
    repo = _calc_repo(tmp_path)
    calls = {"n": 0}

    async def _fake_codegen(**kwargs: Any) -> PatchCodegenResult:
        calls["n"] += 1
        if calls["n"] == 1:
            return PatchCodegenResult(
                edits=[],
                no_files_identified=True,
                already_satisfied=True,
                already_satisfied_evidence_refs=("calc.py",),
                reason="stub: objective already satisfied",
                usage=_stub_usage(),
            )
        return PatchCodegenResult(edits=[_fix_edit(_current_calc(kwargs))], usage=_stub_usage())

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.multi_step_execute.generate_source_patch",
        _fake_codegen,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.multi_step_execute.infer_required_effects",
        lambda **_kwargs: (),
    )

    events: list[dict[str, Any]] = []
    toolbox = RepositoryToolbox(repository_root=repo, on_event=lambda *a, **k: None)
    result = await run_multi_step_mutation(**_run_kwargs(repo, toolbox, events))

    assert result.ok is True
    assert len(result.rounds) >= 2
    assert "satisfied_claim_with_red_validation" in result.rounds[0].gate_reasons
    assert (repo / "calc.py").read_text(encoding="utf-8") == _CALC_FIXED


@pytest.mark.asyncio
async def test_deterministic_failure_gets_bounded_repair_then_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """T3: deterministic apply + red suite runs the repair loop, then fails
    explicitly with COMPLETION_GATES_FAILED when nothing fixes it. Bounded by
    max_rounds; never an immediate first-round terminal, never a hang."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "flag.py").write_text("MODE = 'OLD'\n", encoding="utf-8", newline="\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_flag.py").write_text(
        "def test_always_red() -> None:\n    assert False, 'broken suite stays broken'\n",
        encoding="utf-8",
        newline="\n",
    )
    _init_git_repo(repo)

    events: list[dict[str, Any]] = []
    toolbox = RepositoryToolbox(repository_root=repo, on_event=lambda *a, **k: None)
    with pytest.raises(MultiStepMutationError) as excinfo:
        await run_multi_step_mutation(
            objective="In flag.py replace 'OLD' with 'NEW'. Verify with pytest.",
            toolbox=toolbox,
            repository_root=repo,
            settings=None,
            model_override=None,
            budget_mgr=None,
            on_event=lambda *a, **k: None,
            events=events,
            allowed_files=("flag.py",),
            task_family="single_file_helper",
            evaluate_completion_gates=evaluate_completion_gates,
            infer_test_command=_infer_test_command,
            normalize_repo_relative_path=normalize_repo_relative_path,
            looks_like_a_file=_looks_like_a_file,
            extract_target_path=extract_target_path,
            resolve_relative_path=resolve_relative_path,
            max_rounds=3,
        )

    assert excinfo.value.code == "COMPLETION_GATES_FAILED"
    # The deterministic edit applied (proves round 0 ran), and the repair loop
    # re-evaluated more than once (proves no immediate terminal).
    assert "NEW" in (repo / "flag.py").read_text(encoding="utf-8")
    assert excinfo.value.detail.count("tests_failed") >= 2


@pytest.mark.asyncio
async def test_satisfied_claim_with_green_suite_still_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """T4: the probe must not break the genuine no-op path (no false repair)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text(_CALC_FIXED, encoding="utf-8", newline="\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(_TEST_CALC, encoding="utf-8", newline="\n")
    _init_git_repo(repo)

    async def _fake_codegen(**kwargs: Any) -> PatchCodegenResult:
        return PatchCodegenResult(
            edits=[],
            no_files_identified=True,
            already_satisfied=True,
            already_satisfied_evidence_refs=("calc.py",),
            reason="stub: already fixed",
            usage=_stub_usage(),
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.multi_step_execute.generate_source_patch",
        _fake_codegen,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.multi_step_execute.infer_required_effects",
        lambda **_kwargs: (),
    )

    events: list[dict[str, Any]] = []
    toolbox = RepositoryToolbox(repository_root=repo, on_event=lambda *a, **k: None)
    result = await run_multi_step_mutation(
        objective=_OBJECTIVE,
        toolbox=toolbox,
        repository_root=repo,
        settings=None,
        model_override=None,
        budget_mgr=None,
        on_event=lambda *a, **k: None,
        events=events,
        allowed_files=("calc.py", "tests/test_calc.py"),
        task_family="single_file_helper",
        evaluate_completion_gates=evaluate_completion_gates,
        infer_test_command=_infer_test_command,
        normalize_repo_relative_path=normalize_repo_relative_path,
        looks_like_a_file=_looks_like_a_file,
        extract_target_path=extract_target_path,
        resolve_relative_path=resolve_relative_path,
        max_rounds=3,
        required_effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
    )

    assert result.ok is True
    assert result.code == "ALREADY_SATISFIED"


def test_local_fallback_identity_never_passes_governed_enforcement() -> None:
    """T5 (INT-D11 review): deterministic local results carry provider `local`
    and must fail closed against any governed route, never masquerade as it."""
    contract = contract_from_model_routes(
        tenant_id="t",
        work_item_id="w",
        model_routes={"executor": {"provider": "openrouter", "model_id": "qwen/qwen3-coder-next"}},
    )
    ctx = ModelExecutionContext(route=contract)
    with pytest.raises(ExecutionKernelRepositoryError, match="EXECUTOR_MODEL_MISMATCH"):
        enforce_executor_model(ctx, provider="local", model_id="local-fallback")
