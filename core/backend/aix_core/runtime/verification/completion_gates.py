"""Completion-gate enforcement: COMPLETED is never reached without real evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.objective_cycle.objective_coverage import check_objective_coverage
from backend.aix_core.runtime.verification.quality_gates import build_report
from backend.aix_core.runtime.governance.secret_boundary import (
    ResolvedSecretV1,
    SecretBoundary,
    SecretBoundaryError,
    SecretRefV1,
)
from backend.aix_core.runtime.verification.verification import IndependentVerifier, VerificationInputV1

RUN_ARTIFACT_PREFIXES = (
    "aix-generated/",
    ".aix/",
    "execution-artifacts/",
    "workhub-payload/",
    ".tmp/",
)


@dataclass
class CompletionGateResult:
    passed: bool
    reasons: tuple[str, ...]
    gates: dict[str, bool] = field(default_factory=dict)
    changed_source_files: list[str] = field(default_factory=list)
    run_artifacts: list[str] = field(default_factory=list)
    verification_verdict: str = ""


def _is_test_only_path(path: str) -> bool:
    """True for test-suite paths (tests/ dirs or test_*.py/*_test.py files)."""
    norm = str(path or "").replace("\\", "/").strip().lstrip("/")
    if not norm:
        return False
    if norm == "tests" or norm.startswith("tests/"):
        return True
    base = norm.rsplit("/", 1)[-1]
    return base.startswith("test_") or base.endswith("_test.py")


def _is_run_artifact(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in RUN_ARTIFACT_PREFIXES)


def split_changed_paths(stats: dict[str, dict[str, int]]) -> tuple[list[str], list[str]]:
    source: list[str] = []
    artifacts: list[str] = []
    for path in stats:
        (artifacts if _is_run_artifact(path) else source).append(path)
    return source, artifacts


def _is_meaningful_change(path_stats: dict[str, int]) -> bool:
    return int(path_stats.get("additions", 0)) > 0 or int(path_stats.get("deletions", 0)) > 0


def _count_unified_diff_changed_lines(diff_text: str) -> int:
    """Count +/- content lines in a unified diff (exclude ---/+++ headers)."""
    count = 0
    for line in str(diff_text or "").splitlines():
        if not line or line.startswith(("---", "+++", "@@", "\\")):
            continue
        if line.startswith("+") or line.startswith("-"):
            count += 1
    return count


_REFACTOR_SPECIFIC_GOALS = (
    "extract",
    "group",
    "split",
    "move",
    "organize",
    "organise",
    "readability",
    "maintainability",
    "performance",
    "structure",
    "duplicate",
    "middleware",
    "route group",
    "controller",
    "namespace",
    "simplify",
    "section",
    "comment",
    "format",
)


def is_vague_refactor_objective(objective: str) -> bool:
    lowered = str(objective or "").casefold()
    if not any(token in lowered for token in ("refactor", "restructure", "clean up")):
        return False
    return not any(goal in lowered for goal in _REFACTOR_SPECIFIC_GOALS)


def refactor_change_is_substantive(*, diff_text: str, diff_stats: dict[str, dict[str, int]]) -> bool:
    """Reject cosmetic-only refactors (e.g. one Route::Post casing tweak on a huge file)."""
    changed_lines = _count_unified_diff_changed_lines(diff_text)
    if changed_lines >= 8:
        return True
    total_add = sum(int(v.get("additions", 0)) for v in diff_stats.values())
    total_del = sum(int(v.get("deletions", 0)) for v in diff_stats.values())
    if total_add + total_del >= 8:
        return True
    return changed_lines >= 4


def evaluate_completion_gates(
    *,
    discovery: DiscoveryReport,
    diff_stats: dict[str, dict[str, int]],
    diff_text: str,
    test_result: dict[str, Any] | None,
    already_satisfied: bool = False,
    already_satisfied_reason: str = "",
    already_satisfied_evidence_refs: tuple[str, ...] = (),
    secret_values: list[str] | None = None,
    diff_only_verification_allowed: bool = False,
    state_assertions_passed: bool = True,
    state_assertion_detail: str = "",
    task_family: str = "",
    objective: str = "",
    required_effects: tuple = (),
    before_files: dict[str, str] | None = None,
    after_files: dict[str, str] | None = None,
    step_contract: object | None = None,
) -> CompletionGateResult:
    """Fail-closed gates. Diff-only verification is an explicit caller opt-in when
    no test surface exists — never implied by filename magic.

    SEM-001-W4: when ``step_contract`` carries nonempty continuity refs,
    ``product_structure_coverage`` is required and diff_only / presence soft-pass
    alone cannot clear verification.
    """
    from backend.aix_core.runtime.verification.product_structure_coverage import (
        evaluate_product_structure_coverage,
        step_has_continuity_refs,
    )

    reasons: list[str] = []
    gates: dict[str, bool] = {}

    continuity_required = step_has_continuity_refs(step_contract)
    # Continuity Law (C): never allow diff-only soft verification for ref-bearing steps.
    if continuity_required:
        diff_only_verification_allowed = False
        # Presence soft-pass must not clear STEP_PASSED when refs are nonempty.
        if already_satisfied and str(already_satisfied_reason or "").startswith(
            "greenfield_php_sql"
        ):
            already_satisfied = False
            reasons.append(
                "SEM001_W4: greenfield presence soft-pass disabled for ref-bearing step"
            )

    changed_source_files, run_artifacts = split_changed_paths(diff_stats)
    meaningful_source_files = [p for p in changed_source_files if _is_meaningful_change(diff_stats[p])]

    gates["source_read"] = discovery.files_read > 0
    if not gates["source_read"]:
        reasons.append("no real source file was read during discovery")

    if already_satisfied and not already_satisfied_reason:
        reasons.append("already_satisfied=True requires a recorded reason")
    gates["source_changed"] = bool(meaningful_source_files) or (
        already_satisfied and bool(already_satisfied_reason)
    )
    if not gates["source_changed"]:
        reasons.append("no real source file was changed and the objective was not proven already satisfied")

    family = str(task_family or "").strip().upper()
    if (
        family == "REFACTOR"
        and is_vague_refactor_objective(objective)
        and meaningful_source_files
        and not refactor_change_is_substantive(diff_text=diff_text, diff_stats=diff_stats)
    ):
        gates["refactor_substantive"] = False
        reasons.append(
            "refactor change was cosmetic-only (too few lines changed); specify what to "
            "refactor (e.g. group routes, extract middleware, improve structure) and retry"
        )
    else:
        gates["refactor_substantive"] = True

    # AIX Closed-Loop Execution -- Sprint 003 (CL3-P1): completion is not the same
    # thing as objective coverage -- see `objective_coverage.py` and
    # `.validation/closed-loop-003-baseline.md` for the incident this closes
    # (a cosmetic import-banner restyle that cleared `refactor_substantive`'s
    # line-count floor while doing none of the objective's named work). Runs
    # regardless of `task_family` -- `check_objective_coverage` is itself a no-op
    # (`satisfied=True`) for any objective that names no extract/unused-import/
    # section-comment signal, so this never fails an unrelated bound EDIT.
    structural_kinds = {
        "GROUP_METHODS",
        "MOVE_METHODS",
        "EXTRACT_HELPER",
        "EXTRACT_TO_FILES",
        "STRIP_UNUSED_IMPORTS",
        "PRESERVE_PUBLIC_INTERFACE",
    }
    structural_effects = tuple(
        effect
        for effect in required_effects
        if getattr(effect, "kind", "") in structural_kinds
    )
    # Docs/config text replacements are not PHP/structural targets. Planner noise
    # that attaches PRESERVE_PUBLIC_INTERFACE to a README edit must not fail the gate.
    _NON_STRUCTURAL_SUFFIXES = (
        ".md",
        ".txt",
        ".css",
        ".html",
        ".htm",
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".env",
    )
    structural_paths = {
        p
        for p in set(after_files or {})
        if not str(p).lower().endswith(_NON_STRUCTURAL_SUFFIXES)
    }
    # Only mutated paths are structural targets. Discovery may bind many sibling
    # files (live S9: .tsx/.ts decoys); verifying unchanged neighbors as
    # PRESERVE_PUBLIC_INTERFACE falsely fails with UNSUPPORTED_CAPABILITY.
    if structural_effects and structural_paths and (before_files or after_files):
        from backend.aix_core.runtime.verification.structural_verify import verify_structural_effects

        missing: list[str] = []
        details: list[str] = []
        created_files = {
            p: (after_files or {}).get(p, "")
            for p in (after_files or {})
            if p not in (before_files or {}) or not str((before_files or {}).get(p) or "").strip()
        }
        has_extract_to_files = any(
            getattr(effect, "kind", "") == "EXTRACT_TO_FILES" for effect in structural_effects
        )
        for path in structural_paths:
            # New service files are evidence for EXTRACT_TO_FILES, not independent
            # structural targets — verify against the original source file only.
            if has_extract_to_files and path in created_files and path not in (before_files or {}):
                continue
            before = (before_files or {}).get(path, "")
            after = (after_files or {}).get(path, before)
            if not before and not after:
                continue
            sv = verify_structural_effects(
                path=path,
                before=before,
                after=after,
                effects=structural_effects,  # type: ignore[arg-type]
                diff_text=diff_text,
                created_files=created_files,
            )
            details.append(sv.detail)
            if not sv.passed:
                missing.extend(sv.missing_effects)
        gates["structural_effects"] = not missing
        if missing:
            reasons.append(
                f"STRUCTURAL_EFFECTS_FAILED: missing {sorted(set(missing))}; " + "; ".join(details)
            )
        if meaningful_source_files:
            pre_image_snippet = "\n".join(
                discovery.config_files.get(p, "") for p in meaningful_source_files
            )
            workspace_files = {
                **dict(discovery.config_files or {}),
                **dict(before_files or {}),
                **dict(after_files or {}),
            }
            coverage_result = check_objective_coverage(
                objective=objective, diff_text=diff_text, task_family=family,
                pre_image_snippet=pre_image_snippet,
                workspace_files=workspace_files,
            )
            effect_backed_signals = {
                "remove_unused_imports": "STRIP_UNUSED_IMPORTS",
                "extract_helpers": "EXTRACT_HELPER",
                "extract_to_new_types": "EXTRACT_TO_FILES",
            }
            uncovered = {
                signal for signal in coverage_result.missing_signals
                if effect_backed_signals.get(signal) not in {
                    effect.kind for effect in structural_effects
                }
            }
            coverage_satisfied = coverage_result.satisfied or not uncovered
            gates["objective_coverage"] = coverage_satisfied and gates["structural_effects"]
            if not coverage_satisfied:
                reasons.append(f"OBJECTIVE_COVERAGE_FAILED: {coverage_result.detail}")
        else:
            gates["objective_coverage"] = gates["structural_effects"]
    elif structural_effects and not structural_paths:
        gates["structural_effects"] = True
        if meaningful_source_files:
            pre_image_snippet = "\n".join(
                discovery.config_files.get(p, "") for p in meaningful_source_files
            )
            workspace_files = {
                **dict(discovery.config_files or {}),
                **dict(before_files or {}),
                **dict(after_files or {}),
            }
            coverage_result = check_objective_coverage(
                objective=objective, diff_text=diff_text, task_family=family,
                pre_image_snippet=pre_image_snippet,
                workspace_files=workspace_files,
            )
            gates["objective_coverage"] = coverage_result.satisfied
            if not coverage_result.satisfied:
                reasons.append(f"OBJECTIVE_COVERAGE_FAILED: {coverage_result.detail}")
        else:
            gates["objective_coverage"] = True
    elif meaningful_source_files:
        pre_image_snippet = "\n".join(
            discovery.config_files.get(p, "") for p in meaningful_source_files
        )
        workspace_files = {
            **dict(discovery.config_files or {}),
            **dict(before_files or {}),
            **dict(after_files or {}),
        }
        coverage_result = check_objective_coverage(
            objective=objective, diff_text=diff_text, task_family=family,
            pre_image_snippet=pre_image_snippet,
            workspace_files=workspace_files,
        )
        gates["objective_coverage"] = coverage_result.satisfied
        if not coverage_result.satisfied:
            reasons.append(f"OBJECTIVE_COVERAGE_FAILED: {coverage_result.detail}")
    else:
        gates["objective_coverage"] = True

    # SEM-001-W4: Continuity Law (C) — deterministic product_structure_coverage.
    coverage = evaluate_product_structure_coverage(
        step_contract=step_contract,
        after_files=after_files,
        before_files={
            **dict(getattr(discovery, "config_files", None) or {}),
            **dict(before_files or {}),
        },
        diff_text=diff_text,
    )
    if coverage.checked:
        gates["product_structure_coverage"] = bool(coverage.ok)
        if not coverage.ok:
            reasons.append(f"PRODUCT_STRUCTURE_COVERAGE_FAILED: {coverage.detail}")
    else:
        # Empty-ref steps: legacy existence / diff_only path; key present as True
        # so SEM-001-W4 empty-ref assertions and continuity checks stay stable.
        gates["product_structure_coverage"] = True

    # Soft presence "tests" must not alone greenlight ref-bearing steps.
    if continuity_required and isinstance(test_result, dict):
        soft_cmd = " ".join(str(x) for x in (test_result.get("command") or []))
        if "greenfield_php_sql_presence_verified" in soft_cmd.casefold():
            if not coverage.ok:
                test_result = {
                    **test_result,
                    "returncode": 1,
                    "stderr": (
                        str(test_result.get("stderr") or "")
                        + "; SEM001_W4: presence soft-pass insufficient for ref-bearing step"
                    ).strip("; "),
                }

    gates["diff_exists"] = (
        bool(diff_text.strip())
        or bool(meaningful_source_files)
        or (already_satisfied and bool(already_satisfied_reason))
    )
    if not gates["diff_exists"]:
        reasons.append("no mutation diff exists")

    gates["verification_ran"] = (
        test_result is not None
        or (already_satisfied and bool(already_satisfied_reason))
        or (diff_only_verification_allowed and bool(meaningful_source_files))
    )
    if not gates["verification_ran"]:
        reasons.append("no configuration/build/lint/test verification was run")

    verification_passed = already_satisfied or (
        diff_only_verification_allowed and bool(meaningful_source_files) and state_assertions_passed
    )
    # AIX-EDIT-001 fail-closed (narrow): a mutation whose meaningful diff
    # touches ONLY test paths, with no test verification run, must never
    # pass on model judgement / diff inspection alone — that is the EDIT-1
    # wrong-file shape (target source untouched, tests edited instead).
    # Source-bearing diffs keep prior behavior (a README rename on a
    # test-bearing repo must not newly fail).
    if (
        verification_passed
        and test_result is None
        and not already_satisfied
        and bool(meaningful_source_files)
        and all(_is_test_only_path(p) for p in meaningful_source_files)
    ):
        verification_passed = False
        reasons.append(
            "TESTS_NOT_RUN: mutation changed only test files without test "
            "verification; model judgement cannot substitute"
        )
    if test_result is not None:
        verification_passed = int(test_result.get("returncode", 1)) == 0
        if not verification_passed:
            reasons.append("verification command exited non-zero")
    if not state_assertions_passed:
        verification_passed = False
        reasons.append(state_assertion_detail or "requested state assertions failed")
    gates["verification_passed"] = verification_passed

    leaked = False
    leak_detail = ""
    if secret_values:
        resolved = [
            ResolvedSecretV1(ref=SecretRefV1(name=f"secret_{idx}", rotated_at="1970-01-01T00:00:00Z"), value=value)
            for idx, value in enumerate(secret_values)
            if value
        ]
        try:
            SecretBoundary(secrets={}, now="1970-01-01T00:00:00Z").assert_no_leakage(diff_text, resolved=resolved)
        except SecretBoundaryError as exc:
            leaked = True
            leak_detail = str(exc)
    gates["no_secrets_leaked"] = not leaked
    if leaked:
        reasons.append(f"secret leakage detected: {leak_detail}")

    quality_report = build_report(
        {
            "test": (
                gates["verification_passed"],
                (
                    "diff-only verification allowed; mutation evidence present"
                    if diff_only_verification_allowed and test_result is None
                    else ("" if test_result is None else str(test_result.get("stdout", ""))[:500])
                ),
            ),
            "diff": (gates["diff_exists"], f"{len(meaningful_source_files)} source file(s) changed"),
            "acceptance": (gates["source_changed"], already_satisfied_reason),
            "security": (gates["no_secrets_leaked"], leak_detail),
            "lint": (True, "not configured for this repository"),
            "type": (True, "not configured for this repository"),
        }
    )

    evidence_refs = tuple(meaningful_source_files) + already_satisfied_evidence_refs
    verdict = "BLOCKED"
    if evidence_refs:
        verification_input = VerificationInputV1(
            step_id="source-edit",
            attempt_id="attempt-1",
            tenant_id="",
            acceptance_criteria=("source_read", "source_changed", "diff_exists", "verification_ran"),
            criteria_results={
                "source_read": gates["source_read"],
                "source_changed": gates["source_changed"],
                "diff_exists": gates["diff_exists"],
                "verification_ran": gates["verification_ran"],
            },
            independent_evidence_refs=evidence_refs,
            quality_gates=quality_report,
            claimed_outcome="ok" if already_satisfied else "edited",
        )
        verification_result = IndependentVerifier().verify(verification_input)
        verdict = verification_result.verdict
        if verdict != "PASSED":
            reasons.extend(f"verifier:{code}" for code in verification_result.reason_codes)
    else:
        reasons.append("no independent evidence available for verification")

    passed = all(gates.values()) and verdict == "PASSED"
    return CompletionGateResult(
        passed=passed,
        reasons=tuple(dict.fromkeys(reasons)),
        gates=gates,
        changed_source_files=meaningful_source_files,
        run_artifacts=run_artifacts,
        verification_verdict=verdict,
    )


__all__ = [
    "CompletionGateResult",
    "RUN_ARTIFACT_PREFIXES",
    "split_changed_paths",
    "evaluate_completion_gates",
]
