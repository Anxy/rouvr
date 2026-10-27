"""AIX-SEM-001-W3 — Role-aware acceptance criteria for ProductStructure steps.

When a step carries nonempty operation_refs / entity_refs / mapping_refs,
acceptance criteria must include role-aware lines backed by those refs.
Empty-ref steps (mkdir / README / thin support) keep existence-only AC.

Does not invent requirements not present in refs.
"""

from __future__ import annotations

from typing import Any, Iterable

ACCEPTANCE_HELPER_VERSION = "sem001.w3.v1"
CREATE_ROOT_ACCEPTANCE = (
    "no existing repository files outside create_root are modified"
)


def _clean_refs(refs: Iterable[str] | None) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in refs or ():
        token = str(raw or "").strip()
        if not token:
            continue
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return tuple(out)


def _has_semantic_refs(
    *,
    operation_refs: Iterable[str] | None = (),
    entity_refs: Iterable[str] | None = (),
    mapping_refs: Iterable[str] | None = (),
) -> bool:
    return bool(
        _clean_refs(operation_refs)
        or _clean_refs(entity_refs)
        or _clean_refs(mapping_refs)
    )


def build_role_aware_acceptance_criteria(
    *,
    scope: Iterable[str] | None,
    role: str = "SUPPORTING",
    operation_refs: Iterable[str] | None = (),
    entity_refs: Iterable[str] | None = (),
    mapping_refs: Iterable[str] | None = (),
) -> tuple[str, ...]:
    """Build AC for one ProductStructure step.

    Always retains ``{path} exists`` + create_root constraint.
    When refs are nonempty, appends role-aware lines using only those refs.
    """
    paths = tuple(str(p).strip() for p in (scope or ()) if str(p).strip())
    existence = tuple(f"{p} exists" for p in paths)

    ops = _clean_refs(operation_refs)
    ents = _clean_refs(entity_refs)
    maps = _clean_refs(mapping_refs)

    if not (ops or ents or maps):
        return existence + (CREATE_ROOT_ACCEPTANCE,)

    role_u = str(role or "SUPPORTING").strip().upper() or "SUPPORTING"
    semantic: list[str] = []

    if ops:
        if role_u in {"UI_PAGE", "UI_ASSET"}:
            semantic.append("wires UI operations / calls API ops: " + ", ".join(ops))
        else:
            # API_BACKEND, MIXED, DB-adjacent with ops, UNKNOWN, SUPPORTING
            semantic.append("implements operations: " + ", ".join(ops))

    if ents:
        semantic.append("defines entities: " + ", ".join(ents))

    if maps:
        if role_u in {"UI_PAGE", "UI_ASSET"}:
            semantic.append("covers UI↔API mappings: " + ", ".join(maps))
        else:
            semantic.append("covers integration mappings: " + ", ".join(maps))

    return existence + tuple(semantic) + (CREATE_ROOT_ACCEPTANCE,)


def enrich_step_acceptance_from_binding(
    step: Any,
    *,
    role: str | None = None,
) -> Any:
    """Return step with role-aware AC derived from stamped refs (model_copy)."""
    if step is None:
        return step
    resolved_role = str(
        role
        or getattr(step, "binding_role", None)
        or "SUPPORTING"
    )
    # Prefer explicit planner role when provided; stamped binding_role is IMPLEMENT.
    ac = build_role_aware_acceptance_criteria(
        scope=getattr(step, "target_scope", ()) or (),
        role=resolved_role,
        operation_refs=getattr(step, "operation_refs", ()) or (),
        entity_refs=getattr(step, "entity_refs", ()) or (),
        mapping_refs=getattr(step, "mapping_refs", ()) or (),
    )
    if hasattr(step, "model_copy"):
        return step.model_copy(update={"acceptance_criteria": ac})
    return step


__all__ = [
    "ACCEPTANCE_HELPER_VERSION",
    "CREATE_ROOT_ACCEPTANCE",
    "build_role_aware_acceptance_criteria",
    "enrich_step_acceptance_from_binding",
    "_has_semantic_refs",
]
