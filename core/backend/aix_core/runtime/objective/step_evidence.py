"""AIX-MPE-003 Packet MPE-21: a step cannot PASS without declared validation evidence."""
from __future__ import annotations

from backend.aix_core.runtime.objective.contracts import StepContractV1
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult


def missing_required_evidence(
    *,
    step: StepContractV1 | None,
    result: RealExecuteResult,
) -> tuple[str, ...]:
    if step is None:
        return ()
    required = {item.strip().lower() for item in step.required_evidence if str(item).strip()}
    if not required:
        return ()
    missing: list[str] = []
    gates = (result.completion_gates or {}).get("gates") or {}
    if ("tests" in required or "test" in required) and not gates.get("verification_ran"):
        missing.append("tests")
    if "diff" in required and not str(result.diff_text or "").strip():
        missing.append("diff")
    if "commands" in required and int(result.real_operations or 0) < 1:
        missing.append("commands")
    return tuple(missing)


__all__ = ["missing_required_evidence"]
