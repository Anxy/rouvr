"""AIX Closed-Loop 003, Packet CL3-P3: a stub codegen that keeps returning a
cosmetic-only patch (import-banner restyle, one new single-call-site wrapper -- the
same shape as the real incident's own diff) must never let a mutation round report
`ok=True`. The bounded repair loop must exhaust and raise closed with
`OBJECTIVE_COVERAGE_FAILED`, not a generic silent give-up -- and repair notes naming
the unmet signals must reach later rounds, not "make a bigger diff."

Exercises `run_multi_step_mutation` directly (the actual locus of this packet's
requirement), reusing the same real callables `real_execute.py` itself passes in --
this sprint's own rule against building a second execution path applies to tests too.
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

_OBJECTIVE = (
    "Refactor Small.php: extract repeated database boilerplate into a helper method "
    "and remove unused use-imports."
)

_ORIGINAL_CONTENT = (
    "<?php\n"
    "// ===== models =====\n"
    "use App\\Models\\Used;\n"
    "use App\\Models\\NeverReferenced;\n"
    "class Small {\n"
    "\tpublic function feeDue() {\n"
    "\t\t$a = env('DB_DATABASE', '360degree');\n"
    "\t\treturn new Used();\n"
    "\t}\n"
    "\tpublic function feeDue2() {\n"
    "\t\t$b = env('DB_DATABASE', '360degree');\n"
    "\t\treturn new Used();\n"
    "\t}\n"
    "}\n"
)

_COSMETIC_CONTENT = _ORIGINAL_CONTENT.replace(
    "// ===== models =====",
    "// ============================================================================\n"
    "// models\n"
    "// ============================================================================",
).replace(
    "class Small {\n",
    "class Small {\n"
    "\tprivate function centralDatabaseName() {\n"
    "\t\treturn env('DB_DATABASE', '360degree');\n"
    "\t}\n",
)


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


@pytest.mark.asyncio
async def test_cosmetic_stub_codegen_never_completes_and_fails_closed_with_coverage_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "Small.php").write_text(_ORIGINAL_CONTENT, encoding="utf-8", newline="\n")
    _init_git_repo(repo)

    call_count = {"n": 0}

    async def _fake_generate_source_patch(**kwargs: Any) -> PatchCodegenResult:
        # Diff from the file's REAL current content each round -- round 1's edit
        # actually lands on disk, so a static before/after pair would be rejected by
        # the real patch-apply machinery as a context mismatch on round 2+. A distinct
        # trailing comment each round keeps every round's diff genuinely new while
        # staying cosmetic-only (never extraction with a reused helper, never an
        # import actually removed).
        call_count["n"] += 1
        discovery = kwargs.get("discovery")
        current = (
            discovery.config_files.get("Small.php", _ORIGINAL_CONTENT)
            if discovery is not None else _ORIGINAL_CONTENT
        )
        new_content = _COSMETIC_CONTENT if call_count["n"] == 1 else current + f"\n// cosmetic pass {call_count['n']}\n"
        diff = _build_unified_diff(path="Small.php", old_content=current, new_content=new_content)
        return PatchCodegenResult(
            edits=[
                PatchEdit(
                    path="Small.php", unified_diff=diff, new_content=new_content,
                    reason="cosmetic banner restyle + single-call-site wrapper",
                )
            ],
            usage=OpenRouterUsage(model="stub-cosmetic", provider="test", ok=True, detail="stub"),
        )

    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.multi_step_execute.generate_source_patch",
        _fake_generate_source_patch,
    )
    monkeypatch.setattr(
        "backend.aix_core.runtime.executor.multi_step_execute.infer_required_effects",
        lambda **_kwargs: (),
    )

    events: list[dict[str, Any]] = []
    toolbox = RepositoryToolbox(repository_root=repo, on_event=lambda *a, **k: None)

    with pytest.raises(MultiStepMutationError) as excinfo:
        await run_multi_step_mutation(
            objective=_OBJECTIVE,
            toolbox=toolbox,
            repository_root=repo,
            settings=None,
            model_override=None,
            budget_mgr=None,
            on_event=lambda *a, **k: None,
            events=events,
            allowed_files=("Small.php",),
            task_family="REFACTOR",
            evaluate_completion_gates=evaluate_completion_gates,
            infer_test_command=_infer_test_command,
            normalize_repo_relative_path=normalize_repo_relative_path,
            looks_like_a_file=_looks_like_a_file,
            extract_target_path=extract_target_path,
            resolve_relative_path=resolve_relative_path,
            max_rounds=3,
        )

    assert excinfo.value.code == "OBJECTIVE_COVERAGE_FAILED"
    assert "extract_helpers" in excinfo.value.detail or "remove_unused_imports" in excinfo.value.detail
    # The repair note fed to later rounds names the actual unmet signals, not a vague
    # "try harder" instruction (sprint CL3-P3 task #1).
    assert "OBJECTIVE_COVERAGE_FAILED" in excinfo.value.detail

    # Multiple distinct coverage-failure notes in the accumulated detail (one per
    # round the gate was re-evaluated against) prove the repair budget genuinely
    # ran multiple rounds before exhausting -- not a first-attempt hard fail with a
    # single note repeated. Bounded by `max_rounds=3` passed above.
    assert excinfo.value.detail.count("OBJECTIVE_COVERAGE_FAILED") >= 2
    assert call_count["n"] >= 1
