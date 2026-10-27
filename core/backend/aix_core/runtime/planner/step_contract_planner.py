"""AIX Closed-Loop Execution Plan, Sprint W5: the real, HOW-capable planner.

Distinct from `intelligence_layer/service.py`'s discuss-stage intake (WHAT-only,
pre-admission, no repository evidence) -- this stage runs post-admission, only after
`UnderstandingEvidenceV1` (Sprint W4) exists, and is allowed to reason from real,
already-collected evidence into a bounded `StepContractV1`. It never executes anything
itself and never self-certifies; it proposes, `_build_objective_envelope`-style
governance (Sprint W2/W6) authorizes, and `run_real_execute` (Sprint W7) implements.

Reuses the existing "planner" model role (`INTELLIGENCE_LAYER_MODEL_PLANNER`,
`GovernedProviderRouter`, `chat_completion`) rather than inventing a second model-call
path -- same infrastructure `run_discuss_turn` already uses for the intake stage.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

from pydantic import ValidationError

from backend.aix_core.runtime.objective.contracts import (
    ActionRequestV1,
    BudgetV1,
    ObjectiveObligationV1,
    PlanRevisionV1,
    RequiredEffectV1,
    RiskTier,
    StepContractV1,
    UnderstandingEvidenceV1,
)
from backend.aix_core.runtime.foundation.capability_registry import (
    resolve_capability,
    resolve_or_coerce_capability,
)
from backend.infrastructure.model_gateway.hyeve_integration_client import (
    ChatMessage,
    HyeveIntegrationChatError,
    chat_completion,
)
from backend.infrastructure.model_gateway.intelligence_layer.model_registry import load_model_registry
from backend.aix_core.runtime.model_registry.registry import get_model_registry
from backend.infrastructure.model_gateway.intelligence_layer.prompts import (
    STEP_CONTRACT_PLANNER_PROVENANCE_PROMPT,
    STEP_CONTRACT_PLANNER_SYSTEM_PROMPT,
    build_provenance_planner_evidence,
    build_step_contract_planner_evidence,
)
from backend.aix_core.runtime.objective.obligation_step_seed import (
    ObligationStepSeedResult,
    candidates_for_planner,
    compile_obligation_step_seed,
    should_prefer_obligation_seed,
)
from backend.aix_core.runtime.executor.executor_profile import default_executor_profile
from backend.aix_core.runtime.executor.required_effects import parse_required_effects
from backend.aix_core.runtime.objective.provenance.contracts import (
    ExplorationEvidence,
    ExplorationNeed,
    GovernanceRevision,
    PlanRequest,
    PlanTarget,
    UnderstandingRevision,
    UserIntent,
)
from backend.aix_core.runtime.objective.provenance.plan_request import (
    construct_plan_revision,
    parse_plan_targets,
    plan_request_sections,
)
from backend.aix_core.runtime.objective.provenance.validators import RuleResult, validate_replan_revision
from backend.infrastructure.model_gateway.provider_routing import GovernedProviderRouter
from backend.aix_core.planner_proof.provenance import (
    PLANNER_PARSER_VERSION,
    PlannerCallProvenance,
    capture_prompt_provenance,
)
from backend.aix_core.runtime.planner.planner_context_budget import budget_planner_prompt
from backend.hyeve_aix_service.config import settings as _planner_settings

PLANNER_MODEL_MAX_ATTEMPTS = 2
_PLANNER_REPAIR_SUFFIX = (
    "\n\nRepair: your prior response was invalid or incomplete. "
    "Return ONLY valid JSON with explicit targets[] (PlanTargets) and steps[] "
    "per the schema. Do not emit obligation-seed placeholders under proposed/."
)


def _files_by_name(files_read: tuple[str, ...] | list[str]) -> dict[str, str]:
    """Dead: basename rewrite is K1. Kept only so historical imports do not crash."""
    del files_read
    return {}


def _compat_plan_targets_from_steps(
    raw_steps: list[object],
    *,
    files_read: tuple[str, ...],
    outcome_claim_ids: tuple[str, ...] = (),
    exploration_evidence: tuple[ExplorationEvidence, ...] = (),
) -> tuple[PlanTarget, ...]:
    """Derive EXISTING PlanTargets from grounded step target_scope (PD-001 compatibility)."""
    grounded = {str(p).replace("\\", "/") for p in files_read if str(p).strip()}
    evidence_by_path = {
        str(getattr(item, "location", "") or "").replace("\\", "/"): str(getattr(item, "id", "") or "")
        for item in exploration_evidence
        if str(getattr(item, "location", "") or "").strip()
    }
    fallback_evidence_id = next(
        (str(getattr(item, "id", "") or "") for item in exploration_evidence if str(getattr(item, "id", "") or "")),
        "",
    ) or None
    seen: set[str] = set()
    targets: list[PlanTarget] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            continue
        for item in raw.get("target_scope") or ():
            path = str(item or "").strip().replace("\\", "/")
            if not path or path in seen:
                continue
            if grounded and path not in grounded:
                continue
            seen.add(path)
            purpose = str(raw.get("purpose") or f"Modify {path}")
            targets.append(
                PlanTarget(
                    kind="EXISTING",
                    path=path,
                    effect="LITERAL_REPLACE",
                    rationale=purpose[:240],
                    outcome_claim_ids=outcome_claim_ids,
                    evidence_id=evidence_by_path.get(path) or fallback_evidence_id,
                )
            )
    return tuple(targets)


def _is_cycle_workspace_proposal_path(item: str) -> bool:
    """Observability helper. Must not auto-ground CREATE (K3)."""
    norm = str(item or "").replace("\\", "/").strip().lstrip("./").casefold()
    return norm == "proposed" or norm.startswith("proposed/")


def _target_scope_grounded(
    item: str,
    *,
    files_read: tuple[str, ...] = (),
    symbols_found: tuple[str, ...] = (),
    constraints: list[str] | None = None,
    file_excerpts: dict[str, str] | None = None,
    plan_target_paths: tuple[str, ...] = (),
    require_plan_targets: bool = False,
) -> bool:
    """Exact path match only. No basename, no constraint membership, no proposed/ auto-true.

    Live planner always passes plan_request, so grounding is PlanTargets only.
    The files_read fallback is quarantined for callers without a PlanRequest.
    """
    del symbols_found, constraints, file_excerpts
    if require_plan_targets or plan_target_paths:
        return item in plan_target_paths
    return item in tuple(files_read)


def _canonicalize_target_scope(item: str, files_read: tuple[str, ...]) -> str:
    """Identity. Canonicalization is deleted (K1)."""
    del files_read
    return item


_ALLOWED_EVIDENCE_REQUEST_TYPES = frozenset(
    {
        "find_file",
        "find_symbol",
        "symbol_references",
        "related_tests",
        "related_routes",
        "read_file",
    }
)


def parse_evidence_requests(raw: object) -> tuple[dict[str, str], ...]:
    if not isinstance(raw, list):
        return ()
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").strip()
        query = str(item.get("query") or item.get("path") or item.get("symbol") or "").strip()
        if kind not in _ALLOWED_EVIDENCE_REQUEST_TYPES or not query:
            continue
        out.append({"type": kind, "query": query})
    return tuple(out)


def _operation_looks_like_prose(operation: str) -> bool:
    """True when the model stuffed purpose/rationale into `operation`."""
    text = str(operation or "").strip()
    if not text:
        return False
    if " " in text:
        return True
    if len(text) > 48:
        return True
    # "generate:Create the greenfield..." style slips
    if ":" in text and not text.lower().startswith("http"):
        return True
    return False


def _parse_actions(raw: object) -> tuple[ActionRequestV1, ...]:
    if not raw:
        return ()
    if not isinstance(raw, list):
        raise ValueError("actions must be a list")
    parsed: list[ActionRequestV1] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each action must be an object")
        capability = str(item.get("capability") or "").strip()
        operation = str(item.get("operation") or "").strip()
        arguments = dict(item.get("arguments") or {})
        # LLMs often emit "filesystem.read" as capability plus "read_file" as
        # operation. Normalize to canonical (capability, operation) lookup instead
        # of hard failing an otherwise valid governed action.
        if "." in capability:
            cap_head, cap_tail = capability.split(".", 1)
            cap_head = cap_head.strip()
            cap_tail = cap_tail.strip()
            if not operation or _operation_looks_like_prose(operation):
                if _operation_looks_like_prose(operation) and operation and "purpose" not in arguments:
                    arguments["purpose"] = operation
                capability = cap_head or capability
                operation = cap_tail
            else:
                capability = cap_head or capability
        if not capability or not operation:
            raise ValueError("action requires capability and operation")
        # "generate:Create the ..." when capability was already "content"
        if _operation_looks_like_prose(operation) and ":" in operation:
            head, _tail = operation.split(":", 1)
            head = head.strip()
            if head and resolve_or_coerce_capability(capability, head) is not None:
                if "purpose" not in arguments:
                    arguments["purpose"] = operation
                operation = head
        elif _operation_looks_like_prose(operation):
            # Bare prose operation with a known capability head → treat as inventible
            # verb on that head (filesystem + "Plan to create...") via coerce.
            if "purpose" not in arguments:
                arguments["purpose"] = operation
            # Prefer resolving inventable verbs from capability alone when op is prose.
            prose_resolved = resolve_or_coerce_capability(capability, "write")
            if prose_resolved is not None:
                capability, operation, _spec = prose_resolved
                parsed.append(
                    ActionRequestV1(
                        capability=capability,
                        operation=operation,
                        arguments=arguments,
                        expected_observation=dict(item.get("expected_observation") or {}),
                    )
                )
                continue
        resolved = resolve_or_coerce_capability(capability, operation)
        if resolved is None:
            raise ValueError(f"CAPABILITY_NOT_AVAILABLE:{capability}.{operation}")
        # Persist catalog ids (filesystem.patch), never invented planner verbs.
        capability, operation, _spec = resolved
        parsed.append(
            ActionRequestV1(
                capability=capability,
                operation=operation,
                arguments=arguments,
                expected_observation=dict(item.get("expected_observation") or {}),
            )
        )
    return tuple(parsed)


def _action_has_path_args(arguments: dict[str, Any]) -> bool:
    if str(arguments.get("path") or "").strip():
        return True
    paths = arguments.get("paths")
    if isinstance(paths, (list, tuple)) and any(str(p).strip() for p in paths):
        return True
    return False


# Capability operations whose spec requires the singular `path` argument.
# `paths` alone never satisfies these at the policy gateway.
_PATH_REQUIRED_OPERATIONS = frozenset({"create", "read", "exists", "stat", "delete", "mkdir"})


def _bind_action_paths_to_scope(
    actions: tuple[ActionRequestV1, ...],
    target_scope: list[str] | tuple[str, ...],
) -> tuple[ActionRequestV1, ...]:
    """Fill missing path/paths from step target_scope.

    Planners often coerce to filesystem.create/patch with empty arguments (purpose
    stuffed into operation). Without this, execute dies as
    INVALID_ARGUMENTS:missing required arguments: ['path'].
    Multi-file create without paths becomes one filesystem.patch over the scope
    so greenfield bodies still go through codegen instead of empty create stubs.
    """
    scope = [str(p).strip().replace("\\", "/") for p in target_scope if str(p).strip()]
    if not actions or not scope:
        return actions
    out: list[ActionRequestV1] = []
    for action in actions:
        args = dict(action.arguments or {})
        if action.capability != "filesystem":
            out.append(action)
            continue
        if _action_has_path_args(args):
            if action.operation in _PATH_REQUIRED_OPERATIONS and not str(args.get("path") or "").strip():
                # Planner emitted `paths` but the spec requires singular `path`.
                # Promote the first path instead of dying at the policy gateway
                # with INVALID_ARGUMENTS:missing required arguments: ['path'].
                first_path = next(
                    (str(p).strip() for p in (args.get("paths") or []) if str(p).strip()),
                    "",
                )
                if first_path:
                    args["path"] = first_path
                    out.append(action.model_copy(update={"arguments": args}))
                    continue
                # No usable path at all: fall through to scope-based fill below.
            else:
                out.append(action)
                continue
        if action.operation == "patch":
            args["paths"] = list(scope)
            out.append(action.model_copy(update={"arguments": args}))
            continue
        if action.operation == "mkdir":
            # Directory of first scoped file, or the scoped dir itself.
            first = scope[0]
            args["path"] = first.rsplit("/", 1)[0] if "/" in first and not first.endswith("/") else first
            out.append(action.model_copy(update={"arguments": args}))
            continue
        if action.operation in {"create", "read", "exists", "stat", "delete"}:
            if action.operation == "create" and len(scope) > 1:
                out.append(
                    ActionRequestV1(
                        capability="filesystem",
                        operation="patch",
                        arguments={**args, "paths": list(scope)},
                        expected_observation=action.expected_observation,
                    )
                )
                continue
            args["path"] = scope[0]
            out.append(action.model_copy(update={"arguments": args}))
            continue
        out.append(action)
    return tuple(out)


def _raw_structured_response(raw_text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {"_unparsed_text": str(raw_text or "")[:20_000]}
    if isinstance(parsed, dict):
        return parsed
    return {"_non_object": parsed}


def _transform(
    *,
    kind: str,
    field: str,
    op: str,
    step_index: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"kind": kind, "field": field, "op": op}
    if step_index is not None:
        payload["step_index"] = step_index
    payload.update(extra)
    return payload


@dataclass(frozen=True)
class StepContractProposalV1:
    """Either a validated first `StepContractV1` plus optional `PlanRevisionV1`
    (`contract`/`plan` set), or a refusal (`contract` is `None`)."""

    contract: StepContractV1 | None
    reply: str
    reason: str | None = None
    plan: PlanRevisionV1 | None = None
    # C040: prompt/model/usage capture (never secret-bearing)
    provenance: PlannerCallProvenance | None = None
    evidence_requests: tuple[dict[str, str], ...] = ()
    used_legacy_one_step: bool = False
    planner_call: dict[str, Any] | None = None
    transformations: tuple[dict[str, Any], ...] = ()
    budget_report: dict[str, Any] | None = None
    provenance_plan: object | None = None
    provenance_decision: str | None = None


def _proposal(
    *,
    contract: StepContractV1 | None,
    reply: str,
    reason: str | None = None,
    plan: PlanRevisionV1 | None = None,
    provenance: PlannerCallProvenance | None = None,
    evidence_requests: tuple[dict[str, str], ...] = (),
    used_legacy_one_step: bool = False,
    planner_call: dict[str, Any] | None = None,
    transformations: tuple[dict[str, Any], ...] | list[dict[str, Any]] = (),
    budget_report: dict[str, Any] | None = None,
    provenance_plan: object | None = None,
    provenance_decision: str | None = None,
) -> StepContractProposalV1:
    artifact = dict(planner_call or {})
    if artifact and transformations and "transformations" not in artifact:
        artifact["transformations"] = [dict(item) for item in transformations]
    return StepContractProposalV1(
        contract=contract,
        reply=reply,
        reason=reason,
        plan=plan,
        provenance=provenance,
        evidence_requests=evidence_requests,
        used_legacy_one_step=used_legacy_one_step,
        planner_call=artifact or planner_call,
        transformations=tuple(dict(item) for item in transformations),
        budget_report=budget_report,
        provenance_plan=provenance_plan,
        provenance_decision=provenance_decision,
    )


def materialize_obligation_seed_steps(
    seed: ObligationStepSeedResult,
    *,
    tenant_id: str,
    cycle_id: str,
    assigned_role: str,
    step_budget: BudgetV1,
    outcome_contract_id: str | None = None,
    outcome_contract_hash: str | None = None,
) -> list[StepContractV1]:
    """Turn deterministic seed candidates into StepContractV1 rows (DoD-002D)."""
    if not seed.ok or not seed.steps:
        return []
    key_to_index = {s.step_key: i for i, s in enumerate(seed.steps)}
    contracts: list[StepContractV1] = []
    for index, cand in enumerate(seed.steps):
        contract = StepContractV1(
            cycle_id=cycle_id,
            tenant_id=tenant_id,
            step_index=index,
            purpose=cand.purpose,
            target_scope=tuple(cand.target_scope_hints),
            expected_output_schema_ref="aix.step_contract.v1.obligation_seed",
            acceptance_criteria=cand.acceptance_criteria,
            validation_commands=("echo obligation-seed-validation",),
            assigned_role=assigned_role,  # type: ignore[arg-type]
            risk_tier=RiskTier.LOW,
            step_budget=step_budget,
            obligation_refs=cand.obligation_refs,
            outcome_contract_id=outcome_contract_id,
            outcome_contract_hash=outcome_contract_hash,
            facet_refs=cand.facet_refs,
            operation_refs=cand.operation_refs,
            entity_refs=cand.entity_refs,
            mapping_refs=cand.mapping_refs,
            binding_role="IMPLEMENT",
            status="PENDING",
        )
        contracts.append(contract)

    id_by_key = {seed.steps[i].step_key: contracts[i].id for i in range(len(contracts))}
    resolved: list[StepContractV1] = []
    for index, cand in enumerate(seed.steps):
        dep_ids = tuple(
            id_by_key[k]
            for k in cand.depends_on_step_keys
            if k in id_by_key and key_to_index.get(k, index) != index
        )
        contract = contracts[index]
        if dep_ids:
            data = contract.model_dump()
            data["depends_on"] = dep_ids
            contract = StepContractV1.model_validate(data)
        resolved.append(contract)
    return resolved


def _extract_greenfield_create_paths(
    *,
    intent_text: str,
    create_roots: tuple[str, ...],
) -> tuple[str, ...]:
    """CREATE files under Governance create_root (proposed/...).

    Prefer user-named paths. When a product-class CREATE omitted filenames,
    synthesize facet-justified stack defaults under the inferred product root.
    """
    from backend.aix_core.runtime.objective.product_class import infer_greenfield_product_slug
    from backend.aix_core.runtime.objective.product_structure_plan import _stack_ext
    from backend.aix_core.runtime.objective.provenance.validators import (
        create_root_contains,
        normalize_rel_path,
    )

    roots = create_roots or ("proposed/",)
    text = str(intent_text or "")
    slug_m = re.search(r"(?i)\bproposed[/\\]([\w.-]+)\b", text)
    if slug_m:
        token = slug_m.group(1)
        if "." in token:
            token = token.split(".", 1)[0]
        slug = f"proposed/{token}"
    else:
        inferred = infer_greenfield_product_slug(objective=text)
        slug = f"proposed/{inferred}" if inferred else None
    path_re = re.compile(
        r"(?i)(?<![A-Za-z0-9])("
        r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\."
        r"(?:py|php|ts|tsx|js|sql|md|json|yml|yaml|html|css)"
        r"|[A-Za-z0-9_.-]+\."
        r"(?:py|php|ts|tsx|js|sql|md|json|yml|yaml|html|css)"
        r")"
    )
    found: list[str] = []
    seen: set[str] = set()
    for match in path_re.finditer(text):
        raw = match.group(1).replace("\\", "/")
        if "/" not in raw and slug:
            raw = f"{slug}/{raw}"
        norm = normalize_rel_path(raw) or raw
        if not any(create_root_contains(target=norm, root=root) for root in roots):
            continue
        if norm in seen:
            continue
        seen.add(norm)
        found.append(norm)
    if found:
        return tuple(found)
    if not slug:
        return ()
    defaults: list[str] = [f"{slug}/index.html"]
    if re.search(r"(?i)\bcss\b", text):
        defaults.append(f"{slug}/styles.css")
    if re.search(r"(?i)\bjavascript\b|\.js\b", text):
        defaults.append(f"{slug}/app.js")
    ext = _stack_ext(text, kind="api") or "py"
    defaults.append(f"{slug}/api.{ext}")
    if re.search(r"(?i)\b(sql|sqlite|mysql|postgres|database|schema)\b", text):
        defaults.append(f"{slug}/database.sql")
    if re.search(r"(?i)\breadme\b", text):
        defaults.append(f"{slug}/README.md")
    if re.search(r"(?i)\b(pytest|api tests|automated tests)\b", text):
        defaults.append(f"{slug}/tests/test_api.py")
    out: list[str] = []
    for raw in defaults:
        norm = normalize_rel_path(raw) or raw
        if not any(create_root_contains(target=norm, root=root) for root in roots):
            continue
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return tuple(out)


def _try_product_structure_plan_under_provenance(
    *,
    reply: str,
    provenance: PlannerCallProvenance | None,
    planner_call: dict[str, Any],
    transformations: list[dict[str, Any]],
    budget_report: dict[str, Any] | None,
    detail: str,
    plan_request: PlanRequest | None,
    intent: Any,
    understanding_revision: Any,
    needs: tuple[Any, ...],
    exploration_evidence: tuple[Any, ...],
    governance: Any,
    tenant_id: str,
    cycle_id: str,
    assigned_role: str,
    step_budget: BudgetV1,
    objective_id: str,
    understanding: UnderstandingEvidenceV1,
    plan_revision: int,
    oc_id: str | None,
    oc_hash: str | None,
    outcome_contract: object | None,
) -> StepContractProposalV1 | None:
    """PD-003B: ProductStructureV1 → governed PlanTargets (prefer over seed/legacy)."""
    if plan_request is None or intent is None or understanding_revision is None or governance is None:
        return None
    if outcome_contract is None:
        return None
    from backend.aix_core.runtime.objective.outcome_contract import OutcomeContractV1
    from backend.aix_core.runtime.objective.product_structure import compile_product_structure
    from backend.aix_core.runtime.objective.product_structure_plan import (
        compile_product_structure_plan,
    )
    from backend.aix_core.runtime.objective.provenance.plan_request import construct_plan_revision

    if not isinstance(outcome_contract, OutcomeContractV1):
        return None
    if "CREATE_FILE" not in tuple(getattr(governance, "create_capability", ()) or ()):
        return None

    outcomes = tuple(
        c
        for c in getattr(understanding_revision, "claims", ()) or ()
        if getattr(c, "kind", None) == "OUTCOME" and getattr(c, "status", None) == "ACTIVE"
    )
    if not outcomes:
        return None
    outcome_ids = tuple(str(c.id) for c in outcomes[:1])

    ps = compile_product_structure(outcome_contract)
    if not ps.ok or ps.structure is None:
        transformations.append(
            _transform(
                kind="SYSTEM_REJECTED",
                field="targets",
                op="product_structure_plan_structure_failed",
                detail=f"{ps.code}:{ps.detail}"[:240],
            )
        )
        return None

    plan_result = compile_product_structure_plan(
        contract=outcome_contract,
        structure=ps.structure,
        intent_text=str(getattr(intent, "text", "") or ""),
        create_roots=tuple(getattr(governance, "create_root", ()) or ()),
        create_capability=tuple(getattr(governance, "create_capability", ()) or ()),
        outcome_claim_ids=outcome_ids,
    )
    if not plan_result.ok:
        transformations.append(
            _transform(
                kind="SYSTEM_REJECTED",
                field="targets",
                op="product_structure_plan_rejected",
                detail=f"{plan_result.code}:{plan_result.detail}"[:240],
            )
        )
        return None

    construction = construct_plan_revision(
        request=plan_request,
        intent=intent,
        understanding=understanding_revision,
        needs=needs,
        evidence=exploration_evidence,
        governance=governance,
        targets=plan_result.targets,
        step_scopes=plan_result.step_scopes,
        transformations=tuple(transformations),
        revision=plan_revision,
        cycle_id=cycle_id,
    )
    if not construction.validation.ok:
        transformations.append(
            _transform(
                kind="SYSTEM_REJECTED",
                field="targets",
                op="product_structure_plan_validation_rejected",
                detail=f"{construction.validation.rule_id}:{construction.validation.detail}"[:240],
            )
        )
        return None

    transformations.append(
        _transform(
            kind="SYSTEM_APPLIED",
            field="targets",
            op="product_structure_plan_compiled",
            detail=(
                f"targets={len(plan_result.targets)};steps={len(plan_result.step_scopes)};"
                f"root={plan_result.product_root};prior={detail[:80]}"
            ),
        )
    )

    from backend.aix_core.runtime.objective.product_structure_step_binding import (
        apply_semantic_bindings_to_steps,
        compile_product_structure_step_bindings,
    )

    # PD-003B2: deliberate semantic refs from ProductStructure (not filename dumps).
    binding_result = compile_product_structure_step_bindings(
        contract=outcome_contract,
        structure=ps.structure,
        step_scopes=plan_result.step_scopes,
        traces=plan_result.traces,
    )
    if not binding_result.ok:
        transformations.append(
            _transform(
                kind="SYSTEM_REJECTED",
                field="steps",
                op="product_structure_step_binding_rejected",
                detail=f"{binding_result.code}:{binding_result.detail}"[:240],
            )
        )
        return None

    from backend.aix_core.runtime.objective.flow_coherence import validate_product_structure_flow_coherence

    coherence = validate_product_structure_flow_coherence(
        contract=outcome_contract,
        structure=ps.structure,
        bindings=binding_result.bindings,
        step_scopes=plan_result.step_scopes,
        depends_on=plan_result.depends_on,
    )
    if not coherence.ok:
        transformations.append(
            _transform(
                kind="SYSTEM_REJECTED",
                field="steps",
                op="product_structure_flow_coherence_rejected",
                detail=f"{coherence.code}:{coherence.detail}"[:240],
            )
        )
        return None

    from backend.aix_core.runtime.verification.coverage_alignment import (
        validate_product_structure_coverage_alignment,
    )

    alignment = validate_product_structure_coverage_alignment(
        contract=outcome_contract,
        structure=ps.structure,
        bindings=binding_result.bindings,
    )
    if not alignment.ok:
        transformations.append(
            _transform(
                kind="SYSTEM_REJECTED",
                field="steps",
                op="product_structure_coverage_alignment_rejected",
                detail=f"{alignment.code}:{alignment.detail}"[:240],
            )
        )
        return None

    role_purpose = {
        "DIRECTORY": "Create governed product directory under create_root",
        "DB_SCHEMA": "Create database/schema artifacts from ProductStructure",
        "API_BACKEND": "Create API/backend artifacts from ProductStructure",
        "UI_ASSET": "Create shared UI assets from ProductStructure plan",
        "UI_PAGE": "Create UI page views from ProductStructure",
        "SUPPORTING": "Create supporting artifacts under create_root",
    }

    # Executable StepContracts: one per ordered scope group with depends_on
    contracts: list[StepContractV1] = []
    id_by_index: dict[int, str] = {}
    for index, scope in enumerate(plan_result.step_scopes):
        role = (
            binding_result.bindings[index].role
            if index < len(binding_result.bindings)
            else "SUPPORTING"
        )
        purpose = role_purpose.get(role, "Create governed artifacts under create_root")
        contract = StepContractV1(
            cycle_id=cycle_id,
            tenant_id=tenant_id,
            step_index=index,
            purpose=f"{purpose}",
            target_scope=scope,
            expected_output_schema_ref="aix.step_contract.v1.generic_edit",
            acceptance_criteria=tuple(f"{p} exists" for p in scope)
            + ("no existing repository files outside create_root are modified",),
            validation_commands=tuple(
                f"test -d {p}" if p.endswith("/") else f"test -f {p}" for p in scope
            ),
            assigned_role=assigned_role,
            step_budget=step_budget,
            obligation_refs=(),
            required_effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
            prohibited_actions=("Do not modify files outside Governance create_root",),
            risk_tier=RiskTier.MEDIUM,
            depends_on=(),
            outcome_contract_id=oc_id,
            outcome_contract_hash=oc_hash,
            status="PENDING",
        )
        id_by_index[index] = contract.id
        contracts.append(contract)

    contracts = list(
        apply_semantic_bindings_to_steps(
            steps=tuple(contracts),
            bindings=binding_result.bindings,
        )
    )
    # SEM-001-W3: role-aware acceptance when ProductStructure refs are nonempty.
    # Empty-ref steps (DIRECTORY / README / thin support) keep existence-only AC.
    from backend.aix_core.runtime.planner.step_acceptance_continuity import (
        build_role_aware_acceptance_criteria,
    )

    enriched: list[StepContractV1] = []
    for index, contract in enumerate(contracts):
        binding = (
            binding_result.bindings[index]
            if index < len(binding_result.bindings)
            else None
        )
        role = binding.role if binding is not None else "SUPPORTING"
        ac = build_role_aware_acceptance_criteria(
            scope=contract.target_scope,
            role=role,
            operation_refs=contract.operation_refs,
            entity_refs=contract.entity_refs,
            mapping_refs=contract.mapping_refs,
        )
        enriched.append(contract.model_copy(update={"acceptance_criteria": ac}))
    contracts = enriched
    transformations.append(
        _transform(
            kind="SYSTEM_APPLIED",
            field="steps",
            op="product_structure_step_binding_applied",
            detail=(
                f"bindings={len(binding_result.bindings)};"
                f"code={binding_result.code}"
            ),
        )
    )
    transformations.append(
        _transform(
            kind="SYSTEM_APPLIED",
            field="acceptance_criteria",
            op="sem001_w3_role_aware_acceptance",
            detail=f"steps={len(contracts)};helper=sem001.w3.v1",
        )
    )

    resolved: list[StepContractV1] = []
    for index, contract in enumerate(contracts):
        dep_indexes = (
            plan_result.depends_on[index] if index < len(plan_result.depends_on) else ()
        )
        depends_on = tuple(
            id_by_index[i] for i in dep_indexes if i in id_by_index and i != index
        )
        if depends_on:
            data = contract.model_dump()
            data["depends_on"] = depends_on
            # Preserve semantic refs stamped by PD-003B2
            contract = StepContractV1.model_validate(data)
        resolved.append(contract)

    coverage_ids = tuple(
        sorted({oid for b in binding_result.bindings for oid in b.obligation_refs})
    ) or tuple(ps.structure.obligation_ids)
    plan = PlanRevisionV1(
        objective_id=objective_id or cycle_id,
        cycle_id=cycle_id,
        revision=plan_revision,
        created_from_evidence=understanding.id,
        steps=tuple(resolved),
        obligation_coverage=coverage_ids,
        planner_reasoning_ref=reply or "product-structure-plan-pd003b2",
        status="DRAFT",
        intent_id=intent.id,
        understanding_revision_id=understanding_revision.id,
        evidence_ids=(),
        governance_revision_id=governance.id,
    )
    first = resolved[0] if resolved else None
    planner_call["resulting_plan_revision_id"] = plan.plan_id
    planner_call["product_structure_plan"] = True
    planner_call["product_structure_step_binding"] = True
    planner_call["product_structure_id"] = ps.structure.structure_id
    planner_call["transformations"] = transformations
    return _proposal(
        contract=first,
        reply=reply
        or "Compiled PlanTargets + semantic StepContract bindings (PD-003B/B2)",
        reason=None,
        plan=plan,
        provenance=provenance,
        used_legacy_one_step=False,
        planner_call=planner_call,
        transformations=transformations,
        budget_report=budget_report,
        provenance_plan=construction.plan,
        provenance_decision=construction.validation.decision,
    )


def _try_greenfield_create_under_provenance(
    *,
    reply: str,
    provenance: PlannerCallProvenance | None,
    planner_call: dict[str, Any],
    transformations: list[dict[str, Any]],
    budget_report: dict[str, Any] | None,
    detail: str,
    plan_request: PlanRequest | None,
    intent: Any,
    understanding_revision: Any,
    needs: tuple[Any, ...],
    exploration_evidence: tuple[Any, ...],
    governance: Any,
    tenant_id: str,
    cycle_id: str,
    assigned_role: str,
    step_budget: BudgetV1,
    objective_id: str,
    understanding: UnderstandingEvidenceV1,
    plan_revision: int,
    oc_id: str | None,
    oc_hash: str | None,
) -> StepContractProposalV1 | None:
    """When LLM/seed cannot ACCEPT, bootstrap CREATE PlanTargets from Intent paths."""
    if plan_request is None or intent is None or understanding_revision is None or governance is None:
        return None
    if "CREATE_FILE" not in tuple(getattr(governance, "create_capability", ()) or ()):
        return None
    create_roots = tuple(getattr(governance, "create_root", ()) or ())
    paths = _extract_greenfield_create_paths(
        intent_text=str(getattr(intent, "text", "") or ""),
        create_roots=create_roots,
    )
    if not paths:
        return None
    outcomes = tuple(
        c
        for c in getattr(understanding_revision, "claims", ()) or ()
        if getattr(c, "kind", None) == "OUTCOME" and getattr(c, "status", None) == "ACTIVE"
    )
    if not outcomes:
        return None
    outcome_ids = (outcomes[0].id,)
    root = create_roots[0] if create_roots else "proposed/"
    from backend.aix_core.runtime.objective.provenance.contracts import PlanTarget
    from backend.aix_core.runtime.objective.provenance.plan_request import construct_plan_revision

    targets: list[PlanTarget] = []
    parents = sorted(
        {
            p.rsplit("/", 1)[0] + "/"
            for p in paths
            if "/" in p
        }
    )
    for parent in parents:
        targets.append(
            PlanTarget(
                kind="CREATE",
                path=parent,
                effect="LITERAL_REPLACE",
                rationale=f"Create governed directory {parent}",
                create_capability="CREATE_DIRECTORY",
                create_root=root,
                outcome_claim_ids=outcome_ids,
            )
        )
    for path in paths:
        targets.append(
            PlanTarget(
                kind="CREATE",
                path=path,
                effect="LITERAL_REPLACE",
                rationale=f"Create requested greenfield artifact {path}",
                create_capability="CREATE_FILE",
                create_root=root,
                outcome_claim_ids=outcome_ids,
            )
        )
    construction = construct_plan_revision(
        request=plan_request,
        intent=intent,
        understanding=understanding_revision,
        needs=needs,
        evidence=exploration_evidence,
        governance=governance,
        targets=tuple(targets),
        step_scopes=(paths,),
        transformations=tuple(transformations),
        revision=plan_revision,
        cycle_id=cycle_id,
    )
    if not construction.validation.ok:
        transformations.append(
            _transform(
                kind="SYSTEM_REJECTED",
                field="targets",
                op="greenfield_create_bootstrap_rejected",
                detail=f"{construction.validation.rule_id}:{construction.validation.detail}"[:240],
            )
        )
        return None
    transformations.append(
        _transform(
            kind="SYSTEM_APPLIED",
            field="targets",
            op="greenfield_create_bootstrap",
            detail=f"from_intent paths={len(paths)}; prior={detail[:120]}",
        )
    )
    contract = StepContractV1(
        cycle_id=cycle_id,
        tenant_id=tenant_id,
        step_index=0,
        purpose="Create requested greenfield files under Governance create_root",
        target_scope=paths,
        expected_output_schema_ref="aix.step_contract.v1.generic_edit",
        acceptance_criteria=tuple(f"{p} exists" for p in paths)
        + ("no existing repository files outside create_root are modified",),
        validation_commands=tuple(f"test -f {p}" for p in paths),
        assigned_role=assigned_role,
        step_budget=step_budget,
        obligation_refs=(),
        required_effects=(RequiredEffectV1(kind="LITERAL_REPLACE"),),
        prohibited_actions=("Do not modify files outside Governance create_root",),
        risk_tier=RiskTier.MEDIUM,
        depends_on=(),
        outcome_contract_id=oc_id,
        outcome_contract_hash=oc_hash,
        status="PENDING",
    )
    plan = PlanRevisionV1(
        objective_id=objective_id or cycle_id,
        cycle_id=cycle_id,
        revision=plan_revision,
        created_from_evidence=understanding.id,
        steps=(contract,),
        obligation_coverage=(),
        planner_reasoning_ref=reply or "greenfield-create-bootstrap",
        status="DRAFT",
        intent_id=intent.id,
        understanding_revision_id=understanding_revision.id,
        evidence_ids=(),
        governance_revision_id=governance.id,
    )
    planner_call["resulting_plan_revision_id"] = plan.plan_id
    planner_call["greenfield_create_bootstrap"] = True
    planner_call["transformations"] = transformations
    return _proposal(
        contract=contract,
        reply=reply or "Bootstrapped CREATE PlanTargets from Intent under create_root",
        reason=None,
        plan=plan,
        provenance=provenance,
        used_legacy_one_step=False,
        planner_call=planner_call,
        transformations=transformations,
        budget_report=budget_report,
        provenance_plan=construction.plan,
        provenance_decision=construction.validation.decision,
    )


def _existing_target_effect(path: str, intent_text: str) -> str:
    """Pick a governed effect for EXISTING evidence-backed targets."""
    text = str(intent_text or "")
    norm = str(path or "").replace("\\", "/").casefold()
    if re.search(r"(?i)preserve.*(public )?interface", text):
        if any(token in norm for token in ("router", "api", "service", "handler", "endpoint")):
            return "PRESERVE_PUBLIC_INTERFACE"
    if "test" in norm or norm.startswith("tests/"):
        return "RUN_DECLARED_VALIDATION"
    return "LITERAL_REPLACE"


def _assign_evidence_scopes_for_steps(
    paths: tuple[str, ...],
    step_count: int,
) -> tuple[tuple[str, ...], ...]:
    """Partition grounded evidence paths across planner steps (deterministic)."""
    if not paths or step_count < 1:
        return ()
    if step_count == 1:
        return (paths,)
    scopes: list[tuple[str, ...]] = []
    for index in range(step_count):
        assigned = tuple(path for offset, path in enumerate(paths) if offset % step_count == index)
        if not assigned:
            assigned = (paths[index % len(paths)],)
        scopes.append(assigned)
    return tuple(scopes)


def _try_existing_evidence_plan_under_provenance(
    *,
    reply: str,
    provenance: PlannerCallProvenance | None,
    planner_call: dict[str, Any],
    transformations: list[dict[str, Any]],
    budget_report: dict[str, Any] | None,
    detail: str,
    plan_request: PlanRequest | None,
    intent: Any,
    understanding_revision: Any,
    needs: tuple[Any, ...],
    exploration_evidence: tuple[Any, ...],
    governance: Any,
    tenant_id: str,
    cycle_id: str,
    assigned_role: str,
    step_budget: BudgetV1,
    objective_id: str,
    understanding: UnderstandingEvidenceV1,
    plan_revision: int,
    oc_id: str | None,
    oc_hash: str | None,
    seed: ObligationStepSeedResult | None = None,
) -> StepContractProposalV1 | None:
    """Deterministic EXISTING PlanTargets from typed ExplorationEvidence.

    Used when the LLM fails or omits targets[] for refactor/modify objectives that
    already have repository evidence — obligation seed alone cannot ACCEPT under
    provenance because seed scopes live under proposed/obligation_seed/.
    """
    if plan_request is None or intent is None or understanding_revision is None or governance is None:
        return None
    evidence_items = [
        item
        for item in exploration_evidence
        if str(getattr(item, "location", "") or "").strip()
        and str(getattr(item, "id", "") or "").strip()
    ]
    if not evidence_items:
        return None
    outcomes = tuple(
        c
        for c in getattr(understanding_revision, "claims", ()) or ()
        if getattr(c, "kind", None) == "OUTCOME" and getattr(c, "status", None) == "ACTIVE"
    )
    if not outcomes:
        outcomes = tuple(
            c
            for c in getattr(understanding_revision, "claims", ()) or ()
            if getattr(c, "status", None) == "ACTIVE"
        )[:1]
    if not outcomes:
        return None
    outcome_ids = tuple(str(c.id) for c in outcomes[:1])
    intent_text = str(getattr(intent, "text", "") or "")
    intent_fold = intent_text.replace("\\", "/").casefold()
    # Prefer concrete files named in the intent / .py modules over directories and tests.
    ranked: list[Any] = []
    for item in evidence_items:
        path = str(getattr(item, "location", "") or "").strip().replace("\\", "/")
        if not path or path.endswith("/"):
            continue
        base = path.rsplit("/", 1)[-1].casefold()
        score = 0
        if path.casefold() in intent_fold or base in intent_fold:
            score += 100
        if path.endswith(".py"):
            score += 20
        if "/services/" in path.replace("\\", "/"):
            score += 10
        if path.startswith("proposed/") or "/tests/" in f"/{path}/":
            score -= 50
        ranked.append((score, path, item))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    if any(score >= 100 for score, _, _ in ranked):
        ranked = [row for row in ranked if row[0] >= 20] or ranked
    ordered_paths: list[str] = []
    seen_paths: set[str] = set()
    targets: list[PlanTarget] = []
    for _score, path, item in ranked:
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)
        ordered_paths.append(path)
        targets.append(
            PlanTarget(
                kind="EXISTING",
                path=path,
                effect=_existing_target_effect(path, intent_text),  # type: ignore[arg-type]
                rationale=f"Modify grounded repository evidence at {path}",
                evidence_id=str(getattr(item, "id", "") or ""),
                outcome_claim_ids=outcome_ids,
            )
        )
        if len(targets) >= 8:
            break
    if not targets:
        return None
    path_tuple = tuple(ordered_paths)
    from backend.aix_core.runtime.objective.provenance.plan_request import construct_plan_revision

    seed_contracts: list[StepContractV1] = [
        StepContractV1(
            cycle_id=cycle_id,
            tenant_id=tenant_id,
            step_index=0,
            purpose=str(intent_text or "Implement objective on grounded evidence files")[:240],
            target_scope=path_tuple,
            expected_output_schema_ref="aix.step_contract.v1.generic_edit",
            acceptance_criteria=tuple(
                dict.fromkeys(
                    [
                        *(
                            str(x).strip()
                            for x in (understanding.observed_problem or "").split(";")
                            if str(x).strip()
                        ),
                        "Changes remain within grounded evidence paths",
                    ]
                )
            )[:8]
            or ("Objective satisfied on cited evidence files",),
            validation_commands=tuple(f"test -f {p}" for p in path_tuple[:4] if str(p).strip())
            or ("echo existing-evidence-plan-validation",),
            assigned_role=assigned_role,  # type: ignore[arg-type]
            step_budget=step_budget,
            obligation_refs=(),
            required_effects=(
                RequiredEffectV1(kind=_existing_target_effect(path_tuple[0], intent_text)),
            ),
            prohibited_actions=("Do not modify files outside grounded evidence paths",),
            risk_tier=RiskTier.MEDIUM,
            depends_on=(),
            outcome_contract_id=oc_id,
            outcome_contract_hash=oc_hash,
            status="PENDING",
        )
    ]
    step_scopes = tuple(c.target_scope for c in seed_contracts)
    construction = construct_plan_revision(
        request=plan_request,
        intent=intent,
        understanding=understanding_revision,
        needs=needs,
        evidence=exploration_evidence,
        governance=governance,
        targets=tuple(targets),
        step_scopes=step_scopes,
        transformations=tuple(transformations),
        revision=plan_revision,
        cycle_id=cycle_id,
    )
    if not construction.validation.ok or construction.validation.decision != "ACCEPT":
        transformations.append(
            _transform(
                kind="SYSTEM_REJECTED",
                field="targets",
                op="existing_evidence_plan_rejected",
                detail=f"{construction.validation.rule_id}:{construction.validation.detail}"[:240],
            )
        )
        return None
    transformations.append(
        _transform(
            kind="SYSTEM_APPLIED",
            field="targets",
            op="existing_evidence_plan_compiled",
            detail=(
                f"targets={len(targets)};steps={len(seed_contracts)};"
                f"prior={detail[:120]}"
            ),
        )
    )
    coverage_ids = tuple(dict.fromkeys(ref for c in seed_contracts for ref in c.obligation_refs))
    plan = PlanRevisionV1(
        objective_id=objective_id or cycle_id,
        cycle_id=cycle_id,
        revision=plan_revision,
        created_from_evidence=understanding.id,
        steps=tuple(seed_contracts),
        obligation_coverage=coverage_ids,
        planner_reasoning_ref=reply or "existing-evidence-plan-bootstrap",
        status="DRAFT",
        intent_id=intent.id,
        understanding_revision_id=understanding_revision.id,
        evidence_ids=tuple(t.evidence_id for t in targets if t.evidence_id),
        governance_revision_id=governance.id,
    )
    planner_call["resulting_plan_revision_id"] = plan.plan_id
    planner_call["existing_evidence_plan_bootstrap"] = True
    planner_call["transformations"] = transformations
    return _proposal(
        contract=seed_contracts[0],
        reply=reply or "Compiled EXISTING PlanTargets from grounded ExplorationEvidence",
        reason=None,
        plan=plan,
        provenance=provenance,
        used_legacy_one_step=False,
        planner_call=planner_call,
        transformations=transformations,
        budget_report=budget_report,
        provenance_plan=construction.plan,
        provenance_decision=construction.validation.decision,
    )


def _refuse_seed_under_provenance(
    *,
    reply: str,
    provenance: PlannerCallProvenance | None,
    planner_call: dict[str, Any],
    transformations: list[dict[str, Any]],
    budget_report: dict[str, Any] | None,
    detail: str,
    plan_request: PlanRequest | None = None,
    intent: Any = None,
    understanding_revision: Any = None,
    needs: tuple[Any, ...] = (),
    exploration_evidence: tuple[Any, ...] = (),
    governance: Any = None,
    tenant_id: str = "",
    cycle_id: str = "",
    assigned_role: str = "executor",
    step_budget: BudgetV1 | None = None,
    objective_id: str = "",
    understanding: UnderstandingEvidenceV1 | None = None,
    plan_revision: int = 1,
    oc_id: str | None = None,
    oc_hash: str | None = None,
    outcome_contract: object | None = None,
    seed: ObligationStepSeedResult | None = None,
) -> StepContractProposalV1:
    """DoD seed must not ACCEPT under live provenance Intent.

    Prefer PD-003B ProductStructure → PlanTargets, then EXISTING evidence bootstrap,
    then greenfield Intent bootstrap. Otherwise refuse with a clear code (not LEGACY).
    """
    if (
        step_budget is not None
        and understanding is not None
        and plan_request is not None
    ):
        structured = _try_product_structure_plan_under_provenance(
            reply=reply,
            provenance=provenance,
            planner_call=planner_call,
            transformations=transformations,
            budget_report=budget_report,
            detail=detail,
            plan_request=plan_request,
            intent=intent,
            understanding_revision=understanding_revision,
            needs=needs,
            exploration_evidence=exploration_evidence,
            governance=governance,
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            assigned_role=assigned_role,
            step_budget=step_budget,
            objective_id=objective_id,
            understanding=understanding,
            plan_revision=plan_revision,
            oc_id=oc_id,
            oc_hash=oc_hash,
            outcome_contract=outcome_contract,
        )
        if structured is not None:
            return structured
        existing = _try_existing_evidence_plan_under_provenance(
            reply=reply,
            provenance=provenance,
            planner_call=planner_call,
            transformations=transformations,
            budget_report=budget_report,
            detail=detail,
            plan_request=plan_request,
            intent=intent,
            understanding_revision=understanding_revision,
            needs=needs,
            exploration_evidence=exploration_evidence,
            governance=governance,
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            assigned_role=assigned_role,
            step_budget=step_budget,
            objective_id=objective_id,
            understanding=understanding,
            plan_revision=plan_revision,
            oc_id=oc_id,
            oc_hash=oc_hash,
            seed=seed,
        )
        if existing is not None:
            return existing
        bootstrapped = _try_greenfield_create_under_provenance(
            reply=reply,
            provenance=provenance,
            planner_call=planner_call,
            transformations=transformations,
            budget_report=budget_report,
            detail=detail,
            plan_request=plan_request,
            intent=intent,
            understanding_revision=understanding_revision,
            needs=needs,
            exploration_evidence=exploration_evidence,
            governance=governance,
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            assigned_role=assigned_role,
            step_budget=step_budget,
            objective_id=objective_id,
            understanding=understanding,
            plan_revision=plan_revision,
            oc_id=oc_id,
            oc_hash=oc_hash,
        )
        if bootstrapped is not None:
            return bootstrapped
    transformations.append(
        _transform(
            kind="SYSTEM_REJECTED",
            field="steps",
            op="obligation_seed_forbidden_under_provenance",
            detail=detail[:240],
        )
    )
    planner_call["transformations"] = transformations
    return _proposal(
        contract=None,
        reply=reply,
        reason=f"PROVENANCE_SEED_FORBIDDEN:{detail}",
        provenance=provenance,
        planner_call=planner_call,
        transformations=transformations,
        budget_report=budget_report,
        provenance_decision="REJECT",
    )


def _build_seed_plan_proposal(
    *,
    seed: ObligationStepSeedResult,
    tenant_id: str,
    cycle_id: str,
    assigned_role: str,
    step_budget: BudgetV1,
    objective_id: str,
    understanding: UnderstandingEvidenceV1,
    plan_revision: int,
    reply: str,
    reason: str | None,
    provenance: PlannerCallProvenance | None,
    planner_call: dict[str, Any],
    transformations: list[dict[str, Any]],
    budget_report: dict[str, Any] | None,
    oc_id: str | None,
    oc_hash: str | None,
    intent: Any = None,
    understanding_revision: Any = None,
    governance: Any = None,
) -> StepContractProposalV1:
    resolved = materialize_obligation_seed_steps(
        seed,
        tenant_id=tenant_id,
        cycle_id=cycle_id,
        assigned_role=assigned_role,
        step_budget=step_budget,
        outcome_contract_id=oc_id,
        outcome_contract_hash=oc_hash,
    )
    if not resolved:
        planner_call["transformations"] = transformations
        return _proposal(
            contract=None,
            reply=reply,
            reason=reason or f"OBLIGATION_SEED_FAILED:{seed.code}",
            provenance=provenance,
            planner_call=planner_call,
            transformations=transformations,
            budget_report=budget_report,
        )
    coverage_ids = tuple(dict.fromkeys(ref for c in resolved for ref in c.obligation_refs))
    plan = PlanRevisionV1(
        objective_id=objective_id or cycle_id,
        cycle_id=cycle_id,
        revision=plan_revision,
        created_from_evidence=understanding.id,
        steps=tuple(resolved),
        obligation_coverage=coverage_ids,
        planner_reasoning_ref=reply or "obligation_step_seed",
        status="DRAFT",
        intent_id=intent.id if intent is not None else None,
        understanding_revision_id=understanding_revision.id if understanding_revision is not None else None,
        governance_revision_id=governance.id if governance is not None else None,
    )
    planner_call["resulting_plan_revision_id"] = plan.plan_id
    planner_call["obligation_seed_applied"] = True
    planner_call["obligation_seed_step_keys"] = [s.step_key for s in seed.steps]
    planner_call["transformations"] = transformations
    return _proposal(
        contract=resolved[0],
        reply=reply or "Applied deterministic obligation step seed (DoD-002D)",
        reason=None,
        plan=plan,
        provenance=provenance,
        used_legacy_one_step=False,
        planner_call=planner_call,
        transformations=transformations,
        budget_report=budget_report,
    )


async def propose_step_contract(
    *,
    objective: str,
    definition_of_done: list[str],
    understanding: UnderstandingEvidenceV1,
    constraints: list[str],
    tenant_id: str,
    cycle_id: str,
    step_index: int,
    assigned_role: str,
    step_budget: BudgetV1,
    actor_id: str,
    authorization_header: str,
    timeout_seconds: float,
    obligations: tuple[ObjectiveObligationV1, ...] = (),
    prior_failure_evidence: str | None = None,
    objective_id: str = "",
    file_excerpts: dict[str, str] | None = None,
    evidence_revision_ref: str | None = None,
    plan_request: PlanRequest | None = None,
    intent: UserIntent | None = None,
    understanding_revision: UnderstandingRevision | None = None,
    needs: tuple[ExplorationNeed, ...] = (),
    exploration_evidence: tuple[ExplorationEvidence, ...] = (),
    governance: GovernanceRevision | None = None,
    plan_revision: int = 1,
    outcome_contract: object | None = None,
    outcome_contract_obligations: tuple[object, ...] | list[object] = (),
    force_existing_evidence_bootstrap: bool = False,
    skip_llm_when_evidence_grounded: bool = False,
) -> StepContractProposalV1:
    from backend.aix_core.runtime.objective.outcome_obligations import (
        OutcomeContractObligationV1,
        obligations_for_planner,
    )

    oc_obligations = tuple(
        o
        for o in (outcome_contract_obligations or ())
        if isinstance(o, OutcomeContractObligationV1)
    )
    oc_planner_list = obligations_for_planner(oc_obligations) if oc_obligations else []
    oc_id = str(getattr(outcome_contract, "contract_id", "") or "") or None
    oc_hash = str(getattr(outcome_contract, "contract_hash", "") or "") or None
    if oc_obligations and not oc_id:
        oc_id = oc_obligations[0].contract_id
        oc_hash = oc_obligations[0].contract_hash

    # DoD-002D: deterministic coherent baseline from sealed obligations
    seed: ObligationStepSeedResult | None = None
    baseline_steps: list[dict[str, object]] | None = None
    if oc_obligations:
        seed = compile_obligation_step_seed(oc_obligations)
        if seed.ok:
            baseline_steps = candidates_for_planner(seed.steps)

    if plan_request is not None:
        evidence = build_provenance_planner_evidence(
            plan_request_sections(plan_request),
            outcome_contract_obligations=oc_planner_list or None,
            outcome_contract_id=oc_id,
            outcome_contract_hash=oc_hash,
            baseline_candidate_steps=baseline_steps,
        )
        # Compatibility boundary: provenance sections are authoritative, but the
        # planner prompt still exposes repository grounding from UnderstandingEvidenceV1
        # so NME/retry loops and PD-001-style proofs see files_read in the Evidence blob.
        if understanding.files_read:
            evidence["files_read"] = list(understanding.files_read)
        if understanding.symbols_found:
            evidence["symbols_found"] = list(understanding.symbols_found)
        if understanding.observed_problem:
            evidence["observed_problem"] = understanding.observed_problem
        if file_excerpts:
            evidence["file_excerpts"] = dict(file_excerpts)
        if obligations:
            evidence["obligations"] = [
                {"id": o.id, "description": o.description} for o in obligations
            ]
        if definition_of_done:
            evidence["definition_of_done"] = list(definition_of_done)
        if objective:
            evidence["objective"] = objective
        system_prompt = STEP_CONTRACT_PLANNER_PROVENANCE_PROMPT
    else:
        evidence = build_step_contract_planner_evidence(
            objective=objective,
            definition_of_done=definition_of_done,
            files_read=list(understanding.files_read),
            symbols_found=list(understanding.symbols_found),
            observed_problem=understanding.observed_problem,
            constraints=constraints,
            obligations=[{"id": o.id, "description": o.description} for o in obligations],
            prior_failure_evidence=prior_failure_evidence,
            executor_profile=default_executor_profile().model_dump(mode="json"),
            file_excerpts=file_excerpts or {},
            outcome_contract_obligations=oc_planner_list or None,
            outcome_contract_id=oc_id,
            outcome_contract_hash=oc_hash,
            baseline_candidate_steps=baseline_steps,
        )
        system_prompt = STEP_CONTRACT_PLANNER_SYSTEM_PROMPT
    budget = budget_planner_prompt(
        system_prompt=system_prompt,
        evidence=evidence,
        cap=int(_planner_settings.INTELLIGENCE_LAYER_PLANNER_INPUT_TOKEN_CAP),
    )
    system = (
        system_prompt
        + "\n\nEvidence:\n"
        + json.dumps(budget.evidence, sort_keys=True)
    )
    registry = load_model_registry()
    route = get_model_registry().get_route("planner")
    model_ref = route.model_ref
    provenance = capture_prompt_provenance(prompt_body=system, model_ref=model_ref)
    planner_call_id = new_ulid()
    planner_call: dict[str, Any] = {
        "schema": "aix.planner_call.v1",
        "planner_call_id": planner_call_id,
        "cycle_id": cycle_id,
        "objective_id": objective_id or cycle_id,
        "model": model_ref,
        "provider": None,
        "timestamp": provenance.captured_at,
        "prompt_hash": provenance.prompt_body_hash,
        "evidence_revision": evidence_revision_ref or understanding.id,
        "estimated_input_tokens": budget.estimated_input_tokens,
        "configured_input_cap": budget.configured_input_cap,
        "evidence_included": list(budget.evidence_included),
        "evidence_compacted": list(budget.evidence_compacted),
        "evidence_dropped": list(budget.evidence_dropped),
        "raw_response": None,
        "parser_version": PLANNER_PARSER_VERSION,
        "resulting_plan_revision_id": None,
        "transformations": [],
        # DoD-002C: prove authoritative outcome obligations were planner inputs
        "outcome_contract_id": oc_id,
        "outcome_contract_hash": oc_hash,
        "outcome_contract_obligation_ids": [o.obligation_id for o in oc_obligations],
        "outcome_contract_obligations_count": len(oc_obligations),
        # DoD-002D
        "obligation_seed_ok": bool(seed and seed.ok),
        "obligation_seed_code": seed.code if seed is not None else None,
        "obligation_seed_step_count": len(seed.steps) if seed is not None else 0,
        "obligation_seed_applied": False,
    }
    budget_report = budget.to_dict()
    transformations: list[dict[str, Any]] = []

    # PD-003B: prefer deterministic ProductStructure → PlanTargets before LLM / seed.
    if plan_request is not None and outcome_contract is not None:
        structured = _try_product_structure_plan_under_provenance(
            reply="",
            provenance=provenance,
            planner_call=planner_call,
            transformations=transformations,
            budget_report=budget_report,
            detail="prefer_product_structure_plan",
            plan_request=plan_request,
            intent=intent,
            understanding_revision=understanding_revision,
            needs=needs,
            exploration_evidence=exploration_evidence,
            governance=governance,
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            assigned_role=assigned_role,
            step_budget=step_budget,
            objective_id=objective_id,
            understanding=understanding,
            plan_revision=plan_revision,
            oc_id=oc_id,
            oc_hash=oc_hash,
            outcome_contract=outcome_contract,
        )
        if structured is not None:
            return structured

    if plan_request is not None and exploration_evidence:
        existing = _try_existing_evidence_plan_under_provenance(
            reply="",
            provenance=provenance,
            planner_call=planner_call,
            transformations=transformations,
            budget_report=budget_report,
            detail=(
                "planner_timeout_bootstrap"
                if force_existing_evidence_bootstrap
                else "prefer_existing_evidence_before_llm"
            ),
            plan_request=plan_request,
            intent=intent,
            understanding_revision=understanding_revision,
            needs=needs,
            exploration_evidence=exploration_evidence,
            governance=governance,
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            assigned_role=assigned_role,
            step_budget=step_budget,
            objective_id=objective_id,
            understanding=understanding,
            plan_revision=plan_revision,
            oc_id=oc_id,
            oc_hash=oc_hash,
            seed=seed,
        )
        if existing is not None:
            return existing
        if force_existing_evidence_bootstrap or skip_llm_when_evidence_grounded:
            if exploration_evidence:
                existing = _try_existing_evidence_plan_under_provenance(
                    reply="",
                    provenance=provenance,
                    planner_call=planner_call,
                    transformations=transformations,
                    budget_report=budget_report,
                    detail="single_grounded_evidence_fallback",
                    plan_request=plan_request,
                    intent=intent,
                    understanding_revision=understanding_revision,
                    needs=needs,
                    exploration_evidence=exploration_evidence[:1],
                    governance=governance,
                    tenant_id=tenant_id,
                    cycle_id=cycle_id,
                    assigned_role=assigned_role,
                    step_budget=step_budget,
                    objective_id=objective_id,
                    understanding=understanding,
                    plan_revision=plan_revision,
                    oc_id=oc_id,
                    oc_hash=oc_hash,
                    seed=seed,
                )
                if existing is not None:
                    return existing
            return _proposal(
                contract=None,
                reply="",
                reason="EXISTING_EVIDENCE_BOOTSTRAP_UNAVAILABLE",
                provenance=provenance,
                planner_call=planner_call,
                transformations=transformations,
                budget_report=budget_report,
            )

    gpr = GovernedProviderRouter().resolve(role="planner", model_ref=model_ref)
    if gpr.disposition != "ALLOW":
        # Prefer deterministic seed over route denial when obligations exist (002D).
        # Under live provenance Intent, seed cannot ACCEPT without PlanTargets.
        if seed is not None and seed.ok:
            if plan_request is not None:
                return _refuse_seed_under_provenance(
                    reply="",
                    provenance=provenance,
                    planner_call=planner_call,
                    transformations=transformations,
                    budget_report=budget_report,
                    detail=(
                        f"route denied ({','.join(gpr.reason_codes)}); "
                        "emit PlanTargets instead of obligation seed"
                    ),
                    plan_request=plan_request,
                    intent=intent,
                    understanding_revision=understanding_revision,
                    needs=needs,
                    exploration_evidence=exploration_evidence,
                    governance=governance,
                    tenant_id=tenant_id,
                    cycle_id=cycle_id,
                    assigned_role=assigned_role,
                    step_budget=step_budget,
                    objective_id=objective_id,
                    understanding=understanding,
                    plan_revision=plan_revision,
                    oc_id=oc_id,
                    oc_hash=oc_hash,
                    outcome_contract=outcome_contract,
                    seed=seed,
                )
            transformations.append(
                _transform(
                    kind="SYSTEM_APPLIED",
                    field="steps",
                    op="obligation_seed_after_route_denied",
                    detail=",".join(gpr.reason_codes),
                )
            )
            return _build_seed_plan_proposal(
                seed=seed,
                tenant_id=tenant_id,
                cycle_id=cycle_id,
                assigned_role=assigned_role,
                step_budget=step_budget,
                objective_id=objective_id,
                understanding=understanding,
                plan_revision=plan_revision,
                reply="",
                reason=None,
                provenance=provenance,
                planner_call=planner_call,
                transformations=transformations,
                budget_report=budget_report,
                oc_id=oc_id,
                oc_hash=oc_hash,
                intent=intent,
                understanding_revision=understanding_revision,
                governance=governance,
            )
        planner_call["raw_response"] = {"_error": f"GOVERNED_ROUTE_DENIED:{','.join(gpr.reason_codes)}"}
        return _proposal(
            contract=None,
            reply="",
            reason=f"GOVERNED_ROUTE_DENIED:{','.join(gpr.reason_codes)}",
            provenance=provenance,
            planner_call=planner_call,
            transformations=transformations,
            budget_report=budget_report,
        )

    raw_text = ""
    parsed: dict[str, Any] | None = None
    model_exc: Exception | None = None
    prompt_body = system
    for attempt in range(PLANNER_MODEL_MAX_ATTEMPTS):
        try:
            from backend.aix_core.framework.integration.hyeve.llm_ingress import (
                governed_chat_completion,
                hyeve_capabilities_enabled,
            )

            if hyeve_capabilities_enabled():
                result = await governed_chat_completion(
                    role="planner",
                    messages=[ChatMessage(role="system", content=prompt_body)],
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    authorization_header=authorization_header,
                    model_ref=model_ref,
                    response_format={"type": "json_object"},
                    timeout_seconds=timeout_seconds,
                    use_hyeve_adapter=True,
                )
            else:
                result = await chat_completion(
                    messages=[ChatMessage(role="system", content=prompt_body)],
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    authorization_header=authorization_header,
                    response_format={"type": "json_object"},
                    model=model_ref,
                    timeout_seconds=timeout_seconds,
                    metadata={
                        "source": "aix.step_contract_planner",
                        "aix_role": "step_contract_planner",
                        "planner_request_shape": "aix.step_contract.planner.v1",
                    },
                )
            # Immutable raw capture happens before parser transformation.
            raw_text = str(result.output_text or "")
            planner_call["raw_response"] = _raw_structured_response(raw_text)
            planner_call["model"] = result.model or model_ref
            planner_call["provider"] = result.provider
            planner_call["resolved_provider"] = result.resolved_provider
            planner_call["usage"] = dict(result.usage or {})
            provenance = capture_prompt_provenance(
                prompt_body=prompt_body,
                model_ref=result.model or model_ref,
                provider=result.provider,
                resolved_provider=result.resolved_provider,
                usage=dict(result.usage or {}),
            )
            loaded = json.loads(raw_text)
            if not isinstance(loaded, dict):
                raise ValueError("planner reply JSON is not an object")
            parsed = loaded
            planner_call["model_attempts"] = attempt + 1
            model_exc = None
            break
        except (HyeveIntegrationChatError, json.JSONDecodeError, TypeError, ValueError) as exc:
            model_exc = exc
            planner_call["model_attempts"] = attempt + 1
            if planner_call.get("raw_response") is None:
                planner_call["raw_response"] = (
                    _raw_structured_response(raw_text) if raw_text else {"_error": str(exc)[:800]}
                )
            if attempt + 1 < PLANNER_MODEL_MAX_ATTEMPTS:
                transformations.append(
                    _transform(
                        kind="SYSTEM_APPLIED",
                        field="raw_response",
                        op="planner_model_retry",
                        detail=str(exc)[:240],
                    )
                )
                prompt_body = system + _PLANNER_REPAIR_SUFFIX
                continue
            transformations.append(
                _transform(
                    kind="SYSTEM_REJECTED",
                    field="raw_response",
                    op="planner_model_error",
                    detail=str(exc)[:400],
                )
            )
            if plan_request is not None and understanding is not None:
                existing = _try_existing_evidence_plan_under_provenance(
                    reply="",
                    provenance=provenance,
                    planner_call=planner_call,
                    transformations=transformations,
                    budget_report=budget_report,
                    detail=f"model error ({str(exc)[:180]})",
                    plan_request=plan_request,
                    intent=intent,
                    understanding_revision=understanding_revision,
                    needs=needs,
                    exploration_evidence=exploration_evidence,
                    governance=governance,
                    tenant_id=tenant_id,
                    cycle_id=cycle_id,
                    assigned_role=assigned_role,
                    step_budget=step_budget,
                    objective_id=objective_id,
                    understanding=understanding,
                    plan_revision=plan_revision,
                    oc_id=oc_id,
                    oc_hash=oc_hash,
                    seed=seed,
                )
                if existing is not None:
                    return existing
            if seed is not None and seed.ok:
                if plan_request is not None:
                    return _refuse_seed_under_provenance(
                        reply="",
                        provenance=provenance,
                        planner_call=planner_call,
                        transformations=transformations,
                        budget_report=budget_report,
                        detail=(
                            f"model error ({str(exc)[:180]}); "
                            "emit PlanTargets instead of obligation seed"
                        ),
                        plan_request=plan_request,
                        intent=intent,
                        understanding_revision=understanding_revision,
                        needs=needs,
                        exploration_evidence=exploration_evidence,
                        governance=governance,
                        tenant_id=tenant_id,
                        cycle_id=cycle_id,
                        assigned_role=assigned_role,
                        step_budget=step_budget,
                        objective_id=objective_id,
                        understanding=understanding,
                        plan_revision=plan_revision,
                        oc_id=oc_id,
                        oc_hash=oc_hash,
                        outcome_contract=outcome_contract,
                        seed=seed,
                    )
                transformations.append(
                    _transform(
                        kind="SYSTEM_APPLIED",
                        field="steps",
                        op="obligation_seed_after_model_error",
                        detail=str(exc)[:240],
                    )
                )
                return _build_seed_plan_proposal(
                    seed=seed,
                    tenant_id=tenant_id,
                    cycle_id=cycle_id,
                    assigned_role=assigned_role,
                    step_budget=step_budget,
                    objective_id=objective_id,
                    understanding=understanding,
                    plan_revision=plan_revision,
                    reply="",
                    reason=None,
                    provenance=provenance,
                    planner_call=planner_call,
                    transformations=transformations,
                    budget_report=budget_report,
                    oc_id=oc_id,
                    oc_hash=oc_hash,
                    intent=intent,
                    understanding_revision=understanding_revision,
                    governance=governance,
                )
            return _proposal(
                contract=None,
                reply="",
                reason=f"PLANNER_MODEL_ERROR:{exc}",
                provenance=provenance,
                planner_call=planner_call,
                transformations=transformations,
                budget_report=budget_report,
            )
    if parsed is None:
        return _proposal(
            contract=None,
            reply="",
            reason=f"PLANNER_MODEL_ERROR:{model_exc or 'empty planner response'}",
            provenance=provenance,
            planner_call=planner_call,
            transformations=transformations,
            budget_report=budget_report,
        )

    reply = str(parsed.get("reply") or "").strip()
    evidence_requests = parse_evidence_requests(parsed.get("requests"))
    if bool(parsed.get("needs_more_evidence")):
        transformations.append(
            _transform(kind="MODEL_GENERATED", field="needs_more_evidence", op="nme_refusal")
        )
        planner_call["transformations"] = transformations
        return _proposal(
            contract=None,
            reply=reply,
            reason="NEEDS_MORE_EVIDENCE",
            provenance=provenance,
            evidence_requests=evidence_requests,
            planner_call=planner_call,
            transformations=transformations,
            budget_report=budget_report,
        )
    known_obligation_ids = {o.id for o in obligations} | {o.obligation_id for o in oc_obligations}
    parsed_targets = ()
    raw_steps = parsed.get("steps")
    if plan_request is not None:
        parsed_targets, target_parse = parse_plan_targets(parsed.get("targets"))
        if (
            not target_parse.ok
            and isinstance(raw_steps, list)
            and raw_steps
        ):
            synthesized = _compat_plan_targets_from_steps(
                raw_steps,
                files_read=understanding.files_read,
                outcome_claim_ids=tuple(
                    str(getattr(claim, "id", "") or "")
                    for claim in (
                        getattr(understanding_revision, "claims", ()) or ()
                    )
                    if getattr(claim, "kind", None) == "OUTCOME"
                    and getattr(claim, "status", None) == "ACTIVE"
                    and str(getattr(claim, "id", "") or "").strip()
                ),
                exploration_evidence=exploration_evidence,
            )
            if synthesized:
                parsed_targets = synthesized
                target_parse = RuleResult.pass_("P8")
                transformations.append(
                    _transform(
                        kind="SYSTEM_APPLIED",
                        field="targets",
                        op="compat_targets_from_step_scope",
                        detail=f"count={len(synthesized)}",
                    )
                )
        elif (
            not target_parse.ok
            and (not isinstance(raw_steps, list) or not raw_steps)
            and (parsed.get("target_scope") or parsed.get("purpose"))
        ):
            legacy_step = {
                "purpose": parsed.get("purpose"),
                "target_scope": parsed.get("target_scope"),
            }
            synthesized = _compat_plan_targets_from_steps(
                [legacy_step],
                files_read=understanding.files_read,
                outcome_claim_ids=tuple(
                    str(getattr(claim, "id", "") or "")
                    for claim in (
                        getattr(understanding_revision, "claims", ()) or ()
                    )
                    if getattr(claim, "kind", None) == "OUTCOME"
                    and getattr(claim, "status", None) == "ACTIVE"
                    and str(getattr(claim, "id", "") or "").strip()
                ),
                exploration_evidence=exploration_evidence,
            )
            if synthesized:
                parsed_targets = synthesized
                target_parse = RuleResult.pass_("P8")
                transformations.append(
                    _transform(
                        kind="SYSTEM_APPLIED",
                        field="targets",
                        op="compat_targets_from_legacy_one_step",
                        detail=f"count={len(synthesized)}",
                    )
                )
        if not target_parse.ok:
            # When steps name target_scope paths but none are grounded in evidence,
            # report TARGET_SCOPE_NOT_GROUNDED (PA-002) rather than a generic parse
            # failure — synthesis skipped ungrounded paths and left targets empty.
            ungrounded_from_steps: list[str] = []
            if isinstance(raw_steps, list):
                grounded_paths = {
                    str(p).replace("\\", "/")
                    for p in (understanding.files_read or ())
                    if str(p).strip()
                }
                for raw in raw_steps:
                    if not isinstance(raw, dict):
                        continue
                    for item in raw.get("target_scope") or ():
                        path = str(item or "").strip().replace("\\", "/")
                        if path and path not in grounded_paths:
                            ungrounded_from_steps.append(path)
            if ungrounded_from_steps:
                transformations.append(
                    _transform(
                        kind="SYSTEM_REJECTED",
                        field="target_scope",
                        op="ungrounded_paths",
                        paths=tuple(dict.fromkeys(ungrounded_from_steps)),
                    )
                )
                return _proposal(
                    contract=None,
                    reply=reply,
                    reason=(
                        "TARGET_SCOPE_NOT_GROUNDED_IN_EVIDENCE:"
                        f"{list(dict.fromkeys(ungrounded_from_steps))}"
                    ),
                    provenance=provenance,
                    planner_call=planner_call,
                    transformations=transformations,
                    budget_report=budget_report,
                )
            transformations.append(
                _transform(kind="SYSTEM_REJECTED", field="targets", op=target_parse.rule_id)
            )
            return _proposal(
                contract=None,
                reply=reply,
                reason=f"INVALID_PLAN_TARGETS:{target_parse.rule_id}:{target_parse.detail}",
                provenance=provenance,
                planner_call=planner_call,
                transformations=transformations,
                budget_report=budget_report,
                provenance_decision=target_parse.decision,
            )
    plan_target_paths = tuple(t.path for t in parsed_targets)
    used_legacy_one_step = not isinstance(raw_steps, list) or not raw_steps
    if used_legacy_one_step:
        # DoD-002D: never synthesize a legacy mega-step when we have a coherent seed
        if seed is not None and seed.ok:
            if plan_request is not None:
                return _refuse_seed_under_provenance(
                    reply=reply,
                    provenance=provenance,
                    planner_call=planner_call,
                    transformations=transformations,
                    budget_report=budget_report,
                    detail="missing steps[]; emit PlanTargets + steps (seed cannot ACCEPT under provenance)",
                    plan_request=plan_request,
                    intent=intent,
                    understanding_revision=understanding_revision,
                    needs=needs,
                    exploration_evidence=exploration_evidence,
                    governance=governance,
                    tenant_id=tenant_id,
                    cycle_id=cycle_id,
                    assigned_role=assigned_role,
                    step_budget=step_budget,
                    objective_id=objective_id,
                    understanding=understanding,
                    plan_revision=plan_revision,
                    oc_id=oc_id,
                    oc_hash=oc_hash,
                    outcome_contract=outcome_contract,
                    seed=seed,
                )
            transformations.append(
                _transform(
                    kind="SYSTEM_APPLIED",
                    field="steps",
                    op="obligation_seed_instead_of_legacy_one_step",
                )
            )
            return _build_seed_plan_proposal(
                seed=seed,
                tenant_id=tenant_id,
                cycle_id=cycle_id,
                assigned_role=assigned_role,
                step_budget=step_budget,
                objective_id=objective_id,
                understanding=understanding,
                plan_revision=plan_revision,
                reply=reply,
                reason=None,
                provenance=provenance,
                planner_call=planner_call,
                transformations=transformations,
                budget_report=budget_report,
                oc_id=oc_id,
                oc_hash=oc_hash,
                intent=intent,
                understanding_revision=understanding_revision,
                governance=governance,
            )
        if plan_request is not None:
            if not (parsed.get("target_scope") or parsed.get("purpose")):
                transformations.append(
                    _transform(kind="SYSTEM_REJECTED", field="steps", op="legacy_one_step_forbidden")
                )
                return _proposal(
                    contract=None,
                    reply=reply,
                    reason="INVALID_STEP_CONTRACT:steps array required; parser will not synthesize a plan",
                    provenance=provenance,
                    planner_call=planner_call,
                    transformations=transformations,
                    budget_report=budget_report,
                )
            transformations.append(
                _transform(
                    kind="SYSTEM_APPLIED",
                    field="steps",
                    op="legacy_one_step_normalized",
                )
            )
        raw_steps = [
            {
                "purpose": parsed.get("purpose"),
                "target_scope": parsed.get("target_scope"),
                "acceptance_criteria": parsed.get("acceptance_criteria"),
                "validation_commands": parsed.get("validation_commands"),
                "prohibited_actions": parsed.get("prohibited_actions"),
                "obligation_refs": parsed.get("obligation_refs"),
                "required_effects": parsed.get("required_effects"),
                "actions": parsed.get("actions"),
                "risk_tier": parsed.get("risk_tier"),
                "depends_on": [],
            }
        ]

    contracts: list[StepContractV1] = []
    dep_indexes_by_step: list[list[int]] = []
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            transformations.append(
                _transform(kind="SYSTEM_REJECTED", field="steps", op="step_not_object", step_index=index)
            )
            return _proposal(
                contract=None,
                reply=reply,
                reason="INVALID_STEP_CONTRACT:step is not an object",
                provenance=provenance,
                planner_call=planner_call,
                transformations=transformations,
                budget_report=budget_report,
            )
        transformations.append(
            _transform(
                kind="MODEL_GENERATED",
                field="steps",
                op="raw_step",
                step_index=index,
                purpose=str(raw.get("purpose") or "")[:240],
            )
        )
        proposed_scope = [str(p) for p in (raw.get("target_scope") or []) if str(p).strip()]
        ungrounded = [
            p for p in proposed_scope
            if not _target_scope_grounded(
                p,
                files_read=understanding.files_read,
                plan_target_paths=plan_target_paths,
                require_plan_targets=plan_request is not None,
            )
        ]
        if ungrounded:
            if seed is not None and seed.ok:
                if plan_request is not None:
                    return _refuse_seed_under_provenance(
                        reply=reply,
                        provenance=provenance,
                        planner_call=planner_call,
                        transformations=transformations,
                        budget_report=budget_report,
                        detail=(
                            "ungrounded target_scope "
                            + ",".join(ungrounded[:8])
                            + "; emit PlanTargets covering step scope"
                        ),
                        plan_request=plan_request,
                        intent=intent,
                        understanding_revision=understanding_revision,
                        needs=needs,
                        exploration_evidence=exploration_evidence,
                        governance=governance,
                        tenant_id=tenant_id,
                        cycle_id=cycle_id,
                        assigned_role=assigned_role,
                        step_budget=step_budget,
                        objective_id=objective_id,
                        understanding=understanding,
                        plan_revision=plan_revision,
                        oc_id=oc_id,
                        oc_hash=oc_hash,
                        outcome_contract=outcome_contract,
                        seed=seed,
                    )
                transformations.append(
                    _transform(
                        kind="SYSTEM_APPLIED",
                        field="steps",
                        op="obligation_seed_after_ungrounded_scope",
                        step_index=index,
                        paths=ungrounded,
                    )
                )
                return _build_seed_plan_proposal(
                    seed=seed,
                    tenant_id=tenant_id,
                    cycle_id=cycle_id,
                    assigned_role=assigned_role,
                    step_budget=step_budget,
                    objective_id=objective_id,
                    understanding=understanding,
                    plan_revision=plan_revision,
                    reply=reply,
                    reason=None,
                    provenance=provenance,
                    planner_call=planner_call,
                    transformations=transformations,
                    budget_report=budget_report,
                    oc_id=oc_id,
                    oc_hash=oc_hash,
                    intent=intent,
                    understanding_revision=understanding_revision,
                    governance=governance,
                )
            transformations.append(
                _transform(
                    kind="SYSTEM_REJECTED",
                    field="target_scope",
                    op="ungrounded_paths",
                    step_index=index,
                    paths=ungrounded,
                )
            )
            return _proposal(
                contract=None,
                reply=reply,
                reason=f"TARGET_SCOPE_NOT_GROUNDED_IN_EVIDENCE:{ungrounded}",
                provenance=provenance,
                planner_call=planner_call,
                transformations=transformations,
                budget_report=budget_report,
            )
        proposed_scope = list(dict.fromkeys(proposed_scope))
        proposed_obligation_refs = [str(r) for r in (raw.get("obligation_refs") or []) if str(r).strip()]
        dropped_refs = [r for r in proposed_obligation_refs if r not in known_obligation_ids]
        if dropped_refs:
            transformations.append(
                _transform(
                    kind="SYSTEM_REMOVED",
                    field="obligation_refs",
                    op="dropped_unknown_obligation_refs",
                    step_index=index,
                    refs=dropped_refs,
                )
            )
        grounded_obligation_refs = tuple(
            dict.fromkeys(r for r in proposed_obligation_refs if r in known_obligation_ids)
        )
        try:
            dep_indexes = [int(d) for d in (raw.get("depends_on") or []) if str(d).strip() != ""]
        except (TypeError, ValueError) as exc:
            transformations.append(
                _transform(kind="SYSTEM_REJECTED", field="depends_on", op="invalid_dependency", step_index=index)
            )
            return _proposal(
                contract=None,
                reply=reply,
                reason=f"INVALID_STEP_CONTRACT:{exc}",
                provenance=provenance,
                planner_call=planner_call,
                transformations=transformations,
                budget_report=budget_report,
            )
        try:
            step_actions = _bind_action_paths_to_scope(
                _parse_actions(raw.get("actions")),
                proposed_scope,
            )
        except ValueError as exc:
            transformations.append(
                _transform(kind="SYSTEM_REJECTED", field="actions", op="invalid_actions", step_index=index)
            )
            return _proposal(
                contract=None,
                reply=reply,
                reason=f"INVALID_STEP_CONTRACT:{exc}",
                provenance=provenance,
                planner_call=planner_call,
                transformations=transformations,
                budget_report=budget_report,
            )
        try:
            schema_ref = str(raw.get("expected_output_schema_ref") or "aix.step_contract.v1.generic_edit")
            contract = StepContractV1(
                cycle_id=cycle_id,
                tenant_id=tenant_id,
                step_index=index,
                purpose=str(raw.get("purpose") or "").strip(),
                target_scope=tuple(proposed_scope),
                expected_output_schema_ref=schema_ref,
                acceptance_criteria=tuple(str(c) for c in (raw.get("acceptance_criteria") or []) if str(c).strip()),
                validation_commands=tuple(str(c) for c in (raw.get("validation_commands") or []) if str(c).strip())
                or ("python -m pytest -q",),
                assigned_role=assigned_role,  # type: ignore[arg-type]
                risk_tier=RiskTier(str(raw.get("risk_tier") or "LOW").upper()),
                step_budget=step_budget,
                prohibited_actions=tuple(str(c) for c in (raw.get("prohibited_actions") or []) if str(c).strip())
                or StepContractV1.model_fields["prohibited_actions"].default,
                obligation_refs=grounded_obligation_refs,
                actions=step_actions,
                expected_checks=tuple(str(c) for c in (raw.get("expected_checks") or []) if str(c).strip()),
                required_evidence=tuple(str(c) for c in (raw.get("required_evidence") or []) if str(c).strip()),
                estimated_complexity=str(raw.get("estimated_complexity") or "").strip() or None,
                context_requirements=tuple(str(c) for c in (raw.get("context_requirements") or []) if str(c).strip()),
                required_effects=parse_required_effects(raw.get("required_effects")),
                status="PENDING",
            )
        except (ValidationError, ValueError, KeyError) as exc:
            transformations.append(
                _transform(kind="SYSTEM_REJECTED", field="step", op="schema_validation", step_index=index)
            )
            return _proposal(
                contract=None,
                reply=reply,
                reason=f"INVALID_STEP_CONTRACT:{exc}",
                provenance=provenance,
                planner_call=planner_call,
                transformations=transformations,
                budget_report=budget_report,
            )
        contracts.append(contract)
        dep_indexes_by_step.append(dep_indexes)

    id_by_index = {i: c.id for i, c in enumerate(contracts)}
    resolved: list[StepContractV1] = []
    for index, (contract, dep_indexes) in enumerate(zip(contracts, dep_indexes_by_step)):
        depends_on = tuple(id_by_index[i] for i in dep_indexes if i in id_by_index and i != index)
        if depends_on:
            data = contract.model_dump()
            data["depends_on"] = depends_on
            try:
                contract = StepContractV1.model_validate(data)
            except (ValidationError, ValueError) as exc:
                transformations.append(
                    _transform(kind="SYSTEM_REJECTED", field="depends_on", op="invalid_normalized_dependency", step_index=index)
                )
                return _proposal(
                    contract=None,
                    reply=reply,
                    reason=f"INVALID_STEP_CONTRACT:{exc}",
                    provenance=provenance,
                    planner_call=planner_call,
                    transformations=transformations,
                    budget_report=budget_report,
                )
        resolved.append(contract)

    # DoD-002D: prefer deterministic coherent seed when LLM collapses / omits UI ops.
    # Under live provenance, PlanTargets are sole accept authority — never replace an
    # LLM proposal with a seed that has no PlanTargets (that becomes LEGACY_PLAN_FORBIDDEN).
    if (
        plan_request is None
        and seed is not None
        and seed.ok
        and should_prefer_obligation_seed(
            step_count=len(resolved),
            step_obligation_refs=[c.obligation_refs for c in resolved],
            obligations=oc_obligations,
            used_legacy_one_step=used_legacy_one_step,
        )
    ):
        transformations.append(
            _transform(
                kind="SYSTEM_APPLIED",
                field="steps",
                op="obligation_seed_preferred",
                detail=(
                    f"legacy={used_legacy_one_step};llm_steps={len(resolved)};"
                    f"seed_steps={len(seed.steps)}"
                ),
            )
        )
        return _build_seed_plan_proposal(
            seed=seed,
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            assigned_role=assigned_role,
            step_budget=step_budget,
            objective_id=objective_id,
            understanding=understanding,
            plan_revision=plan_revision,
            reply=reply,
            reason=None,
            provenance=provenance,
            planner_call=planner_call,
            transformations=transformations,
            budget_report=budget_report,
            oc_id=oc_id,
            oc_hash=oc_hash,
            intent=intent,
            understanding_revision=understanding_revision,
            governance=governance,
        )
    if (
        plan_request is not None
        and seed is not None
        and seed.ok
        and should_prefer_obligation_seed(
            step_count=len(resolved),
            step_obligation_refs=[c.obligation_refs for c in resolved],
            obligations=oc_obligations,
            used_legacy_one_step=used_legacy_one_step,
        )
    ):
        transformations.append(
            _transform(
                kind="SYSTEM_REJECTED",
                field="steps",
                op="obligation_seed_skipped_under_provenance",
                detail=(
                    f"legacy={used_legacy_one_step};llm_steps={len(resolved)};"
                    f"seed_steps={len(seed.steps)};keeping_llm_steps_for_PlanTargets"
                ),
            )
        )

    coverage_ids = tuple(dict.fromkeys(ref for c in resolved for ref in c.obligation_refs))
    provenance_plan = None
    provenance_decision = None
    if plan_request is not None:
        if intent is None or understanding_revision is None or governance is None:
            return _proposal(
                contract=None,
                reply=reply,
                reason="INVALID_PLAN_REQUEST:missing provenance refs",
                provenance=provenance,
                planner_call=planner_call,
                transformations=transformations,
                budget_report=budget_report,
            )
        if any(str(t.get("kind") or "") == "SYSTEM_ENRICHED" for t in transformations):
            transformations.append(_transform(kind="SYSTEM_REJECTED", field="plan", op="P10"))
            return _proposal(
                contract=None,
                reply=reply,
                reason="P10:SYSTEM_ENRICHED transformations are forbidden",
                provenance=provenance,
                planner_call=planner_call,
                transformations=transformations,
                budget_report=budget_report,
                provenance_decision="REJECT",
            )
        construction = construct_plan_revision(
            request=plan_request,
            intent=intent,
            understanding=understanding_revision,
            needs=needs,
            evidence=exploration_evidence,
            governance=governance,
            targets=parsed_targets,
            step_scopes=tuple(c.target_scope for c in resolved),
            transformations=tuple(transformations),
            revision=plan_revision,
        )
        provenance_plan = construction.plan
        provenance_decision = construction.validation.decision
        if construction.validation.ok and plan_revision >= 2:
            e3 = validate_replan_revision(plan=construction.plan)
            if not e3.ok:
                transformations.append(
                    _transform(kind="SYSTEM_REJECTED", field="plan", op=e3.rule_id, detail=e3.detail[:240])
                )
                return _proposal(
                    contract=None,
                    reply=reply,
                    reason=f"{e3.rule_id}:{e3.detail}",
                    provenance=provenance,
                    planner_call=planner_call,
                    transformations=transformations,
                    budget_report=budget_report,
                    provenance_plan=construction.plan,
                    provenance_decision=e3.decision,
                )
        if not construction.validation.ok:
            transformations.append(
                _transform(
                    kind="SYSTEM_REJECTED",
                    field="plan",
                    op=construction.validation.rule_id,
                    detail=construction.validation.detail[:240],

                    plan_request=plan_request,
                    intent=intent,
                    understanding_revision=understanding_revision,
                    needs=needs,
                    exploration_evidence=exploration_evidence,
                    governance=governance,
                    tenant_id=tenant_id,
                    cycle_id=cycle_id,
                    assigned_role=assigned_role,
                    step_budget=step_budget,
                    objective_id=objective_id,
                    understanding=understanding,
                    plan_revision=plan_revision,
                    oc_id=oc_id,
                    oc_hash=oc_hash,
                    outcome_contract=outcome_contract,
                )
            )
            return _proposal(
                contract=None,
                reply=reply,
                reason=(
                    f"{construction.validation.rule_id}:{construction.validation.detail}"
                ),
                provenance=provenance,
                planner_call=planner_call,
                transformations=transformations,
                budget_report=budget_report,
                provenance_plan=provenance_plan,
                provenance_decision=provenance_decision,
            )
    try:
        plan = PlanRevisionV1(
            objective_id=objective_id or cycle_id,
            cycle_id=cycle_id,
            revision=plan_revision,
            created_from_evidence=understanding.id,
            steps=tuple(resolved),
            obligation_coverage=coverage_ids,
            planner_reasoning_ref=reply or None,
            status="DRAFT",
            intent_id=intent.id if intent is not None else None,
            understanding_revision_id=understanding_revision.id if understanding_revision is not None else None,
            evidence_ids=tuple(t.evidence_id for t in parsed_targets if t.evidence_id),
            governance_revision_id=governance.id if governance is not None else None,
        )
    except (ValidationError, ValueError) as exc:
        transformations.append(
            _transform(kind="SYSTEM_REJECTED", field="plan", op="plan_revision_validation")
        )
        return _proposal(
            contract=None,
            reply=reply,
            reason=f"INVALID_STEP_CONTRACT:{exc}",
            provenance=provenance,
            planner_call=planner_call,
            transformations=transformations,
            budget_report=budget_report,
        )

    planner_call["resulting_plan_revision_id"] = plan.plan_id
    planner_call["transformations"] = transformations
    return _proposal(
        contract=resolved[0],
        reply=reply,
        reason=None,
        plan=plan,
        provenance=provenance,
        used_legacy_one_step=used_legacy_one_step,
        planner_call=planner_call,
        transformations=transformations,
        budget_report=budget_report,
        provenance_plan=provenance_plan,
        provenance_decision=provenance_decision,
    )


__all__ = [
    "StepContractProposalV1",
    "parse_evidence_requests",
    "propose_step_contract",
    "materialize_obligation_seed_steps",
    "_is_cycle_workspace_proposal_path",
]
