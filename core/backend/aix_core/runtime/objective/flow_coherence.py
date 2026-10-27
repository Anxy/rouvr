"""AIX-PD-003D — Deterministic flow coherence across ProductStructure plan steps.

Pure control plane: no LLM, no I/O.

D1 — Full-graph binding matrix (UI map → API op → DB entity)
D2 — Multi-step state propagation (depends_on inherits upstream refs)
D3 — Shared-surface guard (no path/op conflicts across steps)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.aix_core.runtime.objective.outcome_contract import OutcomeContractV1
from backend.aix_core.runtime.objective.product_structure import ProductStructureV1
from backend.aix_core.runtime.objective.product_structure_step_binding import StepSemanticBindingV1

COHERENCE_VERSION = "pd003d.v1"


@dataclass(frozen=True)
class FlowCoherenceResult:
    ok: bool
    code: str
    detail: str
    violations: tuple[str, ...] = ()


def _norm_path(path: str) -> str:
    return str(path or "").replace("\\", "/").strip()


def validate_d1_full_graph_binding_matrix(
    *,
    contract: OutcomeContractV1,
    structure: ProductStructureV1,
) -> FlowCoherenceResult:
    """Every integration mapping and structure flow must form a complete UI→API→DB chain."""
    violations: list[str] = []
    ui_ops = set(contract.facets.UI.required_operations)
    api_ops = {op.id for op in contract.facets.API.required_operations}
    db_entities = set(contract.facets.DB.entities)

    for mapping in contract.facets.INTEGRATION.mappings:
        if mapping.from_facet == "UI" and mapping.to_facet == "API":
            if mapping.from_id not in ui_ops:
                violations.append(f"D1:ui_map_orphan_from:{mapping.id}:{mapping.from_id}")
            if mapping.to_id not in api_ops:
                violations.append(f"D1:ui_map_orphan_to_api:{mapping.id}:{mapping.to_id}")
        elif mapping.from_facet == "API" and mapping.to_facet == "DB":
            if mapping.from_id not in api_ops:
                violations.append(f"D1:api_map_orphan_from:{mapping.id}:{mapping.from_id}")
            if mapping.to_id not in db_entities:
                violations.append(f"D1:api_map_orphan_to_db:{mapping.id}:{mapping.to_id}")

    api_refs = {n.ref for n in structure.api_operations}
    db_refs = {n.ref for n in structure.db_entities}
    for flow in structure.flows:
        if flow.api_operation_id and flow.db_entity_id:
            api_node = next(
                (n for n in structure.api_operations if n.api_operation_id == flow.api_operation_id),
                None,
            )
            db_node = next(
                (n for n in structure.db_entities if n.db_entity_id == flow.db_entity_id),
                None,
            )
            if api_node is None or api_node.ref not in api_ops:
                violations.append(f"D1:flow_api_unbound:{flow.flow_id}")
            if db_node is None or db_node.ref not in db_refs:
                violations.append(f"D1:flow_db_unbound:{flow.flow_id}")

    mapped_ui = {
        m.from_id for m in contract.facets.INTEGRATION.mappings if m.from_facet == "UI" and m.to_facet == "API"
    }
    for op in sorted(ui_ops):
        if op not in mapped_ui:
            violations.append(f"D1:ui_op_unmapped:{op}")

    if violations:
        return FlowCoherenceResult(
            ok=False,
            code="FLOW_COHERENCE_D1_BINDING_MATRIX",
            detail=f"{len(violations)} binding-matrix violation(s)",
            violations=tuple(violations),
        )
    return FlowCoherenceResult(ok=True, code="FLOW_COHERENCE_D1_PASS", detail="binding matrix complete")


def validate_d2_multi_step_state_propagation(
    *,
    contract: OutcomeContractV1,
    bindings: tuple[StepSemanticBindingV1, ...],
    depends_on: tuple[tuple[int, ...], ...],
) -> FlowCoherenceResult:
    """Downstream steps must inherit upstream facet refs through depends_on ordering."""
    violations: list[str] = []
    by_index = {b.step_index: b for b in bindings}
    ui_to_api = {
        m.from_id: m.to_id
        for m in contract.facets.INTEGRATION.mappings
        if m.from_facet == "UI" and m.to_facet == "API"
    }

    for index, deps in enumerate(depends_on):
        binding = by_index.get(index)
        if binding is None:
            continue
        upstream_ops: set[str] = set()
        upstream_entities: set[str] = set()
        for dep in deps:
            up = by_index.get(dep)
            if up is None:
                violations.append(f"D2:depends_on_unresolved:{index}->{dep}")
                continue
            upstream_ops.update(up.operation_refs)
            upstream_entities.update(up.entity_refs)

        if not deps:
            continue

        if binding.role == "API_BACKEND" and upstream_entities:
            api_to_db = [
                m
                for m in contract.facets.INTEGRATION.mappings
                if m.from_facet == "API" and m.to_facet == "DB" and m.to_id in upstream_entities
            ]
            missing_api = sorted(
                {m.from_id for m in api_to_db if m.from_id not in set(binding.operation_refs)}
            )
            if missing_api:
                violations.append(f"D2:api_missing_upstream_entities:step{index}:{','.join(missing_api)}")

        if binding.role == "UI_PAGE" and upstream_ops:
            needed_api = {ui_to_api.get(ui) for ui in binding.operation_refs if ui in ui_to_api}
            needed_api.discard(None)
            if needed_api and not needed_api.issubset(upstream_ops):
                missing = sorted(needed_api - upstream_ops)
                if missing:
                    violations.append(f"D2:ui_missing_upstream_api:step{index}:{','.join(missing)}")

    if violations:
        return FlowCoherenceResult(
            ok=False,
            code="FLOW_COHERENCE_D2_STATE_PROPAGATION",
            detail=f"{len(violations)} propagation violation(s)",
            violations=tuple(violations),
        )
    return FlowCoherenceResult(ok=True, code="FLOW_COHERENCE_D2_PASS", detail="state propagation ok")


def validate_d3_shared_surface_guard(
    *,
    structure: ProductStructureV1,
    bindings: tuple[StepSemanticBindingV1, ...],
    step_scopes: tuple[tuple[str, ...], ...],
) -> FlowCoherenceResult:
    """Prevent competing steps from claiming the same shared surface or leaving orphan API ops."""
    violations: list[str] = []
    path_steps: dict[str, list[int]] = {}
    for index, scope in enumerate(step_scopes):
        for raw in scope:
            path = _norm_path(raw)
            path_steps.setdefault(path, []).append(index)

    shared_api_paths = [
        p for p, idxs in path_steps.items() if p.endswith("api.php") and len(idxs) > 1
    ]
    for path in shared_api_paths:
        violations.append(f"D3:shared_api_surface:{path}")

    bound_api_ops: set[str] = set()
    for binding in bindings:
        bound_api_ops.update(binding.operation_refs)

    structure_api_refs = {n.ref for n in structure.api_operations}
    orphans = sorted(structure_api_refs - bound_api_ops)
    for op in orphans:
        violations.append(f"D3:orphaned_api_operation:{op}")

    if violations:
        return FlowCoherenceResult(
            ok=False,
            code="FLOW_COHERENCE_D3_SHARED_SURFACE",
            detail=f"{len(violations)} shared-surface violation(s)",
            violations=tuple(violations),
        )
    return FlowCoherenceResult(ok=True, code="FLOW_COHERENCE_D3_PASS", detail="shared surfaces ok")


def validate_product_structure_flow_coherence(
    *,
    contract: OutcomeContractV1,
    structure: ProductStructureV1,
    bindings: tuple[StepSemanticBindingV1, ...],
    step_scopes: tuple[tuple[str, ...], ...],
    depends_on: tuple[tuple[int, ...], ...] = (),
) -> FlowCoherenceResult:
    """Run D1 → D2 → D3; first failure wins."""
    for check in (
        validate_d1_full_graph_binding_matrix(contract=contract, structure=structure),
        validate_d2_multi_step_state_propagation(
            contract=contract,
            bindings=bindings,
            depends_on=depends_on,
        ),
        validate_d3_shared_surface_guard(
            structure=structure,
            bindings=bindings,
            step_scopes=step_scopes,
        ),
    ):
        if not check.ok:
            return check
    return FlowCoherenceResult(
        ok=True,
        code="FLOW_COHERENCE_PASS",
        detail=f"{COHERENCE_VERSION}: D1+D2+D3 pass",
    )


__all__ = [
    "COHERENCE_VERSION",
    "FlowCoherenceResult",
    "validate_d1_full_graph_binding_matrix",
    "validate_d2_multi_step_state_propagation",
    "validate_d3_shared_surface_guard",
    "validate_product_structure_flow_coherence",
]
