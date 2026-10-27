"""AIX-DoD-002E — Semantic binding: does this step substantiate its claimed obligations?

Pure control plane: no LLM, no decomposition (002D), no catch-all package (002F).

Verdicts:
  LEGITIMATE      — claim is evidenced by purpose / acceptance / specific scope tokens
  UNSUBSTANTIATED — claim rests on generic file/facet labels only (smoking-gun stamp)
  SCOPE_MISMATCH  — claim facet contradicts step scope with no purpose rescue
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from backend.aix_core.runtime.objective.contracts import StepContractV1
from backend.aix_core.runtime.objective.outcome_contract import OutcomeContractV1
from backend.aix_core.runtime.objective.outcome_obligations import sanitize_obligation_token

BindingVerdict = Literal["LEGITIMATE", "UNSUBSTANTIATED", "SCOPE_MISMATCH"]

_GENERIC_UI = frozenset(
    {
        "ui",
        "frontend",
        "view",
        "views",
        "page",
        "pages",
        "html",
        "css",
        "js",
        "tsx",
        "jsx",
        "index",
        "app",
        "styles",
    }
)
_GENERIC_API = frozenset(
    {"api", "backend", "endpoint", "endpoints", "router", "routers", "php", "server"}
)
_GENERIC_DB = frozenset(
    {"database", "schema", "migration", "migrations", "sql", "mysql", "postgres", "db"}
)
_GENERIC_ALL = _GENERIC_UI | _GENERIC_API | _GENERIC_DB | frozenset(
    {"implement", "build", "create", "complete", "entire", "whole", "system", "product", "application"}
)

_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class BindingJudgment:
    step_id: str
    claim_kind: str
    claim_ref: str
    verdict: BindingVerdict
    detail: str


@dataclass(frozen=True)
class SemanticBindingResult:
    ok: bool
    code: str
    detail: str
    judgments: tuple[BindingJudgment, ...] = ()


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(str(text or "").casefold()))


def _ref_tokens(ref: str) -> set[str]:
    """Distinctive tokens from an operation/entity/obligation ref."""
    raw = str(ref or "").casefold()
    # obl_ui_register_student → register, student
    if raw.startswith("obl_"):
        parts = raw.split("_")
        # drop obl, facet prefixes
        skip = {"obl", "ui", "api", "db", "auth", "actor", "perm", "map"}
        return {p for p in parts if p and p not in skip and len(p) > 1}
    parts = re.split(r"[.\-_/]+", raw)
    out = {p for p in parts if p and len(p) > 1}
    # also whole sanitized
    tok = sanitize_obligation_token(raw)
    if tok and tok not in {"unnamed"}:
        out.add(tok)
        out.update(t for t in tok.split("_") if len(t) > 1)
    return out


def _step_blob(step: StepContractV1) -> str:
    bits = [
        str(step.purpose or ""),
        " ".join(str(x) for x in (step.acceptance_criteria or ())),
        " ".join(str(x) for x in (step.target_scope or ())),
        " ".join(str(x) for x in (step.obligation_refs or ())),
    ]
    return " ".join(bits).casefold()


def _scope_roles(paths: tuple[str, ...] | list[str]) -> set[str]:
    roles: set[str] = set()
    for path in paths:
        p = str(path or "").casefold()
        if any(p.endswith(suf) for suf in (".html", ".css", ".js", ".tsx", ".jsx")):
            roles.add("UI")
        if p.endswith(".php") or "/api" in p or p.endswith("api.impl"):
            roles.add("API")
        if p.endswith(".sql") or "database" in p or p.endswith("persistence.impl") or "/db_" in p:
            roles.add("DB")
        if "obligation_seed/ui_" in p:
            roles.add("UI")
        if "obligation_seed/api_" in p:
            roles.add("API")
        if "obligation_seed/db_" in p or "obligation_seed/auth" in p:
            roles.add("DB" if "/db_" in p else "AUTH")
    return roles


def _substantiates(blob: str, ref: str, *, generic_ok: frozenset[str]) -> BindingVerdict | None:
    """Return LEGITIMATE if substantiated, None if not yet decided."""
    blob_tokens = _tokens(blob)
    ref_toks = _ref_tokens(ref)
    distinctive = {t for t in ref_toks if t not in _GENERIC_ALL and t not in generic_ok}
    if not distinctive:
        distinctive = set(ref_toks)

    # Full ref / sanitized id appears as contiguous token sequence in blob
    compact = sanitize_obligation_token(ref)
    if compact and compact in sanitize_obligation_token(blob):
        # avoid matching only via generic path suffixes
        if compact not in generic_ok and compact not in {"html", "php", "sql", "index", "app"}:
            return "LEGITIMATE"

    # All distinctive tokens present (e.g. register + student)
    if distinctive and distinctive.issubset(blob_tokens):
        return "LEGITIMATE"

    # Majority of distinctive tokens (≥2) when ref is multi-token
    if len(distinctive) >= 2:
        hit = distinctive & blob_tokens
        if len(hit) >= 2:
            return "LEGITIMATE"

    return None


def judge_operation_claim(
    *,
    step: StepContractV1,
    operation: str,
    facet: Literal["UI", "API"],
) -> BindingJudgment:
    blob = _step_blob(step)
    generic = _GENERIC_UI if facet == "UI" else _GENERIC_API
    hit = _substantiates(blob, operation, generic_ok=generic)
    if hit == "LEGITIMATE":
        return BindingJudgment(
            step_id=step.id,
            claim_kind=f"{facet}_OPERATION",
            claim_ref=operation,
            verdict="LEGITIMATE",
            detail="purpose/acceptance/scope substantiates operation",
        )

    roles = _scope_roles(step.target_scope)
    # Generic file/facet labels only → unsubstantiated (smoking gun)
    if any(tok in blob for tok in generic) or facet in roles:
        return BindingJudgment(
            step_id=step.id,
            claim_kind=f"{facet}_OPERATION",
            claim_ref=operation,
            verdict="UNSUBSTANTIATED",
            detail=(
                f"operation {operation!r} claimed without specific substantiation "
                f"(generic {facet} / file labels are not enough)"
            ),
        )

    if roles and facet not in roles and hit is None:
        return BindingJudgment(
            step_id=step.id,
            claim_kind=f"{facet}_OPERATION",
            claim_ref=operation,
            verdict="SCOPE_MISMATCH",
            detail=f"operation {operation!r} facet {facet} conflicts with scope roles {sorted(roles)}",
        )

    return BindingJudgment(
        step_id=step.id,
        claim_kind=f"{facet}_OPERATION",
        claim_ref=operation,
        verdict="UNSUBSTANTIATED",
        detail=f"operation {operation!r} not evidenced on step",
    )


def judge_entity_claim(*, step: StepContractV1, entity: str) -> BindingJudgment:
    blob = _step_blob(step)
    hit = _substantiates(blob, entity, generic_ok=_GENERIC_DB)
    if hit == "LEGITIMATE":
        return BindingJudgment(
            step_id=step.id,
            claim_kind="DB_ENTITY",
            claim_ref=entity,
            verdict="LEGITIMATE",
            detail="purpose/acceptance/scope substantiates entity",
        )
    # Persistence unit: purpose names persistence and only DB-ish claims
    purpose = str(step.purpose or "").casefold()
    if "persist" in purpose or "database" in purpose or "schema" in purpose:
        facets = {str(f).upper() for f in (step.facet_refs or ())}
        if not facets or facets <= {"DB", "INTEGRATION"}:
            return BindingJudgment(
                step_id=step.id,
                claim_kind="DB_ENTITY",
                claim_ref=entity,
                verdict="LEGITIMATE",
                detail="persistence-shaped step substantiates DB entity",
            )
    roles = _scope_roles(step.target_scope)
    if roles and "DB" not in roles and "AUTH" not in roles:
        return BindingJudgment(
            step_id=step.id,
            claim_kind="DB_ENTITY",
            claim_ref=entity,
            verdict="SCOPE_MISMATCH",
            detail=f"entity {entity!r} claimed outside DB-capable scope {sorted(roles)}",
        )
    return BindingJudgment(
        step_id=step.id,
        claim_kind="DB_ENTITY",
        claim_ref=entity,
        verdict="UNSUBSTANTIATED",
        detail=f"entity {entity!r} not evidenced on step",
    )


def judge_obligation_id_claim(*, step: StepContractV1, obligation_id: str) -> BindingJudgment:
    """obl_* refs: legitimate when id or distinctive tokens appear in step evidence."""
    blob = _step_blob(step)
    oid = str(obligation_id or "")
    if oid and oid.casefold() in blob:
        return BindingJudgment(
            step_id=step.id,
            claim_kind="OBLIGATION_ID",
            claim_ref=oid,
            verdict="LEGITIMATE",
            detail="obligation_id present in step evidence bag",
        )
    hit = _substantiates(blob, oid, generic_ok=_GENERIC_ALL)
    if hit == "LEGITIMATE":
        return BindingJudgment(
            step_id=step.id,
            claim_kind="OBLIGATION_ID",
            claim_ref=oid,
            verdict="LEGITIMATE",
            detail="purpose/acceptance/scope substantiates obligation_id",
        )
    return BindingJudgment(
        step_id=step.id,
        claim_kind="OBLIGATION_ID",
        claim_ref=oid,
        verdict="UNSUBSTANTIATED",
        detail=f"obligation_id {oid!r} not substantiated",
    )


def evaluate_semantic_bindings(
    *,
    outcome_contract: OutcomeContractV1,
    steps: tuple[StepContractV1, ...] | list[StepContractV1],
) -> SemanticBindingResult:
    """Validate every claimed operation/entity/obligation_ref on steps."""
    ui_ops = set(outcome_contract.facets.UI.required_operations)
    api_ops = {op.id for op in outcome_contract.facets.API.required_operations}
    entities = set(outcome_contract.facets.DB.entities)

    judgments: list[BindingJudgment] = []
    for step in steps:
        for ref in step.operation_refs:
            ref_s = str(ref)
            if ref_s in ui_ops:
                judgments.append(judge_operation_claim(step=step, operation=ref_s, facet="UI"))
            elif ref_s in api_ops:
                judgments.append(judge_operation_claim(step=step, operation=ref_s, facet="API"))
            else:
                # unknown handled by coverage gate; skip here
                continue
        for ent in step.entity_refs:
            ent_s = str(ent)
            if ent_s in entities:
                judgments.append(judge_entity_claim(step=step, entity=ent_s))
        for oid in step.obligation_refs:
            oid_s = str(oid)
            if oid_s.startswith("obl_"):
                judgments.append(judge_obligation_id_claim(step=step, obligation_id=oid_s))

    bad = [j for j in judgments if j.verdict != "LEGITIMATE"]
    if not bad:
        return SemanticBindingResult(
            ok=True,
            code="CONTRACT_BINDING_OK",
            detail=f"all {len(judgments)} declared bindings are legitimate",
            judgments=tuple(judgments),
        )

    scope_bad = [j for j in bad if j.verdict == "SCOPE_MISMATCH"]
    if scope_bad:
        return SemanticBindingResult(
            ok=False,
            code="CONTRACT_BINDING_SCOPE_MISMATCH",
            detail="; ".join(j.detail for j in scope_bad[:5]),
            judgments=tuple(judgments),
        )
    return SemanticBindingResult(
        ok=False,
        code="CONTRACT_BINDING_UNSUBSTANTIATED",
        detail="; ".join(j.detail for j in bad[:5]),
        judgments=tuple(judgments),
    )


def narrow_bind_steps_to_contract(
    *,
    steps: tuple[Any, ...] | list[Any],
    contract: OutcomeContractV1,
) -> tuple[Any, ...]:
    """DoD-002E replacement body for bind_steps_to_contract.

    - Always stamps outcome_contract_id / hash
    - Preserves declared facet/operation/entity/mapping refs
    - Never dumps all UI/API/DB ops onto a step based on file suffix alone
    - Optional: if refs empty and purpose uniquely names exactly one known op, bind that one
    """
    ui_ops = list(contract.facets.UI.required_operations)
    api_ops = [op.id for op in contract.facets.API.required_operations]
    entities = list(contract.facets.DB.entities)
    ui_maps = {
        m.from_id: m.id
        for m in contract.facets.INTEGRATION.mappings
        if m.from_facet == "UI" and m.to_facet == "API"
    }
    api_maps = {
        m.from_id: m.id
        for m in contract.facets.INTEGRATION.mappings
        if m.from_facet == "API" and m.to_facet == "DB"
    }

    bound: list[Any] = []
    for step in steps:
        existing_ops = tuple(str(x) for x in (getattr(step, "operation_refs", ()) or ()) if str(x).strip())
        existing_ents = tuple(str(x) for x in (getattr(step, "entity_refs", ()) or ()) if str(x).strip())
        existing_facets = tuple(str(x) for x in (getattr(step, "facet_refs", ()) or ()) if str(x).strip())
        existing_maps = tuple(str(x) for x in (getattr(step, "mapping_refs", ()) or ()) if str(x).strip())

        purpose = str(getattr(step, "purpose", "") or "").casefold()
        acceptance = " ".join(str(x) for x in (getattr(step, "acceptance_criteria", ()) or ())).casefold()
        scope = " ".join(str(p) for p in (getattr(step, "target_scope", ()) or ())).casefold()
        blob = f"{purpose} {acceptance} {scope}"

        facet_refs = list(existing_facets)
        operation_refs = list(existing_ops)
        entity_refs = list(existing_ents)
        mapping_refs = list(existing_maps)

        if not operation_refs and not entity_refs:
            # Unique single-op inference only (never all ops)
            matched_ui = [op for op in ui_ops if sanitize_obligation_token(op) in sanitize_obligation_token(blob)]
            matched_api = [op for op in api_ops if sanitize_obligation_token(op) in sanitize_obligation_token(blob)]
            matched_ent = [e for e in entities if sanitize_obligation_token(e) in sanitize_obligation_token(blob)]
            if len(matched_ui) == 1 and not matched_api:
                operation_refs.append(matched_ui[0])
                facet_refs.append("UI")
                mid = ui_maps.get(matched_ui[0])
                if mid:
                    mapping_refs.append(mid)
            elif len(matched_api) == 1 and not matched_ui:
                operation_refs.append(matched_api[0])
                facet_refs.append("API")
                mid = api_maps.get(matched_api[0])
                if mid:
                    mapping_refs.append(mid)
            elif len(matched_ent) >= 1 and not matched_ui and not matched_api:
                entity_refs.extend(matched_ent)
                facet_refs.append("DB")
            # else: leave unbound — CoverageGate / semantic binding will fail closed

        update = {
            "outcome_contract_id": contract.contract_id,
            "outcome_contract_hash": contract.contract_hash,
            "facet_refs": tuple(dict.fromkeys(facet_refs)),
            "operation_refs": tuple(dict.fromkeys(operation_refs)),
            "entity_refs": tuple(dict.fromkeys(entity_refs)),
            "mapping_refs": tuple(dict.fromkeys(mapping_refs)),
            "binding_role": (
                getattr(step, "binding_role", None)
                or ("IMPLEMENT" if (facet_refs or operation_refs or entity_refs) else None)
            ),
        }
        if hasattr(step, "model_copy"):
            bound.append(step.model_copy(update=update))
        else:
            bound.append(step)
    return tuple(bound)


__all__ = [
    "BindingVerdict",
    "BindingJudgment",
    "SemanticBindingResult",
    "judge_operation_claim",
    "judge_entity_claim",
    "judge_obligation_id_claim",
    "evaluate_semantic_bindings",
    "narrow_bind_steps_to_contract",
]
