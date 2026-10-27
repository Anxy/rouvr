"""Deterministic OutcomeContract coverage gate.

Runs after OutcomeContractV1 validation and StepContract binding, before Plan ACCEPTED.
No LLM. Fail closed when mandatory facet ops/entities/mappings are unbound.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.aix_core.runtime.objective.contracts import StepContractV1
from backend.aix_core.runtime.objective.outcome_contract import (
    OutcomeContractV1,
    validate_outcome_contract_structure,
)
from backend.aix_core.runtime.verification.semantic_binding import evaluate_semantic_bindings


@dataclass(frozen=True)
class ContractCoverageResult:
    ok: bool
    code: str
    detail: str
    unbound_operations: tuple[str, ...] = ()
    unbound_entities: tuple[str, ...] = ()
    unbound_mappings: tuple[str, ...] = ()
    uncovered_facets: tuple[str, ...] = ()


def evaluate_contract_coverage(
    *,
    outcome_contract: OutcomeContractV1 | None,
    steps: tuple[StepContractV1, ...] | list[StepContractV1],
    require_outcome_contract: bool = True,
) -> ContractCoverageResult:
    """Fail closed if sealed contract is missing/invalid or mandatory refs unbound."""
    if outcome_contract is None:
        if require_outcome_contract:
            return ContractCoverageResult(
                ok=False,
                code="OUTCOME_CONTRACT_REQUIRED",
                detail="OutcomeContractV1 must exist before decomposition is authoritative",
            )
        return ContractCoverageResult(
            ok=True,
            code="OUTCOME_CONTRACT_ABSENT_LEGACY",
            detail="no outcome contract; coverage gate skipped (legacy)",
        )

    if not outcome_contract.contract_hash:
        return ContractCoverageResult(
            ok=False,
            code="CONTRACT_INVALID",
            detail="OutcomeContractV1 must be sealed (contract_hash) before decomposition",
        )

    struct_ok, struct_code, struct_details = validate_outcome_contract_structure(outcome_contract)
    if not struct_ok:
        return ContractCoverageResult(
            ok=False,
            code=struct_code,
            detail="; ".join(struct_details) or "outcome contract invalid",
            uncovered_facets=tuple(
                d.split(" ", 1)[0] for d in struct_details if d.split(" ", 1)[0] in {"UI", "API", "DB", "AUTH", "INTEGRATION"}
            ),
        )

    step_list = tuple(steps)
    contract_id = outcome_contract.contract_id
    contract_hash = outcome_contract.contract_hash

    for step in step_list:
        if step.outcome_contract_id and step.outcome_contract_id != contract_id:
            return ContractCoverageResult(
                ok=False,
                code="CONTRACT_REF_UNKNOWN",
                detail=f"step {step.id!r} cites unknown outcome_contract_id",
            )
        if step.outcome_contract_hash and step.outcome_contract_hash != contract_hash:
            return ContractCoverageResult(
                ok=False,
                code="CONTRACT_HASH_MISMATCH",
                detail=f"step {step.id!r} outcome_contract_hash does not match sealed contract",
            )

    bound_ops: set[str] = set()
    bound_entities: set[str] = set()
    bound_mappings: set[str] = set()
    bound_facets: set[str] = set()
    for step in step_list:
        bound_ops.update(str(x) for x in step.operation_refs if str(x).strip())
        bound_entities.update(str(x) for x in step.entity_refs if str(x).strip())
        bound_mappings.update(str(x) for x in step.mapping_refs if str(x).strip())
        bound_facets.update(str(x).upper() for x in step.facet_refs if str(x).strip())

    facets = outcome_contract.facets
    uncovered_facets: list[str] = []
    unbound_ops: list[str] = []
    unbound_entities: list[str] = []
    unbound_mappings: list[str] = []

    if facets.UI.requirement == "MANDATORY":
        if "UI" not in bound_facets and facets.UI.required_operations:
            # facet_refs optional if operation_refs cover UI ops
            if not any(op in bound_ops for op in facets.UI.required_operations):
                uncovered_facets.append("UI")
        for op in facets.UI.required_operations:
            if op not in bound_ops:
                unbound_ops.append(f"UI.{op}")

    if facets.API.requirement == "MANDATORY":
        api_ids = [op.id for op in facets.API.required_operations]
        if "API" not in bound_facets and api_ids and not any(op in bound_ops for op in api_ids):
            uncovered_facets.append("API")
        for op in facets.API.required_operations:
            if op.id not in bound_ops:
                unbound_ops.append(f"API.{op.id}")

    if facets.DB.requirement == "MANDATORY":
        if "DB" not in bound_facets and facets.DB.entities and not any(
            e in bound_entities for e in facets.DB.entities
        ):
            uncovered_facets.append("DB")
        for entity in facets.DB.entities:
            if entity not in bound_entities:
                unbound_entities.append(entity)

    if facets.INTEGRATION.requirement == "MANDATORY":
        if "INTEGRATION" not in bound_facets and facets.INTEGRATION.mappings and not any(
            m.id in bound_mappings for m in facets.INTEGRATION.mappings
        ):
            # Mappings may be proven by IntegrationProof without step mapping_refs if
            # ops are bound; still require each mapping id bound OR both endpoints bound.
            for mapping in facets.INTEGRATION.mappings:
                endpoints_bound = (
                    mapping.from_id in bound_ops or mapping.from_id in bound_entities
                ) and (mapping.to_id in bound_ops or mapping.to_id in bound_entities)
                if mapping.id not in bound_mappings and not endpoints_bound:
                    unbound_mappings.append(mapping.id)
        else:
            for mapping in facets.INTEGRATION.mappings:
                if mapping.id not in bound_mappings:
                    endpoints_bound = (
                        mapping.from_id in bound_ops or mapping.from_id in bound_entities
                    ) and (mapping.to_id in bound_ops or mapping.to_id in bound_entities)
                    if not endpoints_bound:
                        unbound_mappings.append(mapping.id)

    # Unknown refs on steps
    known_ops = set(facets.UI.required_operations) | {
        op.id for op in facets.API.required_operations
    }
    known_entities = set(facets.DB.entities)
    known_mappings = {m.id for m in facets.INTEGRATION.mappings}
    for step in step_list:
        for ref in step.operation_refs:
            if ref not in known_ops:
                return ContractCoverageResult(
                    ok=False,
                    code="CONTRACT_REF_UNKNOWN",
                    detail=f"step {step.id!r} operation_ref {ref!r} not on sealed contract",
                )
        for ref in step.entity_refs:
            if ref not in known_entities:
                return ContractCoverageResult(
                    ok=False,
                    code="CONTRACT_REF_UNKNOWN",
                    detail=f"step {step.id!r} entity_ref {ref!r} not on sealed contract",
                )
        for ref in step.mapping_refs:
            if ref not in known_mappings:
                return ContractCoverageResult(
                    ok=False,
                    code="CONTRACT_REF_UNKNOWN",
                    detail=f"step {step.id!r} mapping_ref {ref!r} not on sealed contract",
                )

    if uncovered_facets:
        return ContractCoverageResult(
            ok=False,
            code="CONTRACT_FACET_MISSING",
            detail=f"mandatory facets unbound: {uncovered_facets}",
            uncovered_facets=tuple(uncovered_facets),
            unbound_operations=tuple(unbound_ops),
            unbound_entities=tuple(unbound_entities),
            unbound_mappings=tuple(unbound_mappings),
        )
    if unbound_ops:
        return ContractCoverageResult(
            ok=False,
            code="CONTRACT_OP_UNBOUND",
            detail=f"mandatory operations unbound: {unbound_ops}",
            unbound_operations=tuple(unbound_ops),
            unbound_entities=tuple(unbound_entities),
            unbound_mappings=tuple(unbound_mappings),
        )
    if unbound_entities:
        return ContractCoverageResult(
            ok=False,
            code="CONTRACT_ENTITY_UNBOUND",
            detail=f"mandatory entities unbound: {unbound_entities}",
            unbound_entities=tuple(unbound_entities),
            unbound_mappings=tuple(unbound_mappings),
        )
    if unbound_mappings:
        return ContractCoverageResult(
            ok=False,
            code="CONTRACT_MAPPING_UNBOUND",
            detail=f"mandatory mappings unbound: {unbound_mappings}",
            unbound_mappings=tuple(unbound_mappings),
        )

    # DoD-002E: declared bindings must be semantically substantiated (not file-stamp theater)
    binding = evaluate_semantic_bindings(outcome_contract=outcome_contract, steps=step_list)
    if not binding.ok:
        return ContractCoverageResult(
            ok=False,
            code=binding.code,
            detail=binding.detail,
            unbound_operations=tuple(unbound_ops),
            unbound_entities=tuple(unbound_entities),
            unbound_mappings=tuple(unbound_mappings),
        )

    return ContractCoverageResult(
        ok=True,
        code="CONTRACT_COVERAGE_OK",
        detail="all mandatory facet operations/entities/mappings are bound to steps",
    )


__all__ = ["ContractCoverageResult", "evaluate_contract_coverage"]
