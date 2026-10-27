"""EX4-R3-P10-S001: Autonomous Planning Engine — bounded core.

Build on Proposal System rails. Never invent parallel governance.
Never silently mutate the execution graph for mid-run replans —
mutations are proposed (and Level-1 may auto-resolve via existing rails).

First slice priorities (packet + roadmap):
  - objective interpretation + ambiguity detection
  - bounded decomposition (hard caps; not 1000+ task graphs)
  - replan triggers from evidence
  - bounded autonomy mapped to Proposal levels 1–4
  - plan quality + dry-run simulation (no oracle claims)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from backend.aix_core.runtime.executor.proposals import required_level_for_impact
from backend.infrastructure.postgres.execution_kernel.repository import ExecutionKernelRepositoryError

MAX_WORK_ITEMS = 12
MIN_OBJECTIVE_CHARS = 24
AMBIGUITY_MARKERS = (
    " somehow ",
    " maybe ",
    " later ",
    " etc",
    " TBD",
    "TODO",
    "as needed",
    "figure out",
    "whatever",
)

PlanMutationKind = Literal[
    "local_title_tweak",
    "add_work_items_same_problem",
    "reorder_dependencies",
    "replan_within_capability",
    "touch_other_capability",
    "new_capability_or_charter",
]

# What may auto-apply (via Proposal Level 1 auto-resolve) without human/planner wait.
AUTO_ALLOWED_MUTATIONS = frozenset({"local_title_tweak"})

MUTATION_LEVEL_FLOOR: dict[str, int] = {
    "local_title_tweak": 1,
    "add_work_items_same_problem": 2,
    "reorder_dependencies": 2,
    "replan_within_capability": 2,
    "touch_other_capability": 3,
    "new_capability_or_charter": 4,
}

REPLAN_TRIGGER_CODES = frozenset(
    {
        "WORK_ITEM_FAILED",
        "WORK_ITEM_BLOCKED",
        "MISSING_KNOWLEDGE",
        "AMBIGUOUS_OBJECTIVE",
        "PLAN_QUALITY_BELOW_THRESHOLD",
        "DEPENDENCY_DEADLOCK_SIGNAL",
    }
)

_SENTENCE_SPLIT = re.compile(r"[.!?\n]+")
_REQUIREMENT_HINT = re.compile(
    r"\b(must|shall|should|need to|require|ensure|implement|add|fix|verify)\b",
    re.IGNORECASE,
)


def _require_tenant(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ExecutionKernelRepositoryError("tenant_id is required (fail closed)")
    return normalized


def interpret_objective(objective: str) -> dict[str, Any]:
    """Turn objective text into explicit requirements + ambiguity signals."""
    text = " ".join(str(objective or "").split()).strip()
    if not text:
        raise ExecutionKernelRepositoryError("objective is required")
    lowered = f" {text.lower()} "
    ambiguities: list[str] = []
    if len(text) < MIN_OBJECTIVE_CHARS:
        ambiguities.append("OBJECTIVE_TOO_SHORT")
    for marker in AMBIGUITY_MARKERS:
        if marker.lower() in lowered:
            ambiguities.append(f"AMBIGUITY_MARKER:{marker.strip()}")

    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    requirements: list[str] = []
    for sentence in sentences:
        if _REQUIREMENT_HINT.search(sentence) or len(requirements) == 0:
            requirements.append(sentence)
    # Cap extracted requirements to keep decomposition bounded.
    requirements = requirements[:MAX_WORK_ITEMS]
    if not requirements:
        ambiguities.append("NO_TESTABLE_REQUIREMENTS")

    return {
        "objective": text,
        "requirements": requirements,
        "ambiguities": ambiguities,
        "ambiguous": bool(ambiguities),
        "requirement_count": len(requirements),
    }


def decompose_plan(
    *,
    objective: str | None = None,
    requirements: list[str] | None = None,
    max_work_items: int = MAX_WORK_ITEMS,
) -> dict[str, Any]:
    """Bounded decomposition into candidate work items + sequential dependencies.

    Does not write projections — returns a draft plan only.
    """
    if max_work_items < 1 or max_work_items > MAX_WORK_ITEMS:
        raise ExecutionKernelRepositoryError(
            f"max_work_items must be between 1 and {MAX_WORK_ITEMS}"
        )
    interpreted = interpret_objective(objective or " ".join(requirements or []))
    if requirements:
        reqs = [str(r).strip() for r in requirements if str(r).strip()][:max_work_items]
        interpreted["requirements"] = reqs
        interpreted["requirement_count"] = len(reqs)
    reqs = list(interpreted["requirements"])[:max_work_items]
    if interpreted["ambiguous"] and not reqs:
        return {
            **interpreted,
            "disposition": "CLARIFICATION_REQUIRED",
            "work_items": [],
            "dependencies": [],
            "capped": False,
            "truncated": False,
        }

    work_items: list[dict[str, Any]] = []
    dependencies: list[dict[str, str]] = []
    for idx, req in enumerate(reqs):
        wi_key = f"draft_wi_{idx + 1}"
        title = req if len(req) <= 120 else req[:117] + "..."
        size = "S" if len(req) < 60 else "M" if len(req) < 100 else "L"
        risk = "low" if size == "S" else "medium" if size == "M" else "high"
        work_items.append(
            {
                "draft_id": wi_key,
                "title": title,
                "size": size,
                "risk": risk,
                "requirement_index": idx,
            }
        )
        if idx > 0:
            dependencies.append(
                {"work_item": wi_key, "depends_on": f"draft_wi_{idx}"}
            )

    truncated = len(interpreted["requirements"]) > max_work_items
    disposition = "CLARIFICATION_REQUIRED" if interpreted["ambiguous"] else "CANDIDATE"
    return {
        **interpreted,
        "requirements": reqs,
        "requirement_count": len(reqs),
        "disposition": disposition,
        "work_items": work_items,
        "dependencies": dependencies,
        "capped": True,
        "max_work_items": max_work_items,
        "truncated": truncated,
    }


def evaluate_plan_quality(plan: dict[str, Any]) -> dict[str, Any]:
    """Deterministic plan critique — not an oracle forecast."""
    work_items = list(plan.get("work_items") or [])
    ambiguities = list(plan.get("ambiguities") or [])
    deps = list(plan.get("dependencies") or [])
    score = 100
    reasons: list[str] = []
    if plan.get("disposition") == "CLARIFICATION_REQUIRED":
        score -= 40
        reasons.append("clarification_required")
    if ambiguities:
        score -= min(30, 10 * len(ambiguities))
        reasons.append("ambiguity_penalty")
    if not work_items:
        score -= 50
        reasons.append("empty_plan")
    if len(work_items) > MAX_WORK_ITEMS:
        score -= 20
        reasons.append("over_cap")
    if work_items and len(deps) < len(work_items) - 1:
        score -= 5
        reasons.append("sparse_dependencies")
    high_risk = sum(1 for w in work_items if w.get("risk") == "high")
    if high_risk:
        score -= 5 * high_risk
        reasons.append("high_risk_items")
    score = max(0, min(100, score))
    return {
        "score": score,
        "pass_threshold": score >= 60,
        "reasons": reasons,
        "work_item_count": len(work_items),
        "ambiguity_count": len(ambiguities),
    }


def evaluate_plan_quality_with_learning(
    plan: dict[str, Any],
    *,
    relevant_learning: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """EX5-S6: Closed Learning Loop — Level 2 governance only (per the
    governance ladder: knowledge retrieved for consideration ->
    RECOMMENDATION INFLUENCING A PLANNER, the level this function
    implements). It never rewrites the decomposition itself and never
    auto-updates a planning heuristic — those are Level 3+, still gated
    behind explicit policy (see learning_lifecycle.py's
    REQUIRES_EXPLICIT_POLICY). This is the mechanism behind 'Project 2
    plans measurably better than Project 1': if Project 1 failed and
    produced ACTIVE_KNOWLEDGE relevant to this plan's capability, Project
    2's quality evaluation surfaces it — a real, measurable score/reasons
    difference versus a plan built with no relevant prior learning."""
    base = evaluate_plan_quality(plan)
    relevant = list(relevant_learning or [])
    if not relevant:
        return base
    result = dict(base)
    result["score"] = max(0, base["score"] - 5 * len(relevant))
    result["reasons"] = [*base["reasons"], "known_failure_pattern_applies"]
    result["relevant_learning"] = [
        {"learning_id": r.get("learning_id"), "summary": r.get("summary")} for r in relevant
    ]
    result["pass_threshold"] = result["score"] >= 60
    return result


@dataclass(frozen=True)
class AutonomyDecision:
    mutation_kind: str
    required_proposal_level: int
    auto_allowed: bool
    reason: str


def bounded_autonomy_for_mutation(
    mutation_kind: str,
    *,
    problem_capability_id: str | None = None,
    affected_capabilities: list[str] | None = None,
    requests_new_capability: bool = False,
    charter_change: bool = False,
) -> AutonomyDecision:
    """Map intended plan mutation → Proposal level floor + auto-apply eligibility."""
    if mutation_kind not in MUTATION_LEVEL_FLOOR:
        raise ExecutionKernelRepositoryError(f"unknown mutation_kind={mutation_kind!r}")
    floor = MUTATION_LEVEL_FLOOR[mutation_kind]
    impact = required_level_for_impact(
        problem_capability_id=problem_capability_id,
        affected_capabilities=affected_capabilities,
        requests_new_capability=requests_new_capability or mutation_kind == "new_capability_or_charter",
        charter_change=charter_change or mutation_kind == "new_capability_or_charter",
    )
    level = max(floor, impact)
    auto = mutation_kind in AUTO_ALLOWED_MUTATIONS and level <= 1
    reason = (
        "level_1_auto_resolve_via_proposal_rails"
        if auto
        else "requires_proposal_approval"
    )
    return AutonomyDecision(
        mutation_kind=mutation_kind,
        required_proposal_level=level,
        auto_allowed=auto,
        reason=reason,
    )


def autonomy_decision_to_dict(decision: AutonomyDecision) -> dict[str, Any]:
    return {
        "mutation_kind": decision.mutation_kind,
        "required_proposal_level": decision.required_proposal_level,
        "auto_allowed": decision.auto_allowed,
        "reason": decision.reason,
    }


def detect_replan_triggers(
    *,
    tenant_id: str,
    work_items: list[dict[str, Any]],
    missing_knowledge: dict[str, Any] | None = None,
    plan_quality: dict[str, Any] | None = None,
    objective_ambiguous: bool = False,
) -> list[dict[str, Any]]:
    """Evidence-driven replan signals — authority-scoped; does not mutate."""
    _require_tenant(tenant_id)
    triggers: list[dict[str, Any]] = []
    for item in work_items:
        status = str(item.get("status") or "").upper()
        wi_id = str(item.get("work_item_id") or item.get("aggregate_id") or "")
        if status == "FAILED":
            triggers.append(
                {
                    "code": "WORK_ITEM_FAILED",
                    "work_item_id": wi_id,
                    "suggested_mutation": "replan_within_capability",
                }
            )
        elif status == "BLOCKED":
            triggers.append(
                {
                    "code": "WORK_ITEM_BLOCKED",
                    "work_item_id": wi_id,
                    "suggested_mutation": "reorder_dependencies",
                }
            )
    if missing_knowledge and missing_knowledge.get("missing"):
        triggers.append(
            {
                "code": "MISSING_KNOWLEDGE",
                "capability_id": missing_knowledge.get("capability_id"),
                "missing_kinds": missing_knowledge.get("missing_kinds") or [],
                "suggested_mutation": "add_work_items_same_problem",
            }
        )
    if objective_ambiguous:
        triggers.append(
            {
                "code": "AMBIGUOUS_OBJECTIVE",
                "suggested_mutation": "local_title_tweak",
            }
        )
    if plan_quality and not plan_quality.get("pass_threshold", True):
        triggers.append(
            {
                "code": "PLAN_QUALITY_BELOW_THRESHOLD",
                "score": plan_quality.get("score"),
                "suggested_mutation": "replan_within_capability",
            }
        )
    # Simple deadlock signal: all PENDING items have unmet deps and none actionable.
    pending = [i for i in work_items if str(i.get("status") or "").upper() == "PENDING"]
    if pending and all(i.get("blocked_by_deps") for i in pending):
        triggers.append(
            {
                "code": "DEPENDENCY_DEADLOCK_SIGNAL",
                "suggested_mutation": "reorder_dependencies",
            }
        )
    return triggers


def simulate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Dry-run progress forecast — projection only, not a completion guarantee."""
    work_items = list(plan.get("work_items") or [])
    if not work_items:
        return {
            "forecast": "no_work",
            "estimated_steps": 0,
            "risk_level": "high",
            "termination": "STOPPED_EMPTY",
            "oracle_claim": False,
        }
    size_weight = {"S": 1, "M": 2, "L": 3}
    effort = sum(size_weight.get(str(w.get("size") or "M"), 2) for w in work_items)
    high = sum(1 for w in work_items if w.get("risk") == "high")
    risk = "high" if high >= 2 else "medium" if high == 1 else "low"
    termination = "DONE_CANDIDATE" if plan.get("disposition") == "CANDIDATE" else "ESCALATE_CLARIFICATION"
    return {
        "forecast": "bounded_projection",
        "estimated_steps": effort,
        "work_item_count": len(work_items),
        "risk_level": risk,
        "termination": termination,
        "oracle_claim": False,
    }


def build_plan_mutation_proposal_payload(
    *,
    mutation_kind: str,
    reason: str,
    draft_plan: dict[str, Any] | None = None,
    autonomy: AutonomyDecision | None = None,
) -> dict[str, Any]:
    """Structure attached to Proposal reason/audit — graph writes stay on resolve path."""
    decision = autonomy or bounded_autonomy_for_mutation(mutation_kind)
    return {
        "planning_engine": "EX4-R3-P10-S001",
        "mutation_kind": mutation_kind,
        "reason": reason,
        "required_proposal_level": decision.required_proposal_level,
        "auto_allowed": decision.auto_allowed,
        "draft_work_item_count": len((draft_plan or {}).get("work_items") or []),
        "silent_graph_mutation": False,
    }


__all__ = [
    "MAX_WORK_ITEMS",
    "AUTO_ALLOWED_MUTATIONS",
    "MUTATION_LEVEL_FLOOR",
    "AutonomyDecision",
    "interpret_objective",
    "decompose_plan",
    "evaluate_plan_quality",
    "evaluate_plan_quality_with_learning",
    "bounded_autonomy_for_mutation",
    "autonomy_decision_to_dict",
    "detect_replan_triggers",
    "simulate_plan",
    "build_plan_mutation_proposal_payload",
]
