"""P2: Understanding v1 from UserIntent + UserTurns only. No world, no Explore."""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.aix_core.runtime.objective.product_class import infer_greenfield_product_slug
from backend.aix_core.runtime.objective.provenance.contracts import (
    ClaimCite,
    GovernanceRevision,
    PlanDecision,
    UnderstandingClaim,
    UnderstandingRevision,
    UserIntent,
    UserTurn,
)
from backend.aix_core.runtime.objective.provenance.validators import (
    USER_DERIVED_KINDS,
    RuleResult,
    normalize_rel_path,
    validate_understanding_revision,
)

V1_CLAIM_KINDS: frozenset[str] = frozenset(
    {"OUTCOME", "SCOPE_IN", "SCOPE_OUT", "AMBIGUITY", "MISSING_INFO"}
)
V1_FORBIDDEN_KINDS: frozenset[str] = frozenset({"SITUATION", "CAUSE"})
_HOW_PHRASES: tuple[str, ...] = (
    "should modify",
    "should change",
    "we should modify",
    "implement steps",
    "target_scope",
    "implementation architecture",
)
_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])("
    r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\."
    r"(?:py|php|ts|tsx|js|sql|md|json|yml|yaml|html|css)"
    r"|[A-Za-z0-9_.-]+\."
    r"(?:py|php|ts|tsx|js|sql|md|json|yml|yaml|html|css)"
    r")"
)
_ONLY_SCOPE_RE = re.compile(
    r"(?i)\bonly(?:\s+the)?\s+("
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z0-9]+"
    r"|[A-Za-z0-9_.-]+\.(?:py|php|ts|tsx|js|sql|md|json|yml|yaml|html|css)"
    r"|schema|database|api"
    r")"
)
_SCOPE_OUT_RE = re.compile(
    r"(?i)\b(?:do not|don't|without(?:\s+modifying)?|except)\s+(?:the\s+)?([A-Za-z0-9_./-]+)"
)
# "Do not create mock-only..." / "Do not modify existing..." must NOT become
# SCOPE_OUT("create"|"modify") — that blocks greenfield CREATE under create_root.
_SCOPE_OUT_ACTION_WORDS: frozenset[str] = frozenset(
    {
        "create",
        "modify",
        "change",
        "update",
        "delete",
        "remove",
        "edit",
        "write",
        "touch",
        "alter",
        "add",
        "make",
        "build",
        "generate",
        "use",
        "run",
        "test",
        "include",
        "require",
        "implement",
        "refactor",
        "overwrite",
        "replace",
    }
)
# Prefer explicit path exclusions: "outside proposed/", "except vendor/", etc.
_SCOPE_OUT_OUTSIDE_RE = re.compile(
    r"(?i)\b(?:outside|beyond|except(?:\s+for)?)\s+(?:the\s+)?"
    r"([A-Za-z0-9_./-]+/?)"
)
_OPEN_RE = re.compile(
    r"(?i)\bwhatever files\b|\bany files you need\b|\bwhichever files\b|\bwhatever you need\b"
)
# Standalone "only" (Change only schema.sql). Do NOT match hyphenated
# adjectives like mock-only / production-only — those falsely BOUNDED greenfield.
_BOUNDED_RE = re.compile(r"(?i)(?<![\w-])only(?![\w-])")
# Default operational create-root name; never user SCOPE_IN in v1.
_CREATE_ROOT_NAMES: frozenset[str] = frozenset({"proposed"})
_SURFACE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("schema", re.compile(r"(?i)\bschema\b|\.sql\b|\bmysql\b")),
    ("html", re.compile(r"(?i)\bhtml\b|\bfrontend\b|\bcss\b")),
    ("php", re.compile(r"(?i)\bphp\b|\bbackend\b")),
)


@dataclass(frozen=True)
class UnderstandingV1Result:
    revision: UnderstandingRevision
    validation: RuleResult
    decision: PlanDecision


def _norm(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _has_how(text: str) -> bool:
    folded = _norm(text)
    return any(phrase in folded for phrase in _HOW_PHRASES)


def _create_root_names(governance: GovernanceRevision | None) -> frozenset[str]:
    names = set(_CREATE_ROOT_NAMES)
    if governance is not None:
        for root in governance.create_root:
            normalized = normalize_rel_path(str(root).rstrip("/"))
            if normalized:
                names.add(normalized.split("/")[0].casefold())
                names.add(normalized.casefold())
    return frozenset(names)


def _is_governance_path(text: str, roots: frozenset[str]) -> bool:
    raw = str(text or "").strip().replace("\\", "/")
    if not raw:
        return False
    lowered = raw.casefold().rstrip("/")
    if lowered in roots:
        return True
    first = lowered.split("/")[0]
    return first in roots


def _cite(kind: str, ref: str) -> ClaimCite:
    return ClaimCite(kind=kind, ref=ref)  # type: ignore[arg-type]


def _claim(
    *,
    kind: str,
    text: str,
    cites: tuple[ClaimCite, ...],
    standing: str = "ASSERTED",
    claim_id: str | None = None,
) -> UnderstandingClaim:
    kwargs: dict[str, object] = {
        "kind": kind,
        "text": text,
        "cites": cites,
        "standing": standing,
        "status": "ACTIVE",
    }
    if claim_id is not None:
        kwargs["id"] = claim_id
    return UnderstandingClaim(**kwargs)  # type: ignore[arg-type]


def _spans(intent: UserIntent, turns: tuple[UserTurn, ...]) -> tuple[tuple[str, str, str], ...]:
    out: list[tuple[str, str, str]] = [("INTENT_SPAN", intent.id, intent.text)]
    if intent.dod_user_authored:
        for item in intent.definition_of_done:
            if str(item).strip():
                out.append(("INTENT_SPAN", intent.id, str(item)))
    for turn in turns:
        out.append(("USER_TURN", turn.id, turn.text))
    return tuple(out)


def derive_scope_mode(corpus: str) -> str:
    if _OPEN_RE.search(corpus or ""):
        return "OPEN_IMPLEMENTATION"
    if _BOUNDED_RE.search(corpus or ""):
        return "BOUNDED"
    return "OPEN_IMPLEMENTATION"


def _surfaces(corpus: str) -> frozenset[str]:
    found = {name for name, pattern in _SURFACE_PATTERNS if pattern.search(corpus or "")}
    return frozenset(found)


def _create_capability_enabled(governance: GovernanceRevision | None) -> bool:
    """When Governance is absent, default operational policy allows CREATE under create_root."""
    if governance is None:
        return True
    return bool(governance.create_capability)


def _path_like(text: str) -> bool:
    return "/" in text or "." in text


def _extract_drafts(
    *,
    intent: UserIntent,
    turns: tuple[UserTurn, ...],
    governance: GovernanceRevision | None,
) -> tuple[str, list[UnderstandingClaim]]:
    roots = _create_root_names(governance)
    drafts: list[UnderstandingClaim] = []
    seen: set[tuple[str, str]] = set()
    # User-named paths under create_root are CREATE permissions targets, never SCOPE_IN (U3).
    # They still answer "which artifacts?" for greenfield CREATE so we must not mint MISSING_INFO.
    named_create_paths: set[str] = set()

    def add(claim: UnderstandingClaim) -> None:
        key = (claim.kind, _norm(claim.text))
        if key in seen or _has_how(claim.text):
            return
        seen.add(key)
        drafts.append(claim)

    outcome = _claim(
        kind="OUTCOME",
        text=intent.text.strip(),
        cites=(_cite("INTENT_SPAN", intent.id),),
    )
    add(outcome)

    for cite_kind, cite_ref, text in _spans(intent, turns):
        cite = _cite(cite_kind, cite_ref)
        for match in _PATH_RE.finditer(text):
            path = match.group(1)
            if _is_governance_path(path, roots):
                if _path_like(path):
                    named_create_paths.add(normalize_rel_path(path) or path)
                continue
            add(_claim(kind="SCOPE_IN", text=path, cites=(cite,)))
        for match in _ONLY_SCOPE_RE.finditer(text):
            span = match.group(1).strip()
            if _is_governance_path(span, roots):
                if _path_like(span):
                    named_create_paths.add(normalize_rel_path(span) or span)
                continue
            add(_claim(kind="SCOPE_IN", text=span, cites=(cite,)))
        for match in _SCOPE_OUT_OUTSIDE_RE.finditer(text):
            span = match.group(1).strip(" .")
            if not span:
                continue
            # "outside proposed/" reinforces create_root — not a SCOPE_OUT ban.
            if _is_governance_path(span, roots):
                continue
            add(_claim(kind="SCOPE_OUT", text=span.rstrip("/"), cites=(cite,)))
        for match in _SCOPE_OUT_RE.finditer(text):
            span = match.group(1).strip(" .")
            if not span:
                continue
            token = span.casefold().rstrip("/")
            if token in _SCOPE_OUT_ACTION_WORDS:
                continue
            if _is_governance_path(span, roots):
                continue
            add(_claim(kind="SCOPE_OUT", text=span, cites=(cite,)))

    corpus = "\n".join(span[2] for span in _spans(intent, turns))
    scope_mode = derive_scope_mode(corpus)
    surfaces = _surfaces(corpus)
    only_spans = {_norm(m.group(1)) for m in _ONLY_SCOPE_RE.finditer(corpus)}
    only_is_schema = any("schema" in item or item.endswith(".sql") for item in only_spans)
    if only_spans and only_is_schema and (surfaces & {"html", "php"}):
        add(
            _claim(
                kind="AMBIGUITY",
                text="Exclusive schema scope vs HTML/PHP product surfaces",
                cites=(_cite("INTENT_SPAN", intent.id),),
            )
        )

    has_ambiguity = any(c.kind == "AMBIGUITY" for c in drafts)
    has_named_scope = any(
        c.kind == "SCOPE_IN" and _path_like(c.text) for c in drafts
    )
    slug = infer_greenfield_product_slug(objective=intent.text)
    if slug and _create_capability_enabled(governance):
        # Delivery location under create_root — not SCOPE_IN (U3).
        named_create_paths.add(f"proposed/{slug}/")
    has_named_create = _create_capability_enabled(governance) and bool(named_create_paths)
    if not has_ambiguity and not has_named_scope and not has_named_create:
        add(
            _claim(
                kind="MISSING_INFO",
                text="Which files or artifacts implement this outcome?",
                cites=(_cite("CLAIM", outcome.id),),
            )
        )
    return scope_mode, drafts


def _reconcile_with_parent(
    *,
    parent: UnderstandingRevision,
    drafts: tuple[UnderstandingClaim, ...],
    intent: UserIntent,
    scope_mode: str,
) -> UnderstandingRevision:
    used: set[str] = set()
    child: list[UnderstandingClaim] = []
    for draft in drafts:
        match = next(
            (
                prior
                for prior in parent.claims
                if prior.id not in used
                and prior.kind == draft.kind
                and _norm(prior.text) == _norm(draft.text)
            ),
            None,
        )
        if match is not None:
            used.add(match.id)
            if match.status == "ACTIVE":
                child.append(match)
            else:
                child.append(match.model_copy(update={"status": "ACTIVE"}))
        else:
            child.append(draft)
    for prior in parent.claims:
        if prior.id in used:
            continue
        if prior.status == "ACTIVE" and prior.kind in USER_DERIVED_KINDS:
            child.append(prior.model_copy(update={"status": "SUPERSEDED"}))
        elif prior.status == "ACTIVE" and prior.kind == "MISSING_INFO":
            child.append(prior.model_copy(update={"status": "SUPERSEDED"}))
        else:
            child.append(prior)
    return UnderstandingRevision(
        objective_id=intent.objective_id,
        intent_id=intent.id,
        parent_revision_id=parent.id,
        scope_mode=scope_mode,  # type: ignore[arg-type]
        claims=tuple(child),
    )


def decision_from_understanding(revision: UnderstandingRevision, validation: RuleResult) -> PlanDecision:
    if any(c.kind == "AMBIGUITY" and c.status == "ACTIVE" for c in revision.claims):
        return "NEED_CLARIFICATION"
    if not validation.ok:
        return validation.decision
    if any(c.kind == "MISSING_INFO" and c.status == "ACTIVE" for c in revision.claims):
        return "NEED_EVIDENCE"
    return "ACCEPT"


def build_understanding_v1(
    *,
    intent: UserIntent,
    turns: tuple[UserTurn, ...] = (),
    parent: UnderstandingRevision | None = None,
    governance: GovernanceRevision | None = None,
) -> UnderstandingV1Result:
    """Construct Understanding v1 from user text only. Never inspects a repository."""
    scope_mode, drafts = _extract_drafts(intent=intent, turns=turns, governance=governance)
    if parent is None:
        revision = UnderstandingRevision(
            objective_id=intent.objective_id,
            intent_id=intent.id,
            scope_mode=scope_mode,  # type: ignore[arg-type]
            claims=tuple(drafts),
        )
    else:
        revision = _reconcile_with_parent(
            parent=parent,
            drafts=tuple(drafts),
            intent=intent,
            scope_mode=scope_mode,
        )
    kinds = {c.kind for c in revision.claims}
    if kinds & V1_FORBIDDEN_KINDS:
        validation = RuleResult.fail("U1", "Understanding v1 cannot emit SITUATION or CAUSE")
        return UnderstandingV1Result(revision=revision, validation=validation, decision="REJECT")
    validation = validate_understanding_revision(
        intent=intent,
        revision=revision,
        parent=parent,
        turns=turns,
        governance=governance,
    )
    decision = decision_from_understanding(revision, validation)
    return UnderstandingV1Result(revision=revision, validation=validation, decision=decision)
