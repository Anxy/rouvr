"""AIX-DoD-002D — Deterministic obligation → coherent StepContract candidates.

Pure control plane: no LLM, no I/O, no binder, no CoverageGate.

Given mandatory OutcomeContractObligationV1 rows (002A), emit coherent
implementation units. UI→API INTEGRATION mappings form multi-facet steps;
DB entities form an explicit persistence unit; AUTH forms an auth unit.

Invariant: every UI / API / DB / AUTH obligation appears on exactly one step
(mappings may attach to the step that implements their endpoints).
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from backend.aix_core.runtime.objective.outcome_obligations import (
    OutcomeContractObligationV1,
    sanitize_obligation_token,
)


class ObligationStepCandidateV1(BaseModel):
    """One coherent implementation unit derived from sealed obligations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    step_key: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    obligation_refs: tuple[str, ...] = Field(min_length=1)
    facet_refs: tuple[str, ...] = ()
    operation_refs: tuple[str, ...] = ()
    entity_refs: tuple[str, ...] = ()
    mapping_refs: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = Field(min_length=1)
    # Logical create destinations — not repository evidence; materializer owns grounding.
    target_scope_hints: tuple[str, ...] = Field(min_length=1)
    depends_on_step_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObligationStepSeedResult:
    ok: bool
    code: str
    detail: str
    steps: tuple[ObligationStepCandidateV1, ...] = ()
    covered_obligation_ids: tuple[str, ...] = ()
    uncovered_obligation_ids: tuple[str, ...] = ()


def _humanize(ref: str) -> str:
    text = str(ref or "").replace(".", " ").replace("_", " ").strip()
    return text or "capability"


def _scope_hint(kind: str, ref: str) -> str:
    token = sanitize_obligation_token(ref) or "unit"
    return f"proposed/obligation_seed/{kind}_{token}.impl"


def compile_obligation_step_seed(
    obligations: tuple[OutcomeContractObligationV1, ...] | list[OutcomeContractObligationV1],
) -> ObligationStepSeedResult:
    """Compile coherent candidate steps from mandatory outcome obligations."""
    obl = tuple(o for o in (obligations or ()) if isinstance(o, OutcomeContractObligationV1))
    if not obl:
        return ObligationStepSeedResult(
            ok=False,
            code="OBLIGATIONS_REQUIRED",
            detail="outcome contract obligations are required for coherent step seed",
        )

    by_id = {o.obligation_id: o for o in obl}
    ui = sorted((o for o in obl if o.kind == "UI_OPERATION"), key=lambda o: o.ref)
    api = sorted((o for o in obl if o.kind == "API_OPERATION"), key=lambda o: o.ref)
    db = sorted((o for o in obl if o.kind == "DB_ENTITY"), key=lambda o: o.ref)
    auth = sorted(
        (o for o in obl if o.kind in {"AUTH_ACTOR", "AUTH_PERMISSION"}),
        key=lambda o: (o.kind, o.ref),
    )
    maps = sorted((o for o in obl if o.kind == "INTEGRATION_MAPPING"), key=lambda o: o.ref)

    ui_to_api: list[OutcomeContractObligationV1] = []
    api_to_db: list[OutcomeContractObligationV1] = []
    other_maps: list[OutcomeContractObligationV1] = []
    for m in maps:
        if m.from_facet == "UI" and m.to_facet == "API":
            ui_to_api.append(m)
        elif m.from_facet == "API" and m.to_facet == "DB":
            api_to_db.append(m)
        else:
            other_maps.append(m)

    api_by_ref = {o.ref: o for o in api}
    used_api: set[str] = set()
    used_maps: set[str] = set()
    steps: list[ObligationStepCandidateV1] = []

    for ui_obl in ui:
        linked_maps = [m for m in ui_to_api if m.from_id == ui_obl.ref]
        linked_apis: list[OutcomeContractObligationV1] = []
        for m in linked_maps:
            api_obl = api_by_ref.get(str(m.to_id or ""))
            if api_obl is not None:
                linked_apis.append(api_obl)
                used_api.add(api_obl.obligation_id)
            used_maps.add(m.obligation_id)

        refs = [ui_obl.obligation_id]
        refs.extend(a.obligation_id for a in linked_apis)
        refs.extend(m.obligation_id for m in linked_maps)
        # Deduplicate preserving order
        refs_t = tuple(dict.fromkeys(refs))

        facets = ["UI"] + (["API"] if linked_apis else [])
        if linked_maps:
            facets.append("INTEGRATION")
        ops = (ui_obl.ref,) + tuple(a.ref for a in linked_apis)
        map_refs = tuple(m.ref for m in linked_maps)
        purpose = f"IMPLEMENT {_humanize(ui_obl.ref)}"
        if linked_apis:
            purpose = f"IMPLEMENT {_humanize(ui_obl.ref)} with {_humanize(linked_apis[0].ref)}"

        acceptance = list(ui_obl.acceptance) or [f"UI operation {ui_obl.ref} is implemented"]
        for a in linked_apis:
            acceptance.extend(a.acceptance or (f"API operation {a.ref} is implemented",))
        acceptance = list(dict.fromkeys(str(x) for x in acceptance if str(x).strip()))

        step_key = f"ui_{sanitize_obligation_token(ui_obl.ref)}"
        steps.append(
            ObligationStepCandidateV1(
                step_key=step_key,
                purpose=purpose,
                obligation_refs=refs_t,
                facet_refs=tuple(dict.fromkeys(facets)),
                operation_refs=tuple(dict.fromkeys(ops)),
                mapping_refs=map_refs,
                acceptance_criteria=tuple(acceptance) or (f"Implements {ui_obl.ref}",),
                target_scope_hints=(_scope_hint("ui", ui_obl.ref),),
            )
        )

    for api_obl in api:
        if api_obl.obligation_id in used_api:
            continue
        step_key = f"api_{sanitize_obligation_token(api_obl.ref)}"
        steps.append(
            ObligationStepCandidateV1(
                step_key=step_key,
                purpose=f"IMPLEMENT {_humanize(api_obl.ref)}",
                obligation_refs=(api_obl.obligation_id,),
                facet_refs=("API",),
                operation_refs=(api_obl.ref,),
                acceptance_criteria=tuple(api_obl.acceptance)
                or (f"API operation {api_obl.ref} is implemented",),
                target_scope_hints=(_scope_hint("api", api_obl.ref),),
            )
        )
        used_api.add(api_obl.obligation_id)

    if db:
        db_refs = [o.obligation_id for o in db]
        db_entities = tuple(o.ref for o in db)
        # Attach API→DB mappings whose DB endpoint is in this persistence unit.
        persist_maps = [
            m
            for m in api_to_db
            if str(m.to_id or "") in set(db_entities) and m.obligation_id not in used_maps
        ]
        for m in persist_maps:
            used_maps.add(m.obligation_id)
        refs_t = tuple(dict.fromkeys(db_refs + [m.obligation_id for m in persist_maps]))
        acceptance = list(dict.fromkeys(
            str(x)
            for o in db
            for x in (o.acceptance or (f"DB entity {o.ref} is persisted",))
            if str(x).strip()
        ))
        ui_keys = [s.step_key for s in steps if "UI" in s.facet_refs or "API" in s.facet_refs]
        steps.append(
            ObligationStepCandidateV1(
                step_key="persistence",
                purpose="IMPLEMENT persistence",
                obligation_refs=refs_t,
                facet_refs=("DB",) + (("INTEGRATION",) if persist_maps else ()),
                entity_refs=db_entities,
                mapping_refs=tuple(m.ref for m in persist_maps),
                acceptance_criteria=tuple(acceptance) or ("Declared DB entities are persisted",),
                target_scope_hints=(_scope_hint("db", "persistence"),),
                depends_on_step_keys=tuple(ui_keys),
            )
        )

    if auth:
        auth_refs = tuple(o.obligation_id for o in auth)
        acceptance = list(dict.fromkeys(
            str(x)
            for o in auth
            for x in (o.acceptance or (f"Auth {o.ref} is declared",))
            if str(x).strip()
        ))
        steps.append(
            ObligationStepCandidateV1(
                step_key="auth",
                purpose="IMPLEMENT authentication and authorization",
                obligation_refs=auth_refs,
                facet_refs=("AUTH",),
                operation_refs=tuple(o.ref for o in auth),
                acceptance_criteria=tuple(acceptance) or ("Auth model is implemented",),
                target_scope_hints=(_scope_hint("auth", "model"),),
            )
        )

    # Leftover mappings (unattached) become their own step so nothing is dropped.
    leftover = [m for m in maps if m.obligation_id not in used_maps]
    leftover.extend(m for m in other_maps if m.obligation_id not in used_maps)
    leftover = list({m.obligation_id: m for m in leftover}.values())
    if leftover:
        steps.append(
            ObligationStepCandidateV1(
                step_key="integration_remainder",
                purpose="IMPLEMENT remaining integration mappings",
                obligation_refs=tuple(m.obligation_id for m in leftover),
                facet_refs=("INTEGRATION",),
                mapping_refs=tuple(m.ref for m in leftover),
                acceptance_criteria=("Remaining integration mappings are implemented",),
                target_scope_hints=(_scope_hint("map", "remainder"),),
            )
        )

    covered = tuple(dict.fromkeys(ref for s in steps for ref in s.obligation_refs))
    uncovered = tuple(oid for oid in by_id if oid not in set(covered))
    if uncovered:
        return ObligationStepSeedResult(
            ok=False,
            code="SEED_COVERAGE_INCOMPLETE",
            detail=f"seed left {len(uncovered)} obligations uncovered",
            steps=tuple(steps),
            covered_obligation_ids=covered,
            uncovered_obligation_ids=uncovered,
        )
    if not steps:
        return ObligationStepSeedResult(
            ok=False,
            code="SEED_EMPTY",
            detail="no coherent steps compiled from obligations",
        )

    # Deterministic: never emit a single catch-all when multiple UI ops exist.
    ui_ids = {o.obligation_id for o in ui}
    if len(ui) >= 2 and len(steps) == 1 and ui_ids.issubset(set(steps[0].obligation_refs)):
        return ObligationStepSeedResult(
            ok=False,
            code="SEED_MEGA_STEP",
            detail="compiler refused to collapse multiple UI operations into one step",
            steps=tuple(steps),
            covered_obligation_ids=covered,
        )

    return ObligationStepSeedResult(
        ok=True,
        code="OBLIGATION_STEPS_COMPILED",
        detail=f"compiled {len(steps)} coherent steps covering {len(covered)} obligations",
        steps=tuple(steps),
        covered_obligation_ids=covered,
        uncovered_obligation_ids=(),
    )


def ui_obligation_ids(
    obligations: tuple[OutcomeContractObligationV1, ...] | list[OutcomeContractObligationV1],
) -> tuple[str, ...]:
    return tuple(
        o.obligation_id
        for o in sorted(
            (x for x in obligations if isinstance(x, OutcomeContractObligationV1) and x.kind == "UI_OPERATION"),
            key=lambda o: o.ref,
        )
    )


def plan_omits_ui_obligations(
    *,
    step_obligation_refs: list[tuple[str, ...]] | tuple[tuple[str, ...], ...],
    obligations: tuple[OutcomeContractObligationV1, ...] | list[OutcomeContractObligationV1],
) -> bool:
    """True when any UI obligation is absent from every step's obligation_refs."""
    required = set(ui_obligation_ids(obligations))
    if not required:
        return False
    present: set[str] = set()
    for refs in step_obligation_refs:
        present.update(str(r) for r in refs)
    return not required.issubset(present)


def plan_is_obligation_mega_collapse(
    *,
    step_count: int,
    step_obligation_refs: list[tuple[str, ...]] | tuple[tuple[str, ...], ...],
    obligations: tuple[OutcomeContractObligationV1, ...] | list[OutcomeContractObligationV1],
    used_legacy_one_step: bool = False,
) -> bool:
    """Heuristic for 002D seed preference (not the 002F catch-all gate).

    Prefer seed when the model collapsed multiple UI ops into one unexplained step,
    or emitted legacy one-step while multiple UI obligations exist.
    """
    ui_ids = set(ui_obligation_ids(obligations))
    if len(ui_ids) < 2:
        return False
    if used_legacy_one_step:
        return True
    if step_count <= 1:
        refs = set(step_obligation_refs[0]) if step_obligation_refs else set()
        return len(ui_ids & refs) >= 2 or ui_ids.issubset(refs)
    return False


def should_prefer_obligation_seed(
    *,
    step_count: int,
    step_obligation_refs: list[tuple[str, ...]] | tuple[tuple[str, ...], ...],
    obligations: tuple[OutcomeContractObligationV1, ...] | list[OutcomeContractObligationV1],
    used_legacy_one_step: bool = False,
) -> bool:
    if plan_omits_ui_obligations(
        step_obligation_refs=step_obligation_refs, obligations=obligations
    ):
        return True
    if plan_is_obligation_mega_collapse(
        step_count=step_count,
        step_obligation_refs=step_obligation_refs,
        obligations=obligations,
        used_legacy_one_step=used_legacy_one_step,
    ):
        return True
    return False


def candidates_for_planner(
    steps: tuple[ObligationStepCandidateV1, ...] | list[ObligationStepCandidateV1],
) -> list[dict[str, object]]:
    """Compact seed for Evidence.baseline_candidate_steps."""
    out: list[dict[str, object]] = []
    for s in steps:
        out.append(
            {
                "step_key": s.step_key,
                "purpose": s.purpose,
                "obligation_refs": list(s.obligation_refs),
                "facet_refs": list(s.facet_refs),
                "operation_refs": list(s.operation_refs),
                "entity_refs": list(s.entity_refs),
                "mapping_refs": list(s.mapping_refs),
                "acceptance_criteria": list(s.acceptance_criteria),
                "target_scope_hints": list(s.target_scope_hints),
                "depends_on_step_keys": list(s.depends_on_step_keys),
            }
        )
    return out


__all__ = [
    "ObligationStepCandidateV1",
    "ObligationStepSeedResult",
    "compile_obligation_step_seed",
    "ui_obligation_ids",
    "plan_omits_ui_obligations",
    "plan_is_obligation_mega_collapse",
    "should_prefer_obligation_seed",
    "candidates_for_planner",
]
