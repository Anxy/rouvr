"""P4: assemble PlanRequest four sections; construct PlanRevision from model output.

The parser must not invent targets or add files. For CREATE only, a missing or
non-enum `effect` defaults to LITERAL_REPLACE (packet: CREATE uses RequiredEffectKind,
typically LITERAL_REPLACE); free-text misplaced in `effect` is moved to rationale.
EXISTING still requires an exact RequiredEffectKind (P9).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.aix_core.runtime.objective.contracts import BudgetV1
from backend.aix_core.runtime.objective.provenance.contracts import (
    CreateCapability,
    ExplorationEvidence,
    ExplorationNeed,
    GovernanceRevision,
    PlanRequest,
    PlanRequestClaimView,
    PlanRequestEvidenceItem,
    PlanRequestGovernanceSection,
    PlanRequestIntentSection,
    PlanRequestUnderstandingSection,
    PlanRevision,
    PlanTarget,
    StepTargetProjection,
    UnderstandingRevision,
    UserIntent,
    UserTurn,
)
from backend.aix_core.runtime.objective.provenance.validators import (
    RuleResult,
    validate_plan_revision,
    validate_target_kind,
)

_EFFECTS: frozenset[str] = frozenset(
    {
        "GROUP_METHODS",
        "MOVE_METHODS",
        "EXTRACT_HELPER",
        "EXTRACT_TO_FILES",
        "STRIP_UNUSED_IMPORTS",
        "LITERAL_REPLACE",
        "PRESERVE_PUBLIC_INTERFACE",
        "RUN_DECLARED_VALIDATION",
    }
)
_CREATE_DEFAULT_EFFECT = "LITERAL_REPLACE"


def _normalize_target_effect(*, kind: str, effect: str, rationale: str) -> tuple[str, str] | RuleResult:
    """Return (effect, rationale) or a P9 RuleResult."""
    raw = str(effect or "").strip()
    rationale_out = str(rationale or "").strip() or "model-authored target"
    if raw in _EFFECTS:
        return raw, rationale_out
    if kind == "CREATE":
        # Models often put the plan sentence in `effect`. That is rationale, not ontology.
        if raw and raw not in _EFFECTS:
            if rationale_out == "model-authored target":
                rationale_out = raw
            elif raw not in rationale_out:
                rationale_out = f"{rationale_out}; {raw}"
        return _CREATE_DEFAULT_EFFECT, rationale_out
    return RuleResult.fail(
        "P9",
        f"effect must be RequiredEffectKind:{raw or '(empty)'}",
        decision="REJECT",
    )


@dataclass(frozen=True)
class PlanConstruction:
    plan: PlanRevision
    validation: RuleResult
    transformations: tuple[dict[str, object], ...]


def build_plan_request(
    *,
    cycle_id: str,
    intent: UserIntent,
    turns: tuple[UserTurn, ...] = (),
    understanding: UnderstandingRevision,
    evidence: tuple[ExplorationEvidence, ...] = (),
    governance: GovernanceRevision,
) -> PlanRequest:
    """Typed four-section request. No files_read bag, no allowed_files, no dump dict."""
    return PlanRequest(
        objective_id=intent.objective_id,
        cycle_id=cycle_id,
        intent=PlanRequestIntentSection(
            intent_id=intent.id,
            text=intent.text,
            turns=turns,
        ),
        understanding=PlanRequestUnderstandingSection(
            revision_id=understanding.id,
            scope_mode=understanding.scope_mode,
            claims=tuple(
                PlanRequestClaimView(
                    id=claim.id,
                    kind=claim.kind,
                    text=claim.text,
                    standing=claim.standing,
                    status=claim.status,
                    cites=claim.cites,
                )
                for claim in understanding.claims
            ),
        ),
        evidence=tuple(
            PlanRequestEvidenceItem(
                id=item.id,
                need_id=item.need_id,
                location=item.location,
                observation=item.observation,
                exists=item.exists,
            )
            for item in evidence
        ),
        governance=PlanRequestGovernanceSection(
            revision_id=governance.id,
            forbidden_effects=governance.forbidden_effects,
            create_capability=governance.create_capability,
            create_root=governance.create_root,
            approval_policy=governance.approval_policy,
        ),
    )


def plan_request_sections(request: PlanRequest) -> dict[str, object]:
    """JSON payload for the planner. create_root is labeled permission, not scope."""
    return {
        "intent": {
            "id": request.intent.intent_id,
            "text": request.intent.text,
            "turns": [{"id": turn.id, "text": turn.text} for turn in request.intent.turns],
        },
        "understanding": {
            "revision_id": request.understanding.revision_id,
            "scope_mode": request.understanding.scope_mode,
            "claims": [
                {
                    "id": claim.id,
                    "kind": claim.kind,
                    "text": claim.text,
                    "standing": claim.standing,
                    "status": claim.status,
                    "cites": [{"kind": c.kind, "ref": c.ref} for c in claim.cites],
                }
                for claim in request.understanding.claims
            ],
        },
        "evidence": [
            {
                "id": item.id,
                "need_id": item.need_id,
                "location": item.location,
                "observation": item.observation,
                "exists": item.exists,
            }
            for item in request.evidence
        ],
        "governance": {
            "revision_id": request.governance.revision_id,
            "forbidden_effects": list(request.governance.forbidden_effects),
            "create_capability": list(request.governance.create_capability),
            "create_root": list(request.governance.create_root),
            "create_root_is": request.governance.create_root_label,
            "approval_policy": request.governance.approval_policy,
        },
    }


def parse_plan_targets(raw: object) -> tuple[tuple[PlanTarget, ...], RuleResult]:
    """Parse model-authored targets. Do not invent targets from step target_scope."""
    if raw is None:
        return (), RuleResult.fail("P6", "Plan must propose PlanTarget objects", decision="REJECT")
    if not isinstance(raw, list):
        return (), RuleResult.fail("P12", "targets must be a list", decision="REJECT")
    if not raw:
        return (), RuleResult.fail("P6", "Plan must propose PlanTarget objects", decision="REJECT")
    out: list[PlanTarget] = []
    for item in raw:
        if not isinstance(item, dict):
            return (), RuleResult.fail("P12", "each target must be an object", decision="REJECT")
        kind = str(item.get("kind") or "").strip()
        kind_check = validate_target_kind(kind)
        if not kind_check.ok:
            return (), kind_check
        path = str(item.get("path") or "").strip()
        if not path:
            return (), RuleResult.fail("P2", "PlanTarget.path is required", decision="REJECT")
        normalized = _normalize_target_effect(
            kind=kind,
            effect=str(item.get("effect") or ""),
            rationale=str(item.get("rationale") or ""),
        )
        if isinstance(normalized, RuleResult):
            return (), normalized
        effect, rationale = normalized
        outcome_ids = tuple(
            str(x).strip() for x in (item.get("outcome_claim_ids") or ()) if str(x).strip()
        )
        evidence_id = str(item.get("evidence_id") or "").strip() or None
        capability = str(item.get("create_capability") or "").strip() or None
        create_root = str(item.get("create_root") or "").strip() or None
        if capability is not None and capability not in {"CREATE_FILE", "CREATE_DIRECTORY"}:
            return (), RuleResult.fail("P4", "CREATE_PROJECT is not a v1 capability", decision="REJECT")
        try:
            out.append(
                PlanTarget(
                    kind=kind,  # type: ignore[arg-type]
                    path=path,
                    effect=effect,  # type: ignore[arg-type]
                    rationale=rationale,
                    evidence_id=evidence_id,
                    create_capability=capability,  # type: ignore[arg-type]
                    create_root=create_root,
                    outcome_claim_ids=outcome_ids,
                )
            )
        except Exception as exc:
            return (), RuleResult.fail("P12", f"invalid PlanTarget: {exc}", decision="REJECT")
    return tuple(out), RuleResult.pass_("P8")


def project_step_scopes(
    *,
    steps: tuple[tuple[str, ...], ...],
    targets: tuple[PlanTarget, ...],
) -> tuple[StepTargetProjection, ...]:
    return tuple(StepTargetProjection(target_scope=scope) for scope in steps)


def construct_plan_revision(
    *,
    request: PlanRequest,
    intent: UserIntent,
    understanding: UnderstandingRevision,
    needs: tuple[ExplorationNeed, ...],
    evidence: tuple[ExplorationEvidence, ...],
    governance: GovernanceRevision,
    targets: tuple[PlanTarget, ...],
    step_scopes: tuple[tuple[str, ...], ...] = (),
    transformations: tuple[dict[str, object], ...] = (),
    revision: int = 1,
    cycle_id: str | None = None,
) -> PlanConstruction:
    projections = project_step_scopes(steps=step_scopes, targets=targets)
    plan = PlanRevision(
        objective_id=intent.objective_id,
        cycle_id=cycle_id or request.cycle_id,
        revision=revision,
        intent_id=intent.id,
        understanding_revision_id=understanding.id,
        evidence_ids=tuple(t.evidence_id for t in targets if t.evidence_id),
        governance_revision_id=governance.id,
        targets=targets,
        steps=projections,
        status="DRAFT",
    )
    validation = validate_plan_revision(
        plan=plan,
        intent=intent,
        understanding=understanding,
        needs=needs,
        evidence=evidence,
        governance=governance,
        transformations=transformations,
    )
    return PlanConstruction(plan=plan, validation=validation, transformations=transformations)


def default_governance(
    *,
    objective_id: str,
    task_class: str,
    budgets: BudgetV1,
    forbidden_effects: tuple[str, ...] = (),
    approval_policy: str = "INITIAL_PLAN_ONLY",
    create_capability: tuple[CreateCapability, ...] = ("CREATE_FILE", "CREATE_DIRECTORY"),
    create_root: tuple[str, ...] = ("proposed/",),
) -> GovernanceRevision:
    return GovernanceRevision(
        objective_id=objective_id,
        forbidden_effects=forbidden_effects,
        create_capability=create_capability,
        create_root=create_root,
        task_class=task_class or "code_change",
        budgets=budgets,
        approval_policy=approval_policy,  # type: ignore[arg-type]
    )
