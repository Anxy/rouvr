"""C040: live planner proof and prompt provenance (wraps objective_cycle planner)."""

# Keep this package init free of imports that pull objective_cycle.step_contract_planner
# (live_runner), or admit/start crashes with a circular ImportError.

from backend.aix_core.planner_proof.fallback_policy import (
    FALLBACK_POLICY_ID,
    FallbackDecision,
    apply_fallback_policy,
)
from backend.aix_core.planner_proof.provenance import (
    PLANNER_PROMPT_ID,
    PLANNER_PROMPT_VERSION,
    PlannerCallProvenance,
    capture_prompt_provenance,
    evidence_bundle,
)
from backend.aix_core.planner_proof.adversarial import (
    ADVERSARIAL_CASES,
    assert_adversarial_refusal,
)
from backend.aix_core.planner_proof.fixtures import representative_multi_obligation_fixture

__all__ = [
    "ADVERSARIAL_CASES",
    "FALLBACK_POLICY_ID",
    "FallbackDecision",
    "PLANNER_PROMPT_ID",
    "PLANNER_PROMPT_VERSION",
    "PlannerCallProvenance",
    "apply_fallback_policy",
    "assert_adversarial_refusal",
    "capture_prompt_provenance",
    "evidence_bundle",
    "representative_multi_obligation_fixture",
    "run_planner_proof",
]


def __getattr__(name: str):
    if name == "run_planner_proof":
        from backend.aix_core.planner_proof.live_runner import run_planner_proof

        return run_planner_proof
    raise AttributeError(name)
