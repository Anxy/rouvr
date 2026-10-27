"""NO_EDIT hardening (REAL-001 T1r1/T3/T5): prose-only model payloads.

Proven root cause: the governed coding model WAS invoked (initial call +
one bounded retry) and answered shape-conforming JSON with empty edits and
a fluent prose ``reason``. The executor adopted that prose as the terminal
detail and dropped codegen usage on the error path, so the terminal was
misread as "model never called" (pre-model NO_EDIT).

Generic fix locked here (no task-specific exceptions):
- empty-edits responses count as model non-conformance; after the existing
  bounded retry budget the terminal is STRUCTURAL (attempts + targets),
  never adopted model prose;
- codegen usage survives the failure path, so invocation stays provable;
- deterministic already-satisfied evidence still permits a clean NO_EDIT;
- missing/non-applicable targets still fail with a precise structural code.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

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
from backend.aix_core.runtime.executor.source_patch_codegen import (
    OpenRouterUsage,
    _edits_from_model_payload,
    generate_source_patch,
    local_fallback_text_replacements,
)
from backend.aix_core.runtime.objective.contracts import RequiredEffectV1
from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.verification.completion_gates import (
    evaluate_completion_gates,
)
from backend.aix_core.intelligence.repository.repository_tools import (
    RepositoryToolbox,
)


_PROSE = (
    "Added the requested helper to app.py and integrated the new behavior "
    "into the existing flow. The change satisfies the objective."
)

_OBJECTIVE = "In app.py introduce a new module helper so downstream behavior changes. Verify with pytest."


def _discovery() -> DiscoveryReport:
    return DiscoveryReport(config_files={"app.py": "OLD = 1\n"})


def _stub_usage() -> OpenRouterUsage:
    return OpenRouterUsage(
        model="stub",
        provider="test",
        ok=True,
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        detail="stub",
    )


def _prose_payload() -> dict[str, Any]:
    return {"edits": [], "reason": _PROSE}


@pytest.mark.asyncio
async def test_prose_only_payload_retried_then_structural_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Applicable source + unmet requirement: model path reached, prose refused."""
    calls = {"n": 0}

    async def _fake_governed(**kwargs: Any) -> tuple[Any, OpenRouterUsage, str]:
        calls["n"] += 1
        return _prose_payload(), _stub_usage(), ""

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.source_patch_codegen._governed_executor_patch_once",
        _fake_governed,
    )
    result = await generate_source_patch(
        objective=_OBJECTIVE,
        discovery=_discovery(),
    )
    # Existing bounded budget: initial call + exactly one retry.
    assert calls["n"] == 2
    assert result.failure_code == "MODEL_RETURNED_NO_EDIT"
    assert result.edits == []
    # Terminal is structural: attempts + targets, never model prose.
    assert "zero applicable edits" in result.reason
    assert "app.py" in result.reason
    assert _PROSE not in result.reason
    # Invocation stays provable on the failure path.
    assert result.usage.total_tokens == 300


@pytest.mark.asyncio
async def test_prose_only_payload_with_bound_target_structural_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same guarantee on the bound-target retry branch (T5 shape)."""
    calls = {"n": 0}

    async def _fake_governed(**kwargs: Any) -> tuple[Any, OpenRouterUsage, str]:
        calls["n"] += 1
        return _prose_payload(), _stub_usage(), ""

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.source_patch_codegen._governed_executor_patch_once",
        _fake_governed,
    )
    result = await generate_source_patch(
        objective=_OBJECTIVE,
        discovery=_discovery(),
        preferred_path="app.py",
    )
    assert calls["n"] == 2
    assert result.failure_code == "MODEL_RETURNED_NO_EDIT"
    assert "zero applicable edits" in result.reason
    assert _PROSE not in result.reason


def test_prose_description_never_counts_as_edit() -> None:
    """A reason-only payload yields zero edits (prose is not an edit)."""
    edits, reason = _edits_from_model_payload(
        {"edits": [], "reason": _PROSE}, discovery=_discovery()
    )
    assert edits == []
    assert reason == _PROSE


_CALC_GREEN = (
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

_GREEN_OBJECTIVE = (
    "In calc.py introduce a new module helper so downstream behavior changes. "
    "Verify with pytest."
)


@pytest.fixture(autouse=True)
def _no_bytecode_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.mark.asyncio
async def test_green_suite_with_unmet_edit_raises_structural_not_satisfied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    """Unchanged tree + green unrelated tests must not read as already satisfied."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text(_CALC_GREEN, encoding="utf-8", newline="\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(_TEST_CALC, encoding="utf-8", newline="\n")
    _init_git_repo(repo)

    calls = {"n": 0}

    async def _fake_governed(**kwargs: Any) -> tuple[Any, OpenRouterUsage, str]:
        calls["n"] += 1
        return _prose_payload(), _stub_usage(), ""

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.source_patch_codegen._governed_executor_patch_once",
        _fake_governed,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.multi_step_execute.infer_required_effects",
        lambda **_kwargs: (),
    )
    events: list[dict[str, Any]] = []
    toolbox = RepositoryToolbox(repository_root=repo, on_event=lambda *a, **k: None)
    with pytest.raises(MultiStepMutationError) as excinfo:
        await run_multi_step_mutation(
            objective=_GREEN_OBJECTIVE,
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
    exc = excinfo.value
    assert exc.code == "MODEL_RETURNED_NO_EDIT"
    # Structural terminal through the real round loop, not prose, not satisfied.
    assert "governed codegen invoked 2x" in exc.detail
    assert "zero applicable edits" in exc.detail
    assert _PROSE not in exc.detail
    # Usage survives the failure path: invocation provable post-terminal.
    assert exc.usage is not None and exc.usage["total_tokens"] == 300
    assert calls["n"] == 2


def test_deterministic_satisfied_evidence_still_permits_noedit() -> None:
    """Genuinely already-satisfied work may still NO_EDIT via deterministic evidence."""
    report = DiscoveryReport(config_files={"app.py": "OLD = 2\n"})
    result = local_fallback_text_replacements(
        report,
        "Replace OLD value.",
        pairs=[("OLD = 1", "OLD = 2")],
    )
    assert result is not None
    assert result.edits == []
    assert result.already_satisfied is True
    assert result.already_satisfied_evidence_refs == ("app.py",)


@pytest.mark.asyncio
async def test_missing_target_fails_clearly() -> None:
    """No applicable target: precise structural code, no model call needed."""
    result = await generate_source_patch(
        objective="In app.py introduce a new module helper.",
        discovery=DiscoveryReport(),
    )
    assert result.edits == []
    assert result.failure_code == "CREATE_CONTEXT_ABSENT"
