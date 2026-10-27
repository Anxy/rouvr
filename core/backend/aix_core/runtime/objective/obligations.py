"""Derive durable objective obligations from admitted user intent.

Prefers explicit substantive ``definition_of_done`` entries. Generic meta DoD
(e.g. \"scoped change complete\") is additive and never replaces real requirements
extracted from the objective text / scope model.
"""
from __future__ import annotations

import re

from backend.aix_core.runtime.objective.contracts import ObjectiveObligationV1
from backend.aix_core.runtime.objective.objective_scope import (
    is_generic_meta_dod,
)

_FACET_DOD_LABELS: frozenset[str] = frozenset(
    {
        "frontend / ui surface",
        "backend / server runtime",
        "api / endpoints",
        "database / persistence",
        "schema migration",
        "tests / verification",
        "configuration",
        "external integration",
        "documentation",
    }
)

_COORD_SPLIT = re.compile(
    r"\s*(?:"
    r",\s+and\s+(?=(?:add|make|expose|cover|implement|consume|configure|update|create|reuse|preserve)\b)"
    r"|;\s+"
    r"|\s+and then\s+"
    r"|,\s+(?=(?:add|make|expose|cover|implement|consume|configure|update|create|reuse|preserve)\b)"
    r"|\s+and\s+(?=(?:expose|cover|add\s+tests?|make|preserve|implement)\b)"
    r")",
    re.IGNORECASE,
)
_ATOMIC_WHILE = re.compile(
    r"\bwhile preserving\b|\bwithout (?:changing|modifying|touching)\b|\bpreserving all other\b",
    re.IGNORECASE,
)
_ATOMIC_READONLY = re.compile(
    r"\b(?:do not (?:modify|change|edit|touch|write)|read[- ]only|"
    r"observation only|docs[- ]only|without (?:writing|mutating))\b",
    re.IGNORECASE,
)
_CONFIG_RE = re.compile(
    r"\b(configur(?:able|ation|e)|timeout|retry limit|setting|flag)\b", re.IGNORECASE
)
_SERVICE_RE = re.compile(
    r"\b(service|implement(?:ation)?|consum(?:e|es|ed)|core behavior)\b", re.IGNORECASE
)
_API_RE = re.compile(
    r"\b(api|endpoint|router|route|http)\b", re.IGNORECASE
)
_TEST_RE = re.compile(
    r"\b(tests?|coverage|pytest|proving (?:the )?(?:configured )?behavior)\b", re.IGNORECASE
)
_PRESERVE_IFACE_RE = re.compile(
    r"\bpreserv(?:e|ing) (?:the )?(?:existing )?(?:public )?(?:interface|api|signatures?|method names?)\b",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"\b(add|make|expose|cover|implement|consume|configure|update|create|change|retry|remove|extract|group|refactor)\b",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip(" .;")


def _objective_echo_dod(dod_text: str, objective: str) -> bool:
    """DoD that repeats the objective verbatim is not an independent requirement list."""
    return _clean(dod_text).casefold() == _clean(objective).casefold()


def _verification_for(role: str | None, text: str) -> str:
    if role == "tests":
        return "Run tests that prove the requested behavior"
    if role == "configuration":
        return "Verify the configuration key exists and is the value the service reads"
    if role == "api":
        return "Verify the API/router exposes the requested behavior"
    if role == "service":
        return "Verify the service consumes and implements the requested behavior"
    if role == "preserve_interface":
        return "Verify the existing public interface is unchanged"
    if role in {"frontend", "backend", "database", "migration", "integration", "files"}:
        return f"Verify required scope facet is delivered: {_clean(text)}"
    return f"Verify: {_clean(text)}"


def _looks_atomic(text: str) -> bool:
    lowered = text.casefold()
    if _ATOMIC_READONLY.search(text):
        return True
    if _PRESERVE_IFACE_RE.search(text) and (
        _CONFIG_RE.search(text)
        or _SERVICE_RE.search(text)
        or _API_RE.search(text)
        or len(_ACTION_RE.findall(text)) >= 1
    ):
        return False
    if _ATOMIC_WHILE.search(text) and lowered.count(" and ") <= 1:
        return True
    if _ACTION_RE.findall(text) and len(_ACTION_RE.findall(text)) <= 1:
        return True
    clauses = [c for c in _COORD_SPLIT.split(text) if _clean(c)]
    return len(clauses) <= 1


def _role(text: str) -> str | None:
    if _TEST_RE.search(text):
        return "tests"
    if _PRESERVE_IFACE_RE.search(text):
        return "preserve_interface"
    if _API_RE.search(text) and not _CONFIG_RE.search(text):
        return "api"
    if _CONFIG_RE.search(text) and not _SERVICE_RE.search(text):
        return "configuration"
    if _SERVICE_RE.search(text):
        return "service"
    if _CONFIG_RE.search(text):
        return "configuration"
    return None


def split_behavioral_requirements(text: str) -> tuple[str, ...]:
    """Split a dense user sentence into independently verifiable behaviors."""
    source = _clean(text)
    if not source:
        return ()
    # Read-only / do-not-modify stays one obligation even if the sentence
    # mentions configuration and tests (inventory, not extra DoDs).
    if _ATOMIC_READONLY.search(source):
        return (source,)
    if _looks_atomic(source) and not (
        _CONFIG_RE.search(source) and _TEST_RE.search(source)
    ):
        return (source,)

    raw_parts = [_clean(p) for p in _COORD_SPLIT.split(source) if _clean(p)]
    if len(raw_parts) <= 1:
        roles: list[tuple[str, str]] = []
        if _CONFIG_RE.search(source):
            roles.append(("configuration", "Configuration supports the requested setting"))
        if _SERVICE_RE.search(source) or (
            "consum" in source.casefold() or "make the service" in source.casefold()
        ):
            roles.append(("service", "Service consumes and implements the requested behavior"))
        if _API_RE.search(source):
            roles.append(("api", "API/router exposes the requested behavior"))
        if _TEST_RE.search(source):
            roles.append(("tests", "Tests prove the requested behavior"))
        if _PRESERVE_IFACE_RE.search(source) and any(r[0] != "preserve_interface" for r in roles):
            roles.append(("preserve_interface", "Preserve the existing public interface"))
        if len(roles) >= 2:
            return tuple(desc for _role_name, desc in roles)
        return (source,)

    merged: list[str] = []
    seen_roles: set[str] = set()
    for part in raw_parts:
        if _ATOMIC_WHILE.search(part) and merged:
            merged[-1] = f"{merged[-1]} {part}".strip()
            continue
        role = _role(part) or f"clause:{part.casefold()[:40]}"
        if role in seen_roles:
            if merged:
                merged[-1] = f"{merged[-1]}; {part}"
            else:
                merged.append(part)
            continue
        seen_roles.add(role)
        merged.append(part)
    if len(merged) >= 2:
        return tuple(merged)
    return (source,)


def _append_unique(
    obligations: list[ObjectiveObligationV1],
    *,
    objective_id: str,
    description: str,
    source: str,
    role: str | None,
    seen: set[str],
    acceptance_criteria: tuple[str, ...] = (),
) -> None:
    key = description.casefold()
    if key in seen:
        return
    seen.add(key)
    obligations.append(
        ObjectiveObligationV1(
            objective_id=objective_id,
            description=description,
            required=True,
            source=source,  # type: ignore[arg-type]
            verification_strategy=_verification_for(role, description),
            acceptance_criteria=acceptance_criteria,
        )
    )


def derive_obligations(
    *, objective_id: str, objective: str, definition_of_done: tuple[str, ...]
) -> tuple[ObjectiveObligationV1, ...]:
    """Build the canonical obligation set for a just-admitted objective.

    - Substantive DoD entries are kept.
    - Generic meta DoD never replaces objective-derived requirements (additive only).
    - Behavioral splits from the objective preserve user wording.
    - Keyword/facet labels are not minted as binding WHAT (K13).
    """
    substantive_dod: list[str] = []
    meta_dod: list[str] = []
    for raw in definition_of_done:
        text = str(raw or "").strip()
        if not text:
            continue
        if is_generic_meta_dod(text):
            if text.casefold() not in {m.casefold() for m in meta_dod}:
                meta_dod.append(text)
            continue
        if " ".join(text.casefold().split()) in _FACET_DOD_LABELS:
            # Client/facet DoD is not binding WHAT (K13).
            continue
        if _objective_echo_dod(text, str(objective or "")):
            # Admission often mirrors objective into DoD when the client has no
            # separate checklist. That echo must not collapse multi-clause objectives
            # into one substantive obligation — behavioral split handles them.
            continue
        if text.casefold() not in {s.casefold() for s in substantive_dod}:
            substantive_dod.append(text)

    obligations: list[ObjectiveObligationV1] = []
    seen: set[str] = set()

    for text in substantive_dod:
        _append_unique(
            obligations,
            objective_id=objective_id,
            description=text,
            source="definition_of_done",
            role=_role(text),
            seen=seen,
        )

    behavioral = split_behavioral_requirements(str(objective or "").strip())
    # K13: keyword/facet labels are not binding WHAT. User scope lives on Understanding.
    scope_reqs: tuple[object, ...] = ()
    enforceable_scope = [
        r
        for r in scope_reqs
        if r.kind
        in {
            "frontend",
            "backend",
            "database",
            "api",
            "tests",
            "migration",
            "config",
            "integration",
            "files",
        }
    ]
    # Multi-facet product surfaces (not config/tests alone) keep durable facet obligations.
    product_facets = [
        r
        for r in enforceable_scope
        if r.kind
        in {
            "frontend",
            "backend",
            "database",
            "api",
            "migration",
            "integration",
            "files",
        }
    ]
    multi_facet = len(product_facets) >= 2

    # Prefer fine-grained behavioral clauses when the objective decomposes cleanly
    # and DoD did not already supply substantive product asks.
    # Exception: multi-facet product scope (e.g. frontend+backend+database) must keep
    # durable facet obligations — sentence splits must not demote them.
    if not substantive_dod and len(behavioral) >= 2 and not multi_facet:
        for part in behavioral:
            _append_unique(
                obligations,
                objective_id=objective_id,
                description=part,
                source="objective_text",
                role=_role(part),
                seen=seen,
            )
        part_blob = " ".join(behavioral).casefold()
        for req in scope_reqs:
            if req.kind in {"constraint", "config", "tests", "api", "backend", "service"}:
                # Likely already represented by behavioral clauses.
                if any(t in part_blob for t in req.evidence_terms[:5]):
                    continue
            if req.kind in {"frontend", "database", "migration", "files", "integration"}:
                if any(t in part_blob for t in req.evidence_terms[:5]):
                    continue
                _append_unique(
                    obligations,
                    objective_id=objective_id,
                    description=req.label,
                    source="objective_text",
                    role=req.kind,
                    seen=seen,
                    acceptance_criteria=(f"Plan and execution must deliver: {req.label}",),
                )
    else:
        for req in scope_reqs:
            if req.kind == "constraint":
                continue
            if any(
                req.kind in d.casefold()
                or any(t in d.casefold() for t in req.evidence_terms[:3])
                for d in substantive_dod
            ):
                continue
            _append_unique(
                obligations,
                objective_id=objective_id,
                description=req.label,
                source="objective_text",
                role=req.kind,
                seen=seen,
                acceptance_criteria=(f"Plan and execution must deliver: {req.label}",),
            )

        # Single atomic objective: prefer the user's exact wording over a lone
        # coarse scope label. Keep multi-facet scope sets (FULL_STACK etc.).
        if len(behavioral) == 1 and not substantive_dod:
            exact = behavioral[0]
            if exact and len(enforceable_scope) < 2:
                if obligations and all(
                    o.description in {r.label for r in scope_reqs} for o in obligations
                ):
                    obligations.clear()
                    seen.clear()
                _append_unique(
                    obligations,
                    objective_id=objective_id,
                    description=exact,
                    source="objective_text",
                    role=_role(exact),
                    seen=seen,
                )

        if not obligations and behavioral:
            for part in behavioral:
                _append_unique(
                    obligations,
                    objective_id=objective_id,
                    description=part,
                    source="objective_text",
                    role=_role(part),
                    seen=seen,
                )

    for text in meta_dod:
        _append_unique(
            obligations,
            objective_id=objective_id,
            description=text,
            source="definition_of_done",
            role=None,
            seen=seen,
        )

    return tuple(obligations)


__all__ = ["derive_obligations", "split_behavioral_requirements"]
