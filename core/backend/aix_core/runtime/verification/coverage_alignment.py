"""AIX-PD-003E — Coverage alignment across obligations, facets, and plan union.

Pure control plane: no LLM, no I/O.

E1 — Obligation coverage (0 orphan mandatory obligations)
E2 — Facet parity (UI ↔ API ↔ DB functional completeness)
E3 — Plan-union coverage (100% ProductStructure ops/entities on step bindings)
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.aix_core.runtime.objective.outcome_contract import OutcomeContractV1
from backend.aix_core.runtime.objective.outcome_obligations import compile_outcome_obligations
from backend.aix_core.runtime.objective.product_structure import ProductStructureV1
from backend.aix_core.runtime.objective.product_structure_step_binding import StepSemanticBindingV1

ALIGNMENT_VERSION = "pd003e.v1"


@dataclass(frozen=True)
class CoverageAlignmentResult:
    ok: bool
    code: str
    detail: str
    violations: tuple[str, ...] = ()


def validate_e1_obligation_coverage(
    *,
    contract: OutcomeContractV1,
    structure: ProductStructureV1,
    bindings: tuple[StepSemanticBindingV1, ...],
) -> CoverageAlignmentResult:
    """Every mandatory obligation must appear on at least one step binding."""
    compiled = compile_outcome_obligations(contract)
    if not compiled.ok:
        return CoverageAlignmentResult(
            ok=False,
            code="OBLIGATION_COMPILE_FAILED",
            detail=compiled.detail or compiled.code,
        )

    mandatory_ids = {o.obligation_id for o in compiled.obligations}
    union_obl = {oid for b in bindings for oid in b.obligation_refs}
    structure_obl = set(structure.obligation_ids)

    violations: list[str] = []
    orphans = sorted(mandatory_ids - union_obl)
    for oid in orphans:
        violations.append(f"E1:orphan_obligation:{oid}")

    uncovered_structure = sorted(structure_obl - union_obl)
    for oid in uncovered_structure:
        violations.append(f"E1:structure_obligation_unbound:{oid}")

    extra = sorted(union_obl - mandatory_ids - structure_obl)
    for oid in extra:
        violations.append(f"E1:unknown_obligation_ref:{oid}")

    if violations:
        return CoverageAlignmentResult(
            ok=False,
            code="COVERAGE_ALIGNMENT_E1_OBLIGATION",
            detail=f"{len(violations)} obligation coverage violation(s)",
            violations=tuple(violations),
        )
    return CoverageAlignmentResult(
        ok=True,
        code="COVERAGE_ALIGNMENT_E1_PASS",
        detail=f"{len(mandatory_ids)} mandatory obligations bound",
    )


def validate_e2_facet_parity(
    *,
    contract: OutcomeContractV1,
    structure: ProductStructureV1,
) -> CoverageAlignmentResult:
    """UI operations, API endpoints, and DB entities maintain facet completeness."""
    ui_ops = set(contract.facets.UI.required_operations)
    api_ops = {op.id for op in contract.facets.API.required_operations}
    db_entities = set(contract.facets.DB.entities)

    ui_to_api = {
        m.from_id: m.to_id
        for m in contract.facets.INTEGRATION.mappings
        if m.from_facet == "UI" and m.to_facet == "API"
    }
    api_to_db = {
        m.from_id: m.to_id
        for m in contract.facets.INTEGRATION.mappings
        if m.from_facet == "API" and m.to_facet == "DB"
    }

    violations: list[str] = []

    for ui in sorted(ui_ops):
        if ui not in ui_to_api:
            violations.append(f"E2:ui_missing_api_trigger:{ui}")

    for api in sorted(api_ops):
        if api not in set(ui_to_api.values()) and api not in {
            m.to_id for m in contract.facets.INTEGRATION.mappings if m.to_facet == "API"
        }:
            # API must be reachable from at least one UI map
            if api not in ui_to_api.values():
                violations.append(f"E2:api_missing_ui_trigger:{api}")
        if api not in api_to_db:
            violations.append(f"E2:api_missing_db_model:{api}")

    for entity in sorted(db_entities):
        if entity not in set(api_to_db.values()):
            violations.append(f"E2:db_missing_api_backing:{entity}")

    structure_api = {n.ref for n in structure.api_operations}
    structure_db = {n.ref for n in structure.db_entities}
    for ref in sorted(structure_api - api_ops):
        violations.append(f"E2:structure_api_exceeds_contract:{ref}")
    for ref in sorted(structure_db - db_entities):
        violations.append(f"E2:structure_db_exceeds_contract:{ref}")

    for view in structure.views:
        for op in view.ui_operation_refs:
            if op not in ui_ops:
                violations.append(f"E2:structure_ui_exceeds_contract:{view.view_id}:{op}")

    if violations:
        return CoverageAlignmentResult(
            ok=False,
            code="COVERAGE_ALIGNMENT_E2_FACET_PARITY",
            detail=f"{len(violations)} facet parity violation(s)",
            violations=tuple(violations),
        )
    return CoverageAlignmentResult(
        ok=True,
        code="COVERAGE_ALIGNMENT_E2_PASS",
        detail="facet parity complete",
    )


def validate_e3_plan_union_coverage(
    *,
    structure: ProductStructureV1,
    bindings: tuple[StepSemanticBindingV1, ...],
) -> CoverageAlignmentResult:
    """Union of step bindings must cover 100% of ProductStructure ops and entities."""
    union_ops = {op for b in bindings for op in b.operation_refs}
    union_ents = {ent for b in bindings for ent in b.entity_refs}
    union_maps = {mid for b in bindings for mid in b.mapping_refs}

    structure_api = {n.ref for n in structure.api_operations}
    structure_db = {n.ref for n in structure.db_entities}

    violations: list[str] = []
    missing_api = sorted(structure_api - union_ops)
    missing_db = sorted(structure_db - union_ents)

    for ref in missing_api:
        violations.append(f"E3:structure_api_uncovered:{ref}")
    for ref in missing_db:
        violations.append(f"E3:structure_db_uncovered:{ref}")

    # Mapping ids referenced by structure flows should appear in plan union
    flow_map_ids = {f.mapping_id for f in structure.flows if f.mapping_id}
    missing_maps = sorted(flow_map_ids - union_maps)
    for mid in missing_maps:
        violations.append(f"E3:structure_mapping_uncovered:{mid}")

    if violations:
        pct_api = (
            100.0
            if not structure_api
            else round(100.0 * len(structure_api & union_ops) / len(structure_api), 2)
        )
        pct_db = (
            100.0
            if not structure_db
            else round(100.0 * len(structure_db & union_ents) / len(structure_db), 2)
        )
        return CoverageAlignmentResult(
            ok=False,
            code="COVERAGE_ALIGNMENT_E3_PLAN_UNION",
            detail=f"plan union below 100% (api={pct_api}% db={pct_db}%)",
            violations=tuple(violations),
        )
    return CoverageAlignmentResult(
        ok=True,
        code="COVERAGE_ALIGNMENT_E3_PASS",
        detail="plan union covers 100% of structure ops/entities",
    )


def validate_product_structure_coverage_alignment(
    *,
    contract: OutcomeContractV1,
    structure: ProductStructureV1,
    bindings: tuple[StepSemanticBindingV1, ...],
) -> CoverageAlignmentResult:
    """Run E1 → E2 → E3; fail closed on first violation set."""
    if structure.contract_hash != contract.contract_hash:
        return CoverageAlignmentResult(
            ok=False,
            code="STRUCTURE_CONTRACT_MISMATCH",
            detail="ProductStructureV1.contract_hash must match sealed contract",
        )
    for check in (
        validate_e1_obligation_coverage(contract=contract, structure=structure, bindings=bindings),
        validate_e2_facet_parity(contract=contract, structure=structure),
        validate_e3_plan_union_coverage(structure=structure, bindings=bindings),
    ):
        if not check.ok:
            return check
    return CoverageAlignmentResult(
        ok=True,
        code="COVERAGE_ALIGNMENT_PASS",
        detail=f"{ALIGNMENT_VERSION}: E1+E2+E3 pass",
    )


__all__ = [
    "ALIGNMENT_VERSION",
    "CoverageAlignmentResult",
    "validate_e1_obligation_coverage",
    "validate_e2_facet_parity",
    "validate_e3_plan_union_coverage",
    "validate_product_structure_coverage_alignment",
]
