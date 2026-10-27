"""AIX Model Registry — Independence Guard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.aix_core.runtime.model_registry.registry import get_model_registry


class IndependenceViolation(ValueError):
    """Raised when collaborating roles share a forbidden model family."""


class SelfVerificationError(IndependenceViolation):
    """Raised when verifier equals executor without escalation."""


def extract_model_family(model_id: str) -> str:
    """Extract canonical family from OpenRouter model id or registry profile."""
    registry = get_model_registry()
    profile = registry.get_profile(model_id)
    if profile is not None:
        return profile.family
    m = model_id.lower().removeprefix("openrouter/")
    if "gpt-5.6-luna" in m or ("gpt" in m and "luna" in m):
        return "gpt-5.6-luna"
    if "deepseek-v3.2" in m or "deepseek" in m:
        return "deepseek-v3.2"
    if "qwen3-coder-30b" in m or "30b-a3b" in m:
        return "qwen3-coder-30b"
    if "qwen3-coder" in m or "qwen" in m:
        return "qwen3-coder"
    if "claude" in m or "anthropic" in m:
        return "anthropic"
    if "gpt" in m or "openai" in m:
        return "openai"
    if "gemini" in m or "google" in m:
        return "google"
    if "mock" in m:
        return "mock"
    return m.split("/")[-1].split("-")[0]


@dataclass(frozen=True)
class ModelIndependenceResult:
    is_independent: bool
    violations: tuple[str, ...] = ()
    provenance_record: dict[str, str] | None = None


def _independence_family(model_id: str) -> str:
    """Coarse family for independence checks (provider-level when profile known)."""
    registry = get_model_registry()
    profile = registry.get_profile(model_id)
    if profile is not None:
        return profile.provider
    return extract_model_family(model_id)


class ModelIndependenceGuard:
    """Validates planner / executor / verifier / certifier independence."""

    def __init__(self, enforce_family_separation: bool = True) -> None:
        self.enforce_family_separation = enforce_family_separation

    def enforce(
        self,
        *,
        produced_by_family: str,
        tested_by_family: str,
        verified_by_family: str,
        certified_by_family: str | None = None,
        produced_by_model: str = "",
        tested_by_model: str = "",
        verified_by_model: str = "",
        certified_by_model: str | None = None,
        planner_family: str | None = None,
        escalated: bool = False,
    ) -> ModelIndependenceResult:
        violations: list[str] = []
        pf = planner_family or produced_by_family

        if self.enforce_family_separation:
            if pf == produced_by_family and pf not in ("mock",):
                violations.append(
                    f"Planner family ({pf}) must differ from executor family ({produced_by_family})"
                )
            if verified_by_family == produced_by_family and verified_by_family not in ("mock",):
                if not escalated:
                    raise SelfVerificationError(
                        f"Verifier family ({verified_by_family}) must differ from executor "
                        f"family ({produced_by_family}) without escalation"
                    )
                violations.append(
                    f"Self-verification allowed only with escalation: verifier={verified_by_family}"
                )
            if certified_by_family and certified_by_family == produced_by_family:
                violations.append(
                    f"Certifier family ({certified_by_family}) must differ from executor "
                    f"family ({produced_by_family})"
                )
        else:
            if verified_by_model and produced_by_model and verified_by_model == produced_by_model:
                if not escalated:
                    raise SelfVerificationError(
                        f"Verifier model ({verified_by_model}) equals executor ({produced_by_model})"
                    )

        provenance = {
            "planned_by": planner_family or "",
            "produced_by": produced_by_model or produced_by_family,
            "tested_by": tested_by_model or tested_by_family,
            "verified_by": verified_by_model or verified_by_family,
            "certified_by": certified_by_model or certified_by_family or verified_by_model or verified_by_family,
        }
        if violations:
            if escalated and all("Self-verification allowed" in v for v in violations):
                return ModelIndependenceResult(
                    is_independent=True,
                    violations=tuple(violations),
                    provenance_record=provenance,
                )
            raise IndependenceViolation("; ".join(violations))
        return ModelIndependenceResult(
            is_independent=True,
            violations=(),
            provenance_record=provenance,
        )

    def validate_role_models(
        self,
        *,
        planner_model: str,
        executor_model: str,
        verifier_model: str,
        certifier_model: str | None = None,
        escalated: bool = False,
    ) -> ModelIndependenceResult:
        return self.enforce(
            planner_family=_independence_family(planner_model),
            produced_by_family=_independence_family(executor_model),
            tested_by_family=_independence_family(executor_model),
            verified_by_family=_independence_family(verifier_model),
            certified_by_family=_independence_family(certifier_model) if certifier_model else None,
            produced_by_model=executor_model,
            tested_by_model=executor_model,
            verified_by_model=verifier_model,
            certified_by_model=certifier_model,
            escalated=escalated,
        )


def log_model_provenance_to_ledger(
    *,
    cycle_id: str,
    objective_id: str,
    provenance: dict[str, str],
) -> dict[str, Any]:
    """Structured payload for cycle ledger model.provenance events."""
    return {
        "schema": "aix.model_provenance.v1",
        "cycle_id": cycle_id,
        "objective_id": objective_id,
        **provenance,
    }


__all__ = [
    "IndependenceViolation",
    "SelfVerificationError",
    "extract_model_family",
    "ModelIndependenceResult",
    "ModelIndependenceGuard",
    "log_model_provenance_to_ledger",
]
