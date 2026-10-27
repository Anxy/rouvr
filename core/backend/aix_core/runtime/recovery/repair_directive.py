"""AIX-MPE-003 Packet MPE-20 / AIX-EC-004 Packet EC-07: structured repair directives."""
from __future__ import annotations

import hashlib

from backend.aix_core.runtime.objective.contracts import RepairDirectiveV1, StepContractV1


def _failure_fingerprint(reasons: tuple[str, ...], diff_text: str = "") -> str:
    """Stable hash of failure reasons + diff for detecting repeated identical failures."""
    content = "|".join(sorted(str(r).strip().casefold() for r in reasons if str(r).strip()))
    if diff_text.strip():
        content += "||" + diff_text.strip()[:2000]
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _is_cosmetic_only_repair(diff_text: str) -> bool:
    """True when a repair diff only touches comments or docstrings."""
    if not diff_text.strip():
        return True
    for line in diff_text.splitlines():
        if not (line.startswith("+") or line.startswith("-")):
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        content = line[1:].strip()
        if not content:
            continue
        if content.startswith("#") or content.startswith("//") or content.startswith("*"):
            continue
        if content.startswith('"""') or content.startswith("'''") or content.startswith("/*"):
            continue
        return False
    return True


def build_repair_directive(
    *,
    step: StepContractV1,
    reasons: tuple[str, ...],
    last_strategy_id: str | None = None,
    remaining_asked_work: bool = False,
    unmet_effects: tuple[str, ...] = (),
    comment_only: bool = False,
    made_progress: bool | None = None,
    prior_failure_fingerprints: tuple[str, ...] = (),
    last_repair_diff: str = "",
) -> RepairDirectiveV1:
    correction = "; ".join(r for r in reasons if str(r).strip()) or "address verifier failure evidence"
    progress = True if made_progress is None else made_progress
    if comment_only or remaining_asked_work:
        progress = False

    # PART 4 — Repair loop guard: reject cosmetic-only repairs unless acceptance
    # criteria explicitly require docstring/comment changes.
    if last_repair_diff and _is_cosmetic_only_repair(last_repair_diff):
        criteria_lower = " ".join(step.acceptance_criteria).casefold()
        if "docstring" not in criteria_lower and "comment" not in criteria_lower:
            progress = False
            correction = (
                f"COSMETIC_REPAIR_REJECTED: prior repair was comment/docstring-only "
                f"but acceptance criteria require substantive code changes. "
                f"Original failure: {correction}"
            )

    # Detect repeated identical failure fingerprint.
    current_fp = _failure_fingerprint(reasons, last_repair_diff)
    if prior_failure_fingerprints and current_fp in prior_failure_fingerprints:
        correction = (
            f"REPEATED_FAILURE_DETECTED (fingerprint={current_fp}): "
            f"this exact failure pattern was seen before. "
            f"A fundamentally different approach is required. {correction}"
        )

    insufficiency = None
    forbidden: tuple[str, ...] = ()
    preferred = None
    if not progress and last_strategy_id:
        insufficiency = "STRATEGY_INSUFFICIENT"
        forbidden = (last_strategy_id,)
        if last_strategy_id != "aix.structural.php.v1":
            preferred = "aix.structural.php.v1"
    unmet = unmet_effects or tuple(
        e.kind for e in step.required_effects if e.kind not in {"PRESERVE_PUBLIC_INTERFACE", "RUN_DECLARED_VALIDATION"}
    )
    return RepairDirectiveV1(
        step_id=step.id,
        failure_refs=reasons,
        remaining_requirements=tuple(step.acceptance_criteria),
        required_correction=correction[:2000],
        constraints=tuple(step.prohibited_actions),
        verification_requirements=tuple(step.validation_commands) + tuple(step.expected_checks),
        forbidden_strategies=forbidden,
        preferred_strategy=preferred,
        last_strategy_id=last_strategy_id,
        remaining_asked_work=remaining_asked_work or (not progress),
        unmet_effects=unmet,
        insufficiency_class=insufficiency,
    )


__all__ = ["build_repair_directive", "_failure_fingerprint", "_is_cosmetic_only_repair"]
