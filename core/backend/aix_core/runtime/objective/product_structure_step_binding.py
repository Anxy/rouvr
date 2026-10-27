"""AIX-PD-003B2 — ProductStructure → StepContract semantic bindings.

Pure control plane: no LLM, no I/O, no filename→all-ops dumps.

Assigns facet_refs / operation_refs / entity_refs / mapping_refs / obligation_refs
to PD-003B step groups using ProductStructureV1 traces + sealed contract.

Plan-level union of step bindings must cover mandatory contract refs.
Individual steps stay narrow (e.g. one UI page binds only that view's ops).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

from backend.aix_core.runtime.objective.outcome_contract import OutcomeContractV1
from backend.aix_core.runtime.objective.outcome_obligations import (
    compile_outcome_obligations,
    sanitize_obligation_token,
)
from backend.aix_core.runtime.objective.product_structure import ProductStructureV1
from backend.aix_core.runtime.objective.product_structure_plan import TargetRole, TargetTrace

BINDER_VERSION = "v1"

StepRole = Literal[
    "DIRECTORY",
    "DB_SCHEMA",
    "API_BACKEND",
    "UI_ASSET",
    "UI_PAGE",
    "SUPPORTING",
    "MIXED",
    "UNKNOWN",
]


@dataclass(frozen=True)
class StepSemanticBindingV1:
    """Deterministic semantic refs for one PD-003B step group."""

    binding_id: str
    step_index: int
    role: StepRole
    facet_refs: tuple[str, ...] = ()
    operation_refs: tuple[str, ...] = ()
    entity_refs: tuple[str, ...] = ()
    mapping_refs: tuple[str, ...] = ()
    obligation_refs: tuple[str, ...] = ()
    structure_refs: tuple[str, ...] = ()  # view_id / api_operation_id / db_entity_id / flow_id


@dataclass(frozen=True)
class ProductStructureStepBindingResult:
    ok: bool
    code: str
    detail: str
    bindings: tuple[StepSemanticBindingV1, ...] = ()


def _binding_id(*, step_index: int, role: str, payload: str) -> str:
    digest = hashlib.sha256(
        f"pd003b2|{BINDER_VERSION}|{step_index}|{role}|{payload}".encode("utf-8")
    ).hexdigest()[:20]
    return f"sb_{digest}"


_AUTH_OP_MARKERS = frozenset(
    {
        "login",
        "register",
        "auth.login",
        "auth.register",
        "sign_in",
        "sign_up",
    }
)


def _is_auth_bearing_ops(operation_refs: set[str] | frozenset[str]) -> bool:
    if operation_refs & _AUTH_OP_MARKERS:
        return True
    return any(str(op).startswith("auth.") for op in operation_refs)


def _norm_path(path: str) -> str:
    return str(path or "").replace("\\", "/").strip()


def _basename(path: str) -> str:
    p = _norm_path(path).rstrip("/")
    return p.rsplit("/", 1)[-1] if p else ""


def _step_role_from_traces(
    *,
    scope: tuple[str, ...],
    path_role: dict[str, TargetRole],
) -> StepRole:
    roles: list[TargetRole] = []
    for raw in scope:
        path = _norm_path(raw)
        role = path_role.get(path) or path_role.get(path.rstrip("/") + "/")
        if role is not None:
            roles.append(role)
    if not roles:
        return "UNKNOWN"
    uniq = tuple(sorted(set(roles)))
    if len(uniq) == 1:
        return uniq[0]  # type: ignore[return-value]
    # Prefer substantive roles over directory when mixed
    substantive = [r for r in uniq if r != "DIRECTORY"]
    if len(substantive) == 1:
        return substantive[0]  # type: ignore[return-value]
    return "MIXED"


def _view_for_page_path(
    *,
    path: str,
    structure: ProductStructureV1,
) -> Any | None:
    base = _basename(path).casefold()
    if not base.endswith(".html"):
        return None
    stem = base[: -len(".html")]
    for view in structure.views:
        if sanitize_obligation_token(view.ref) == stem:
            return view
        if sanitize_obligation_token(view.view_id) == stem:
            return view
    return None


def _obligations_for_refs(
    *,
    contract: OutcomeContractV1,
    operation_refs: set[str],
    entity_refs: set[str],
    mapping_refs: set[str],
) -> tuple[str, ...]:
    obl = compile_outcome_obligations(contract)
    if not obl.ok:
        return ()
    out: list[str] = []
    auth_bearing = _is_auth_bearing_ops(operation_refs)
    for o in obl.obligations:
        if o.kind in {"UI_OPERATION", "API_OPERATION"} and o.ref in operation_refs:
            out.append(o.obligation_id)
        elif o.kind == "DB_ENTITY" and o.ref in entity_refs:
            out.append(o.obligation_id)
        elif o.kind == "INTEGRATION_MAPPING" and o.ref in mapping_refs:
            out.append(o.obligation_id)
        elif o.kind in {"AUTH_ACTOR", "AUTH_PERMISSION"} and auth_bearing:
            # AUTH obligations have no op/entity ref on steps; bind them to
            # login/register (UI) and auth.* (API) groups so E1 is satisfiable.
            out.append(o.obligation_id)
    return tuple(sorted(set(out)))


def compile_product_structure_step_bindings(
    *,
    contract: OutcomeContractV1,
    structure: ProductStructureV1,
    step_scopes: tuple[tuple[str, ...], ...],
    traces: tuple[TargetTrace, ...],
) -> ProductStructureStepBindingResult:
    """Compile per-step semantic bindings from structure + PD-003B traces."""
    if structure.contract_hash != contract.contract_hash:
        return ProductStructureStepBindingResult(
            ok=False,
            code="STRUCTURE_CONTRACT_MISMATCH",
            detail="ProductStructureV1.contract_hash must match sealed contract",
        )
    if not step_scopes:
        return ProductStructureStepBindingResult(
            ok=False,
            code="STEP_SCOPES_REQUIRED",
            detail="PD-003B step_scopes required for binding",
        )
    if not traces:
        return ProductStructureStepBindingResult(
            ok=False,
            code="TRACES_REQUIRED",
            detail="PD-003B TargetTrace list required (no filename guessing)",
        )

    path_role: dict[str, TargetRole] = {}
    path_structure_ref: dict[str, str] = {}
    for tr in traces:
        path_role[_norm_path(tr.path)] = tr.role
        path_structure_ref[_norm_path(tr.path)] = tr.structure_ref

    ui_ops_known = set(contract.facets.UI.required_operations)
    api_ops_known = {op.id for op in contract.facets.API.required_operations}
    entities_known = set(contract.facets.DB.entities)
    mappings = list(contract.facets.INTEGRATION.mappings)
    mapping_by_id = {m.id: m for m in mappings}

    # Structure-indexed helpers
    api_refs = tuple(sorted({n.ref for n in structure.api_operations}))
    db_refs = tuple(sorted({n.ref for n in structure.db_entities}))
    view_by_id = {v.view_id: v for v in structure.views}

    ui_to_api_maps = [
        m for m in mappings if m.from_facet == "UI" and m.to_facet == "API"
    ]
    api_to_db_maps = [
        m for m in mappings if m.from_facet == "API" and m.to_facet == "DB"
    ]

    bindings: list[StepSemanticBindingV1] = []
    for index, scope in enumerate(step_scopes):
        role = _step_role_from_traces(scope=scope, path_role=path_role)
        facets: list[str] = []
        ops: list[str] = []
        ents: list[str] = []
        maps: list[str] = []
        struct_refs: list[str] = []

        if role == "UNKNOWN":
            return ProductStructureStepBindingResult(
                ok=False,
                code="STEP_ROLE_UNKNOWN",
                detail=f"step {index} paths not present in ProductStructure plan traces",
            )
        if role == "MIXED":
            return ProductStructureStepBindingResult(
                ok=False,
                code="STEP_ROLE_AMBIGUOUS",
                detail=f"step {index} mixes incompatible TargetTrace roles",
            )

        if role == "UI_PAGE":
            matched_any = False
            for path in scope:
                view = _view_for_page_path(path=path, structure=structure)
                if view is None:
                    # Shell pages (index.html) may be UI_ASSET-classified; if on UI_PAGE
                    # step without a view, fail closed rather than guess.
                    base = _basename(path).casefold()
                    if base.endswith(".html"):
                        return ProductStructureStepBindingResult(
                            ok=False,
                            code="VIEW_PATH_UNMAPPED",
                            detail=f"UI page path {path!r} does not map to a ProductStructure view",
                        )
                    continue
                matched_any = True
                struct_refs.append(view.view_id)
                for op in view.ui_operation_refs:
                    if op in ui_ops_known:
                        ops.append(op)
                    else:
                        return ProductStructureStepBindingResult(
                            ok=False,
                            code="OPERATION_NOT_ON_CONTRACT",
                            detail=f"view {view.view_id} ui op {op!r} not on sealed UI facet",
                        )
                for m in ui_to_api_maps:
                    if m.from_id in view.ui_operation_refs:
                        maps.append(m.id)
            if structure.views and not matched_any:
                return ProductStructureStepBindingResult(
                    ok=False,
                    code="VIEW_PATH_UNMAPPED",
                    detail=f"UI_PAGE step {index} bound no ProductStructure views",
                )
            if ops:
                facets.append("UI")
            if _is_auth_bearing_ops(set(ops)):
                facets.append("AUTH")

        elif role == "API_BACKEND":
            # Bind structure API nodes assigned to this step group (not "all ops because .php").
            if not api_refs:
                return ProductStructureStepBindingResult(
                    ok=False,
                    code="API_STRUCTURE_EMPTY",
                    detail="API_BACKEND step but ProductStructure has no api_operations",
                )
            for ref in api_refs:
                if ref not in api_ops_known:
                    return ProductStructureStepBindingResult(
                        ok=False,
                        code="OPERATION_NOT_ON_CONTRACT",
                        detail=f"structure API ref {ref!r} not on sealed API facet",
                    )
                ops.append(ref)
            for n in structure.api_operations:
                struct_refs.append(n.api_operation_id)
            for m in api_to_db_maps:
                if m.from_id in api_refs:
                    maps.append(m.id)
            facets.append("API")
            if _is_auth_bearing_ops(set(ops)):
                facets.append("AUTH")

        elif role == "DB_SCHEMA":
            if not db_refs:
                return ProductStructureStepBindingResult(
                    ok=False,
                    code="DB_STRUCTURE_EMPTY",
                    detail="DB_SCHEMA step but ProductStructure has no db_entities",
                )
            for ref in db_refs:
                if ref not in entities_known:
                    return ProductStructureStepBindingResult(
                        ok=False,
                        code="ENTITY_NOT_ON_CONTRACT",
                        detail=f"structure DB ref {ref!r} not on sealed DB facet",
                    )
                ents.append(ref)
            for n in structure.db_entities:
                struct_refs.append(n.db_entity_id)
            for m in api_to_db_maps:
                if m.to_id in db_refs:
                    maps.append(m.id)
            facets.append("DB")

        elif role in {"DIRECTORY", "UI_ASSET", "SUPPORTING"}:
            # No contract op/entity coverage from these groups (narrow).
            pass

        # Validate mapping ids
        for mid in maps:
            if mid not in mapping_by_id:
                return ProductStructureStepBindingResult(
                    ok=False,
                    code="MAPPING_NOT_ON_CONTRACT",
                    detail=f"mapping {mid!r} not on sealed INTEGRATION facet",
                )

        ops_t = tuple(sorted(set(ops)))
        ents_t = tuple(sorted(set(ents)))
        maps_t = tuple(sorted(set(maps)))
        facets_t = tuple(sorted(set(facets)))
        struct_t = tuple(sorted(set(struct_refs)))
        obl_t = _obligations_for_refs(
            contract=contract,
            operation_refs=set(ops_t),
            entity_refs=set(ents_t),
            mapping_refs=set(maps_t),
        )
        payload = "|".join(
            [
                ",".join(facets_t),
                ",".join(ops_t),
                ",".join(ents_t),
                ",".join(maps_t),
                ",".join(obl_t),
            ]
        )
        bindings.append(
            StepSemanticBindingV1(
                binding_id=_binding_id(step_index=index, role=role, payload=payload),
                step_index=index,
                role=role,
                facet_refs=facets_t,
                operation_refs=ops_t,
                entity_refs=ents_t,
                mapping_refs=maps_t,
                obligation_refs=obl_t,
                structure_refs=struct_t,
            )
        )

    # Plan-level coverage vs mandatory contract (fail closed — no guessing)
    union_ops = {o for b in bindings for o in b.operation_refs}
    union_ents = {e for b in bindings for e in b.entity_refs}
    union_facets = {f for b in bindings for f in b.facet_refs}
    roles_present = {b.role for b in bindings}

    if contract.facets.UI.requirement == "MANDATORY" and ui_ops_known:
        missing_ui = sorted(ui_ops_known - union_ops)
        if missing_ui:
            return ProductStructureStepBindingResult(
                ok=False,
                code="BINDING_INCOMPLETE_UI",
                detail=f"plan union missing UI ops: {missing_ui[:12]}",
            )
        if "UI" not in union_facets and not any(b.role == "UI_PAGE" for b in bindings):
            return ProductStructureStepBindingResult(
                ok=False,
                code="BINDING_STEP_MISSING",
                detail="UI mandatory but no UI_PAGE step binding produced",
            )

    if contract.facets.API.requirement == "MANDATORY" and api_ops_known:
        if "API_BACKEND" not in roles_present:
            return ProductStructureStepBindingResult(
                ok=False,
                code="BINDING_STEP_MISSING",
                detail="API mandatory but no API_BACKEND step in PD-003B grouping",
            )
        missing_api = sorted(api_ops_known - union_ops)
        if missing_api:
            return ProductStructureStepBindingResult(
                ok=False,
                code="BINDING_INCOMPLETE_API",
                detail=f"plan union missing API ops: {missing_api[:12]}",
            )

    if contract.facets.DB.requirement == "MANDATORY" and entities_known:
        if "DB_SCHEMA" not in roles_present:
            return ProductStructureStepBindingResult(
                ok=False,
                code="BINDING_STEP_MISSING",
                detail="DB mandatory but no DB_SCHEMA step in PD-003B grouping",
            )
        missing_db = sorted(entities_known - union_ents)
        if missing_db:
            return ProductStructureStepBindingResult(
                ok=False,
                code="BINDING_INCOMPLETE_DB",
                detail=f"plan union missing DB entities: {missing_db[:12]}",
            )

    return ProductStructureStepBindingResult(
        ok=True,
        code="PRODUCT_STRUCTURE_STEPS_BOUND",
        detail=f"bound {len(bindings)} steps; ops={len(union_ops)} ents={len(union_ents)}",
        bindings=tuple(bindings),
    )


def apply_semantic_bindings_to_steps(
    *,
    steps: tuple[Any, ...] | list[Any],
    bindings: tuple[StepSemanticBindingV1, ...],
) -> tuple[Any, ...]:
    """Stamp binding refs onto StepContract-like models (model_copy)."""
    by_index = {b.step_index: b for b in bindings}
    out: list[Any] = []
    for i, step in enumerate(steps):
        b = by_index.get(i)
        if b is None:
            out.append(step)
            continue
        update = {
            "facet_refs": b.facet_refs,
            "operation_refs": b.operation_refs,
            "entity_refs": b.entity_refs,
            "mapping_refs": b.mapping_refs,
            "obligation_refs": b.obligation_refs,
            "binding_role": "IMPLEMENT" if (
                b.facet_refs or b.operation_refs or b.entity_refs
            ) else getattr(step, "binding_role", None),
        }
        if hasattr(step, "model_copy"):
            out.append(step.model_copy(update=update))
        else:
            out.append(step)
    return tuple(out)


__all__ = [
    "BINDER_VERSION",
    "StepRole",
    "StepSemanticBindingV1",
    "ProductStructureStepBindingResult",
    "compile_product_structure_step_bindings",
    "apply_semantic_bindings_to_steps",
]
