"""AIX-PD-003A — Deterministic Product Structure from sealed OutcomeContractV1.

Pure control plane: no LLM, no I/O, no planner, no product hard-coding.

Authoritative input:
  sealed OutcomeContractV1 + compile_outcome_obligations(contract)

Produces ProductStructureV1: capabilities, views/pages, flows, API ops,
DB entities, and typed relations — all IDs deterministic and sorted.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.runtime.objective.outcome_contract import OutcomeContractV1
from backend.aix_core.runtime.objective.outcome_obligations import (
    compile_outcome_obligations,
    sanitize_obligation_token,
)

STRUCTURE_VERSION = "v1"

RelationKind = Literal[
    "CAPABILITY_VIEW",
    "VIEW_UI_OPERATION",
    "VIEW_API_OPERATION",
    "API_DB_ENTITY",
    "FLOW_VIEW",
    "FLOW_API_OPERATION",
    "FLOW_DB_ENTITY",
    "FLOW_UI_OPERATION",
]

NodeKind = Literal[
    "CAPABILITY",
    "VIEW",
    "FLOW",
    "API_OPERATION",
    "DB_ENTITY",
    "UI_OPERATION",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapabilityNodeV1(_Frozen):
    capability_id: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    view_ids: tuple[str, ...] = ()


class ViewNodeV1(_Frozen):
    view_id: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    ui_operation_refs: tuple[str, ...] = ()
    source: Literal["CONTRACT_REQUIRED_VIEWS", "SYNTHETIC_FROM_UI_OPERATION"]


class FlowNodeV1(_Frozen):
    flow_id: str = Field(min_length=1)
    mapping_id: str = Field(min_length=1)
    from_facet: str = Field(min_length=1)
    from_id: str = Field(min_length=1)
    to_facet: str = Field(min_length=1)
    to_id: str = Field(min_length=1)
    view_id: str | None = None
    api_operation_id: str | None = None
    db_entity_id: str | None = None


class ApiOperationNodeV1(_Frozen):
    api_operation_id: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    method: str | None = None
    path_or_action: str | None = None
    obligation_id: str = Field(min_length=1)


class DbEntityNodeV1(_Frozen):
    db_entity_id: str = Field(min_length=1)
    ref: str = Field(min_length=1)
    obligation_id: str = Field(min_length=1)


class RelationEdgeV1(_Frozen):
    relation_id: str = Field(min_length=1)
    kind: RelationKind
    from_kind: NodeKind
    from_id: str = Field(min_length=1)
    to_kind: NodeKind
    to_id: str = Field(min_length=1)


class ProductStructureV1(_Frozen):
    """Deterministic product shape projected from sealed contract + obligations."""

    version: Literal["v1"] = STRUCTURE_VERSION
    structure_id: str = Field(min_length=1)
    contract_id: str = Field(min_length=1)
    contract_hash: str = Field(min_length=1)
    capabilities: tuple[CapabilityNodeV1, ...] = ()
    views: tuple[ViewNodeV1, ...] = ()
    flows: tuple[FlowNodeV1, ...] = ()
    api_operations: tuple[ApiOperationNodeV1, ...] = ()
    db_entities: tuple[DbEntityNodeV1, ...] = ()
    relations: tuple[RelationEdgeV1, ...] = ()
    obligation_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


@dataclass(frozen=True)
class ProductStructureCompileResult:
    ok: bool
    code: str
    detail: str
    structure: ProductStructureV1 | None = None


def _structure_id(*, contract_hash: str) -> str:
    digest = hashlib.sha256(
        f"product_structure|{STRUCTURE_VERSION}|{contract_hash}".encode("utf-8")
    ).hexdigest()
    return f"ps_{digest[:24]}"


def _view_id_for(ref: str) -> str:
    return f"view__{sanitize_obligation_token(ref)}"


def _capability_id_for(ref: str) -> str:
    return f"cap__{sanitize_obligation_token(ref)}"


def _api_node_id(ref: str) -> str:
    return f"api__{sanitize_obligation_token(ref)}"


def _db_node_id(ref: str) -> str:
    return f"db__{sanitize_obligation_token(ref)}"


def _flow_id_for(mapping_id: str) -> str:
    return f"flow__{sanitize_obligation_token(mapping_id)}"


def _relation_id(
    kind: RelationKind,
    from_id: str,
    to_id: str,
) -> str:
    return (
        f"rel__{sanitize_obligation_token(kind)}"
        f"__{sanitize_obligation_token(from_id)}"
        f"__{sanitize_obligation_token(to_id)}"
    )


def _pair_views_to_ui_ops(
    *,
    contract_views: tuple[str, ...],
    contract_ui_ops: tuple[str, ...],
) -> list[ViewNodeV1]:
    """Deterministic view nodes from contract views and/or UI operations.

    Views come only from sealed contract `required_views` when present;
    otherwise one synthetic view per UI operation. Never from a product catalog.

    When view count equals UI op count, pair by **contract declaration order**
    (parallel facets), not by independently sorting both lists.
    """
    views_ordered = tuple(str(v).strip() for v in contract_views if str(v).strip())
    ops_ordered = tuple(str(o).strip() for o in contract_ui_ops if str(o).strip())
    ops_sorted = tuple(sorted(ops_ordered))

    if views_ordered:
        nodes: list[ViewNodeV1] = []
        if len(views_ordered) == len(ops_ordered):
            for view_ref, op in zip(views_ordered, ops_ordered, strict=True):
                nodes.append(
                    ViewNodeV1(
                        view_id=_view_id_for(view_ref),
                        ref=view_ref,
                        ui_operation_refs=(op,),
                        source="CONTRACT_REQUIRED_VIEWS",
                    )
                )
            return sorted(nodes, key=lambda n: n.view_id)

        views_sorted = tuple(sorted(views_ordered))
        # Count mismatch: assign by sorted index chunks; leftover ops → last view.
        for idx, view_ref in enumerate(views_sorted):
            assigned: list[str] = []
            if ops_sorted:
                if len(ops_sorted) >= len(views_sorted):
                    chunk = len(ops_sorted) // len(views_sorted)
                    rem = len(ops_sorted) % len(views_sorted)
                    start = idx * chunk + min(idx, rem)
                    end = start + chunk + (1 if idx < rem else 0)
                    assigned = list(ops_sorted[start:end])
                elif idx < len(ops_sorted):
                    assigned = [ops_sorted[idx]]
            nodes.append(
                ViewNodeV1(
                    view_id=_view_id_for(view_ref),
                    ref=view_ref,
                    ui_operation_refs=tuple(assigned),
                    source="CONTRACT_REQUIRED_VIEWS",
                )
            )
        covered = {op for n in nodes for op in n.ui_operation_refs}
        leftover = [op for op in ops_sorted if op not in covered]
        if leftover and nodes:
            last = nodes[-1]
            nodes[-1] = ViewNodeV1(
                view_id=last.view_id,
                ref=last.ref,
                ui_operation_refs=tuple(sorted(set(last.ui_operation_refs) | set(leftover))),
                source=last.source,
            )
        return sorted(nodes, key=lambda n: n.view_id)

    # No contract views: synthesize one view per UI op (sorted for stability)
    nodes = [
        ViewNodeV1(
            view_id=_view_id_for(op),
            ref=f"view_{op}",
            ui_operation_refs=(op,),
            source="SYNTHETIC_FROM_UI_OPERATION",
        )
        for op in ops_sorted
    ]
    return sorted(nodes, key=lambda n: n.view_id)


def compile_product_structure(contract: OutcomeContractV1) -> ProductStructureCompileResult:
    """Project sealed OutcomeContractV1 into ProductStructureV1. Fail closed."""
    if contract is None:  # type: ignore[comparison-overlap]
        return ProductStructureCompileResult(
            ok=False,
            code="OUTCOME_CONTRACT_REQUIRED",
            detail="OutcomeContractV1 is required",
        )

    obl_result = compile_outcome_obligations(contract)
    if not obl_result.ok:
        # Preserve unsealed / hash / invalid codes from obligation compiler
        code = obl_result.code
        if code not in {
            "CONTRACT_UNSEALED",
            "CONTRACT_HASH_MISMATCH",
            "CONTRACT_INVALID",
            "OUTCOME_CONTRACT_REQUIRED",
        }:
            code = "OBLIGATIONS_FAILED"
        return ProductStructureCompileResult(
            ok=False,
            code=code,
            detail=obl_result.detail,
        )

    obligations = obl_result.obligations
    obligation_ids = tuple(sorted(o.obligation_id for o in obligations))

    api_specs = {
        op.id: op for op in contract.facets.API.required_operations
    }

    # Prefer contract declaration order for view↔op pairing (not obligation sort order).
    contract_ui_ops = tuple(contract.facets.UI.required_operations)
    if not contract_ui_ops:
        contract_ui_ops = tuple(
            o.ref for o in obligations if o.kind == "UI_OPERATION"
        )
    api_obls = tuple(
        sorted((o for o in obligations if o.kind == "API_OPERATION"), key=lambda o: o.obligation_id)
    )
    db_obls = tuple(
        sorted((o for o in obligations if o.kind == "DB_ENTITY"), key=lambda o: o.obligation_id)
    )
    map_obls = tuple(
        sorted(
            (o for o in obligations if o.kind == "INTEGRATION_MAPPING"),
            key=lambda o: o.obligation_id,
        )
    )

    views = _pair_views_to_ui_ops(
        contract_views=tuple(contract.facets.UI.required_views),
        contract_ui_ops=contract_ui_ops,
    )
    view_by_ui_op: dict[str, str] = {}
    for view in views:
        for op in view.ui_operation_refs:
            view_by_ui_op[op] = view.view_id

    api_operations = tuple(
        ApiOperationNodeV1(
            api_operation_id=_api_node_id(o.ref),
            ref=o.ref,
            method=(api_specs[o.ref].method if o.ref in api_specs else None),
            path_or_action=(
                api_specs[o.ref].path_or_action if o.ref in api_specs else None
            ),
            obligation_id=o.obligation_id,
        )
        for o in api_obls
    )
    api_id_by_ref = {n.ref: n.api_operation_id for n in api_operations}

    db_entities = tuple(
        DbEntityNodeV1(
            db_entity_id=_db_node_id(o.ref),
            ref=o.ref,
            obligation_id=o.obligation_id,
        )
        for o in db_obls
    )
    db_id_by_ref = {n.ref: n.db_entity_id for n in db_entities}

    # API → DB lookup from mappings
    api_to_db: dict[str, str] = {}
    for o in map_obls:
        if o.from_facet == "API" and o.to_facet == "DB" and o.from_id and o.to_id:
            api_to_db[o.from_id] = o.to_id

    flows: list[FlowNodeV1] = []
    for o in map_obls:
        if o.from_facet != "UI" or o.to_facet != "API":
            continue
        if not o.from_id or not o.to_id:
            continue
        view_id = view_by_ui_op.get(o.from_id)
        api_op_id = api_id_by_ref.get(o.to_id)
        db_ref = api_to_db.get(o.to_id)
        db_entity_id = db_id_by_ref.get(db_ref) if db_ref else None
        flows.append(
            FlowNodeV1(
                flow_id=_flow_id_for(o.ref),
                mapping_id=o.ref,
                from_facet=o.from_facet,
                from_id=o.from_id,
                to_facet=o.to_facet,
                to_id=o.to_id,
                view_id=view_id,
                api_operation_id=api_op_id,
                db_entity_id=db_entity_id,
            )
        )
    flows_t = tuple(sorted(flows, key=lambda f: f.flow_id))

    # Capabilities: one per view (contract-driven; no product catalog)
    capabilities: list[CapabilityNodeV1] = []
    if views:
        for view in views:
            capabilities.append(
                CapabilityNodeV1(
                    capability_id=_capability_id_for(view.ref),
                    ref=view.ref,
                    view_ids=(view.view_id,),
                )
            )
    else:
        # Structural empty without views — still allow API/DB-only if present
        capabilities.append(
            CapabilityNodeV1(
                capability_id=_capability_id_for("default"),
                ref="default",
                view_ids=(),
            )
        )
    capabilities_t = tuple(sorted(capabilities, key=lambda c: c.capability_id))

    relations: list[RelationEdgeV1] = []

    def _add(
        kind: RelationKind,
        from_kind: NodeKind,
        from_id: str,
        to_kind: NodeKind,
        to_id: str,
    ) -> None:
        relations.append(
            RelationEdgeV1(
                relation_id=_relation_id(kind, from_id, to_id),
                kind=kind,
                from_kind=from_kind,
                from_id=from_id,
                to_kind=to_kind,
                to_id=to_id,
            )
        )

    for cap in capabilities_t:
        for vid in cap.view_ids:
            _add("CAPABILITY_VIEW", "CAPABILITY", cap.capability_id, "VIEW", vid)

    for view in views:
        for op in view.ui_operation_refs:
            _add("VIEW_UI_OPERATION", "VIEW", view.view_id, "UI_OPERATION", op)

    for flow in flows_t:
        if flow.view_id:
            _add("FLOW_VIEW", "FLOW", flow.flow_id, "VIEW", flow.view_id)
            _add(
                "VIEW_API_OPERATION",
                "VIEW",
                flow.view_id,
                "API_OPERATION",
                flow.api_operation_id or _api_node_id(flow.to_id),
            )
        _add("FLOW_UI_OPERATION", "FLOW", flow.flow_id, "UI_OPERATION", flow.from_id)
        if flow.api_operation_id:
            _add(
                "FLOW_API_OPERATION",
                "FLOW",
                flow.flow_id,
                "API_OPERATION",
                flow.api_operation_id,
            )
        if flow.db_entity_id:
            _add(
                "FLOW_DB_ENTITY",
                "FLOW",
                flow.flow_id,
                "DB_ENTITY",
                flow.db_entity_id,
            )
            if flow.api_operation_id:
                _add(
                    "API_DB_ENTITY",
                    "API_OPERATION",
                    flow.api_operation_id,
                    "DB_ENTITY",
                    flow.db_entity_id,
                )

    # Deduplicate relations by relation_id (deterministic last-write identical)
    rel_seen: dict[str, RelationEdgeV1] = {}
    for edge in sorted(relations, key=lambda e: e.relation_id):
        rel_seen[edge.relation_id] = edge
    relations_t = tuple(rel_seen[k] for k in sorted(rel_seen))

    # Empty structure: no views, no api, no db, no flows
    if not views and not api_operations and not db_entities and not flows_t:
        return ProductStructureCompileResult(
            ok=False,
            code="PRODUCT_STRUCTURE_EMPTY",
            detail="no views, api operations, db entities, or flows derived",
        )

    structure = ProductStructureV1(
        structure_id=_structure_id(contract_hash=contract.contract_hash),
        contract_id=contract.contract_id,
        contract_hash=contract.contract_hash,
        capabilities=capabilities_t,
        views=tuple(views),
        flows=flows_t,
        api_operations=api_operations,
        db_entities=db_entities,
        relations=relations_t,
        obligation_ids=obligation_ids,
    )

    return ProductStructureCompileResult(
        ok=True,
        code="PRODUCT_STRUCTURE_COMPILED",
        detail=(
            f"compiled structure {structure.structure_id}: "
            f"{len(structure.capabilities)} capabilities, "
            f"{len(structure.views)} views, "
            f"{len(structure.flows)} flows, "
            f"{len(structure.api_operations)} api ops, "
            f"{len(structure.db_entities)} db entities, "
            f"{len(structure.relations)} relations"
        ),
        structure=structure,
    )


def product_structure_canonical_json(structure: ProductStructureV1) -> str:
    """Canonical JSON for determinism assertions."""
    return json.dumps(structure.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


__all__ = [
    "STRUCTURE_VERSION",
    "RelationKind",
    "NodeKind",
    "CapabilityNodeV1",
    "ViewNodeV1",
    "FlowNodeV1",
    "ApiOperationNodeV1",
    "DbEntityNodeV1",
    "RelationEdgeV1",
    "ProductStructureV1",
    "ProductStructureCompileResult",
    "compile_product_structure",
    "product_structure_canonical_json",
]
