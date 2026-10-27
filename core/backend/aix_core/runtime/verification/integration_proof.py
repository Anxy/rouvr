"""Contract Integration Proof v1 — static composition only.

Result vocabulary:
  INTEGRATION_PROVEN_STATIC | INTEGRATION_FAILED | INTEGRATION_SKIPPED_NO_CONTRACT

Never claims RUNTIME_VERIFIED. No LLM. No I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.aix_core.runtime.objective.contracts import StepContractV1
from backend.aix_core.runtime.objective.outcome_contract import (
    IntegrationProofResult,
    OutcomeContractV1,
    compute_outcome_contract_hash,
    validate_outcome_contract_structure,
)
from backend.aix_core.runtime.verification.contract_coverage import evaluate_contract_coverage

VERIFIER_VERSION = "integration_proof.static.v1"


@dataclass(frozen=True)
class MappingStatus:
    from_ref: str
    to_ref: str
    status: str


@dataclass(frozen=True)
class IntegrationMismatch:
    code: str
    detail: str
    from_ref: str = ""
    to_ref: str = ""


@dataclass(frozen=True)
class IntegrationProofV1:
    contract_id: str
    contract_version: str
    contract_hash: str
    ui_api_mapping: tuple[MappingStatus, ...]
    api_schema_mapping: tuple[MappingStatus, ...]
    uncovered_facets: tuple[str, ...]
    mismatches: tuple[IntegrationMismatch, ...]
    evidence_refs: tuple[str, ...]
    verifier_version: str
    result: IntegrationProofResult

    @property
    def ok(self) -> bool:
        return self.result == "INTEGRATION_PROVEN_STATIC"


def evaluate_integration_proof(
    *,
    outcome_contract: OutcomeContractV1 | None,
    steps: tuple[StepContractV1, ...] | list[StepContractV1] = (),
    evidence_refs: tuple[str, ...] = (),
    require_outcome_contract: bool = True,
) -> IntegrationProofV1:
    """Static UI→API→DB composition. Fail closed on missing/ambiguous mappings."""
    if outcome_contract is None:
        result: IntegrationProofResult = (
            "INTEGRATION_FAILED" if require_outcome_contract else "INTEGRATION_SKIPPED_NO_CONTRACT"
        )
        return IntegrationProofV1(
            contract_id="",
            contract_version="v1",
            contract_hash="",
            ui_api_mapping=(),
            api_schema_mapping=(),
            uncovered_facets=(),
            mismatches=(
                ()
                if result == "INTEGRATION_SKIPPED_NO_CONTRACT"
                else (
                    IntegrationMismatch(
                        code="OUTCOME_CONTRACT_REQUIRED",
                        detail="IntegrationProof requires a sealed OutcomeContractV1",
                    ),
                )
            ),
            evidence_refs=evidence_refs,
            verifier_version=VERIFIER_VERSION,
            result=result,
        )

    expected_hash = compute_outcome_contract_hash(outcome_contract)
    mismatches: list[IntegrationMismatch] = []
    if outcome_contract.contract_hash != expected_hash:
        mismatches.append(
            IntegrationMismatch(
                code="CONTRACT_HASH_MISMATCH",
                detail=f"expected {expected_hash}, got {outcome_contract.contract_hash}",
            )
        )

    struct_ok, struct_code, struct_details = validate_outcome_contract_structure(outcome_contract)
    if not struct_ok:
        for detail in struct_details:
            mismatches.append(IntegrationMismatch(code=struct_code, detail=detail))

    coverage = evaluate_contract_coverage(
        outcome_contract=outcome_contract,
        steps=steps,
        require_outcome_contract=True,
    )
    if not coverage.ok:
        mismatches.append(
            IntegrationMismatch(code=coverage.code, detail=coverage.detail)
        )

    facets = outcome_contract.facets
    api_ids = {op.id: op for op in facets.API.required_operations}
    ui_ops = set(facets.UI.required_operations)
    db_entities = set(facets.DB.entities)

    ui_api: list[MappingStatus] = []
    api_schema: list[MappingStatus] = []

    # Index declared INTEGRATION mappings
    ui_to_api: dict[str, list[str]] = {}
    api_to_db: dict[str, list[str]] = {}
    for mapping in facets.INTEGRATION.mappings:
        if mapping.from_facet == "UI" and mapping.to_facet == "API":
            ui_to_api.setdefault(mapping.from_id, []).append(mapping.to_id)
        elif mapping.from_facet == "API" and mapping.to_facet == "DB":
            api_to_db.setdefault(mapping.from_id, []).append(mapping.to_id)

    if facets.UI.requirement == "MANDATORY":
        for ui_op in facets.UI.required_operations:
            targets = ui_to_api.get(ui_op, [])
            if not targets:
                mismatches.append(
                    IntegrationMismatch(
                        code="UI_OP_NO_API",
                        detail=f"UI operation {ui_op!r} has no API mapping",
                        from_ref=f"UI.{ui_op}",
                    )
                )
                ui_api.append(MappingStatus(from_ref=ui_op, to_ref="", status="MISSING"))
                continue
            if len(targets) > 1:
                mismatches.append(
                    IntegrationMismatch(
                        code="MAPPING_AMBIGUOUS",
                        detail=f"UI operation {ui_op!r} maps to multiple API ops {targets}",
                        from_ref=f"UI.{ui_op}",
                    )
                )
                ui_api.append(MappingStatus(from_ref=ui_op, to_ref=",".join(targets), status="AMBIGUOUS"))
                continue
            api_op = targets[0]
            if api_op not in api_ids:
                mismatches.append(
                    IntegrationMismatch(
                        code="API_OP_MISSING",
                        detail=f"mapped API op {api_op!r} not on API facet",
                        from_ref=f"UI.{ui_op}",
                        to_ref=f"API.{api_op}",
                    )
                )
                ui_api.append(MappingStatus(from_ref=ui_op, to_ref=api_op, status="API_MISSING"))
            else:
                ui_api.append(MappingStatus(from_ref=ui_op, to_ref=api_op, status="OK"))

    if facets.API.requirement == "MANDATORY":
        for api_op in facets.API.required_operations:
            targets = api_to_db.get(api_op.id, [])
            # Allow explicit no-persist via empty mapping only if INTEGRATION declares none
            # for this op AND acceptance allows it — v1 fail closed: require DB map.
            if not targets:
                mismatches.append(
                    IntegrationMismatch(
                        code="API_OP_NO_SCHEMA",
                        detail=f"API operation {api_op.id!r} has no DB entity mapping",
                        from_ref=f"API.{api_op.id}",
                    )
                )
                api_schema.append(MappingStatus(from_ref=api_op.id, to_ref="", status="MISSING"))
                continue
            if len(targets) > 1:
                mismatches.append(
                    IntegrationMismatch(
                        code="MAPPING_AMBIGUOUS",
                        detail=f"API operation {api_op.id!r} maps to multiple entities {targets}",
                        from_ref=f"API.{api_op.id}",
                    )
                )
                api_schema.append(
                    MappingStatus(from_ref=api_op.id, to_ref=",".join(targets), status="AMBIGUOUS")
                )
                continue
            entity = targets[0]
            if entity not in db_entities:
                mismatches.append(
                    IntegrationMismatch(
                        code="DB_ENTITY_MISSING",
                        detail=f"mapped entity {entity!r} not on DB facet",
                        from_ref=f"API.{api_op.id}",
                        to_ref=f"DB.{entity}",
                    )
                )
                api_schema.append(MappingStatus(from_ref=api_op.id, to_ref=entity, status="DB_MISSING"))
            else:
                api_schema.append(MappingStatus(from_ref=api_op.id, to_ref=entity, status="OK"))

    if facets.AUTH.requirement == "MANDATORY":
        if not facets.AUTH.actors and not facets.AUTH.permissions:
            mismatches.append(
                IntegrationMismatch(
                    code="AUTH_UNBOUND",
                    detail="AUTH facet MANDATORY but actors/permissions empty",
                )
            )

    uncovered = list(coverage.uncovered_facets)
    proof_result: IntegrationProofResult = (
        "INTEGRATION_PROVEN_STATIC" if not mismatches else "INTEGRATION_FAILED"
    )
    sealed_refs = tuple(
        dict.fromkeys(
            list(evidence_refs)
            + [f"outcome_contract:{outcome_contract.contract_id}:{outcome_contract.contract_hash}"]
            + [f"step:{s.id}" for s in steps]
        )
    )
    return IntegrationProofV1(
        contract_id=outcome_contract.contract_id,
        contract_version=outcome_contract.version,
        contract_hash=outcome_contract.contract_hash,
        ui_api_mapping=tuple(ui_api),
        api_schema_mapping=tuple(api_schema),
        uncovered_facets=tuple(uncovered),
        mismatches=tuple(mismatches),
        evidence_refs=sealed_refs,
        verifier_version=VERIFIER_VERSION,
        result=proof_result,
    )


@dataclass(frozen=True)
class ObjectiveProvenEvaluation:
    """Five-conjunct objective completion. Independent of last-step flags alone."""

    ok: bool
    result: str
    blocking_reasons: tuple[str, ...] = ()
    contract_covered: bool = False
    all_required_steps_passed: bool = False
    integration_proven: bool = False
    objective_acceptance_proven: bool = False
    evidence_complete: bool = False


def evaluate_objective_proven(
    *,
    contract_covered: bool,
    all_required_steps_passed: bool,
    integration_proven: bool,
    objective_acceptance_proven: bool,
    evidence_complete: bool,
) -> ObjectiveProvenEvaluation:
    """Finalize rule: all five conjuncts required for COMPLETED_FOR_REVIEW."""
    reasons: list[str] = []
    if not all_required_steps_passed:
        reasons.append("STEPS_NOT_ALL_PASSED")
    if not contract_covered:
        reasons.append("CONTRACT_NOT_COVERED")
    if not integration_proven:
        reasons.append("INTEGRATION_NOT_PROVEN")
    if not objective_acceptance_proven:
        reasons.append("OBJECTIVE_ACCEPTANCE_NOT_PROVEN")
    if not evidence_complete:
        reasons.append("EVIDENCE_INCOMPLETE")

    if reasons:
        # Prefer BLOCKED for incomplete proof; FAILED only when steps themselves failed
        # without repair path — callers may map STEPS_NOT_ALL_PASSED to FAILED.
        status = "FAILED" if reasons == ["STEPS_NOT_ALL_PASSED"] else "BLOCKED"
        return ObjectiveProvenEvaluation(
            ok=False,
            result=status,
            blocking_reasons=tuple(reasons),
            contract_covered=contract_covered,
            all_required_steps_passed=all_required_steps_passed,
            integration_proven=integration_proven,
            objective_acceptance_proven=objective_acceptance_proven,
            evidence_complete=evidence_complete,
        )

    return ObjectiveProvenEvaluation(
        ok=True,
        result="COMPLETED_FOR_REVIEW",
        blocking_reasons=(),
        contract_covered=True,
        all_required_steps_passed=True,
        integration_proven=True,
        objective_acceptance_proven=True,
        evidence_complete=True,
    )


__all__ = [
    "VERIFIER_VERSION",
    "MappingStatus",
    "IntegrationMismatch",
    "IntegrationProofV1",
    "evaluate_integration_proof",
    "ObjectiveProvenEvaluation",
    "evaluate_objective_proven",
]
