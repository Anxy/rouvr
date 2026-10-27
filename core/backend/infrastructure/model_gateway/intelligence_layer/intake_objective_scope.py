"""Intake objective scope: never silently drop explicit user deliverables.

Generic (technology-agnostic): extract role/tech deliverables from the original
user request, treat restrictive output phrasing as constraints (unless they
explicitly cancel a deliverable), and reject a normalized_objective that reduces
active scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Role words are structural product facets — not specific stacks (no html/php/mysql).
_ROLE = (
    r"frontend|front-end|backend|back-end|database|persistence|api|apis|"
    r"ui|ux|server|servers|client|clients|tests?|docs?|documentation|"
    r"migration|migrations|config|configuration|integration|integrations|"
    r"mobile|ios|android|worker|workers|queue|queues|schema"
)

_ROLE_RE = re.compile(rf"\b(?:{_ROLE})\b", re.IGNORECASE)

# "a frontend in HTML" / "backend using Node"
_ROLE_IN_TECH = re.compile(
    rf"\b(?P<article>an?\s+|the\s+)?(?P<role>{_ROLE})\s+"
    rf"(?:in|using|via|with|on)\s+(?P<tech>[\w.+#-]{{1,40}})\b",
    re.IGNORECASE,
)
# "HTML frontend" / "PHP backend" / "MySQL database"
_TECH_ROLE = re.compile(
    rf"\b(?P<tech>[\w.+#-]{{1,40}})\s+(?P<role>{_ROLE})\b",
    re.IGNORECASE,
)
# "with a frontend, a backend, and a database"
_WITH_ROLE = re.compile(
    rf"\b(?:with|including|plus)\s+(?:an?\s+|the\s+)?(?P<role>{_ROLE})\b",
    re.IGNORECASE,
)

# Output / delivery limits — constraints unless they cancel a deliverable.
_RESTRICTIVE_START = re.compile(
    r"(?is)^(?:please\s+)?"
    r"(?:"
    r"only\b|just\b|"
    r"(?:provide|return|give|send|output|emit)\s+(?:me\s+)?(?:with\s+)?only\b|"
    r"(?:provide|return|give|send)\b|"
    r"do\s+not\b|don't\b|must\s+not\b|without\b"
    r")"
)

# Explicit cancellation of a named deliverable/role.
_CANCEL = re.compile(
    rf"(?i)\b(?:do\s+not|don't|must\s+not|skip|omit|exclude|cancel|no)\s+"
    rf"(?:(?:create|build|implement|add|include|make|write|generate)\s+)?"
    rf"(?:(?:an?|the|any)\s+)?(?P<target>{_ROLE}|[\w.+#-]{{1,40}})"
)

# Mentions that demote a deliverable to context (not an active create target).
_DEMOTION_BEFORE = re.compile(
    r"(?i)\b(?:"
    r"intended\s+for|supporting|supported\s+by|consumed\s+by|compatible\s+with|"
    r"for\s+(?:an?|the)|needed\s+by|used\s+by|usable\s+by|"
    r"without\s+(?:adding|creating|implementing|modifying)|"
    r"does\s+not\s+(?:add|include|create|implement)|"
    r"do\s+not\s+(?:add|include|create|implement)|"
    r"not\s+(?:adding|creating|including)"
    r")\b"
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class ExplicitDeliverable:
    """One explicit product deliverable from the original user request."""

    role: str
    tech: str | None = None

    @property
    def key(self) -> str:
        role = self.role.casefold()
        tech = (self.tech or "").casefold()
        return f"{role}:{tech}" if tech else role


@dataclass(frozen=True)
class IntakeScopeValidation:
    ok: bool
    code: str
    detail: str
    original_objective: str
    normalized_objective: str
    required: tuple[ExplicitDeliverable, ...] = ()
    missing: tuple[ExplicitDeliverable, ...] = ()
    cancelled: tuple[str, ...] = ()
    used_fallback: bool = False


def _norm_role(role: str) -> str:
    text = str(role or "").casefold().strip()
    aliases = {
        "front-end": "frontend",
        "back-end": "backend",
        "apis": "api",
        "servers": "server",
        "clients": "client",
        "docs": "docs",
        "documentation": "docs",
        "tests": "tests",
        "test": "tests",
        "migrations": "migration",
        "configuration": "config",
        "integrations": "integration",
        "workers": "worker",
        "queues": "queue",
        "persistence": "database",
        "db": "database",
        "ux": "ui",
        "schema": "schema",
    }
    return aliases.get(text, text)


def _is_restrictive_clause(clause: str) -> bool:
    text = str(clause or "").strip()
    if not text:
        return False
    # "provide only …" / "only the schema" / "just return X"
    if _RESTRICTIVE_START.search(text):
        return True
    if re.search(r"(?i)\bonly\s+(?:the\s+)?(?:schema|ddl|sql|output|result)\b", text):
        return True
    if re.search(r"(?i)\b(?:provide|return|give|send)\s+only\b", text):
        return True
    return False


def _cancelled_targets(text: str) -> set[str]:
    out: set[str] = set()
    for match in _CANCEL.finditer(text or ""):
        target = _norm_role(match.group("target"))
        if target:
            out.add(target)
    return out


def _clause_deliverables(clause: str) -> list[ExplicitDeliverable]:
    found: list[ExplicitDeliverable] = []
    seen: set[str] = set()

    def add(role: str, tech: str | None = None) -> None:
        item = ExplicitDeliverable(role=_norm_role(role), tech=(tech or None))
        if not item.role or item.key in seen:
            return
        # Drop bare "schema" when it is only an output artifact word without a stack role
        # already captured — still allow schema when paired as tech via other patterns.
        seen.add(item.key)
        found.append(item)

    for match in _ROLE_IN_TECH.finditer(clause):
        add(match.group("role"), match.group("tech"))
    for match in _TECH_ROLE.finditer(clause):
        tech = match.group("tech")
        role = match.group("role")
        # Avoid treating verbs/articles as tech: "a frontend", "the backend"
        if tech.casefold() in {"a", "an", "the", "our", "my", "your"}:
            add(role, None)
        else:
            add(role, tech)
    for match in _WITH_ROLE.finditer(clause):
        add(match.group("role"), None)
    # Enumerations without "with": "frontend, backend, and database"
    for match in _ROLE_RE.finditer(clause):
        add(match.group(0), None)
    return found


def extract_explicit_deliverables(original: str) -> tuple[ExplicitDeliverable, ...]:
    """Deliverables from the authoritative user request (non-restrictive clauses)."""
    text = str(original or "").strip()
    if not text:
        return ()
    cancelled = _cancelled_targets(text)
    clauses = [c.strip() for c in _SENTENCE_SPLIT.split(text) if c and c.strip()]
    if not clauses:
        clauses = [text]

    required: list[ExplicitDeliverable] = []
    seen: set[str] = set()
    for clause in clauses:
        if _is_restrictive_clause(clause) and not _cancelled_targets(clause):
            # Restrictive output instruction — not a deliverable source.
            continue
        for item in _clause_deliverables(clause):
            if item.role in cancelled or (item.tech and item.tech.casefold() in cancelled):
                continue
            if item.key in seen:
                continue
            seen.add(item.key)
            required.append(item)
    # If every clause was restrictive, fall back to whole-text extraction minus cancels
    # so we do not invent emptiness for short "only X" asks that are the entire goal.
    if not required and not any(not _is_restrictive_clause(c) for c in clauses):
        for item in _clause_deliverables(text):
            if item.role in cancelled or (item.tech and item.tech.casefold() in cancelled):
                continue
            if item.key in seen:
                continue
            seen.add(item.key)
            required.append(item)
    return tuple(required)


def _active_mention(haystack: str, needle: str) -> bool:
    """True when needle appears outside a demotion window."""
    text = haystack or ""
    token = str(needle or "").strip()
    if not token:
        return True
    pattern = re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)
    for match in pattern.finditer(text):
        start = max(0, match.start() - 48)
        window = text[start : match.start()]
        if _DEMOTION_BEFORE.search(window):
            continue
        return True
    return False


def deliverable_active_in(normalized: str, deliverable: ExplicitDeliverable) -> bool:
    text = str(normalized or "")
    if not text.strip():
        return False
    if not _active_mention(text, deliverable.role):
        # Accept common aliases in normalized prose.
        aliases = {
            "frontend": ("front-end", "ui", "client"),
            "backend": ("back-end", "server"),
            "database": ("db", "persistence"),
            "docs": ("documentation"),
        }
        alts = aliases.get(deliverable.role, ())
        if isinstance(alts, str):
            alts = (alts,)
        if not any(_active_mention(text, alt) for alt in alts):
            return False
    if deliverable.tech and not _active_mention(text, deliverable.tech):
        return False
    return True


def validate_intake_normalized_objective(
    *,
    original_objective: str,
    normalized_objective: str | None,
) -> IntakeScopeValidation:
    """Reject normalized text that drops any explicit original deliverable."""
    original = str(original_objective or "").strip()
    normalized = str(normalized_objective or "").strip()
    required = extract_explicit_deliverables(original)
    cancelled = tuple(sorted(_cancelled_targets(original)))

    if not original:
        return IntakeScopeValidation(
            ok=True,
            code="INTAKE_SCOPE_OK",
            detail="empty original",
            original_objective=original,
            normalized_objective=normalized,
            cancelled=cancelled,
        )
    if not normalized:
        return IntakeScopeValidation(
            ok=False,
            code="INTAKE_SCOPE_EMPTY",
            detail="normalized_objective is empty",
            original_objective=original,
            normalized_objective=normalized,
            required=required,
            missing=required,
            cancelled=cancelled,
        )
    if not required:
        return IntakeScopeValidation(
            ok=True,
            code="INTAKE_SCOPE_OK",
            detail="no explicit multi-deliverable scope to enforce",
            original_objective=original,
            normalized_objective=normalized,
            cancelled=cancelled,
        )

    missing = tuple(item for item in required if not deliverable_active_in(normalized, item))
    if missing:
        detail = ", ".join(
            f"{m.role}" + (f"/{m.tech}" if m.tech else "") for m in missing
        )
        return IntakeScopeValidation(
            ok=False,
            code="INTAKE_SCOPE_REDUCED",
            detail=f"normalized_objective dropped explicit deliverables: {detail}",
            original_objective=original,
            normalized_objective=normalized,
            required=required,
            missing=missing,
            cancelled=cancelled,
        )
    return IntakeScopeValidation(
        ok=True,
        code="INTAKE_SCOPE_OK",
        detail="normalized_objective preserves explicit deliverables",
        original_objective=original,
        normalized_objective=normalized,
        required=required,
        cancelled=cancelled,
    )


def resolve_normalized_objective(
    *,
    original_objective: str,
    normalized_objective: str | None,
) -> tuple[str, IntakeScopeValidation]:
    """Return accepted normalized text, falling back to the original on scope reduction."""
    original = str(original_objective or "").strip()
    candidate = str(normalized_objective or "").strip() or None
    result = validate_intake_normalized_objective(
        original_objective=original,
        normalized_objective=candidate,
    )
    if result.ok and candidate:
        return candidate, result
    # Authoritative fallback: never admit a reduced rewrite.
    fallback = original or (candidate or "")
    return fallback, IntakeScopeValidation(
        ok=result.ok,
        code=result.code if not result.ok else "INTAKE_SCOPE_OK",
        detail=result.detail
        if not result.ok
        else "normalized_objective accepted",
        original_objective=original,
        normalized_objective=candidate or "",
        required=result.required,
        missing=result.missing,
        cancelled=result.cancelled,
        used_fallback=not result.ok,
    )


__all__ = [
    "ExplicitDeliverable",
    "IntakeScopeValidation",
    "deliverable_active_in",
    "extract_explicit_deliverables",
    "resolve_normalized_objective",
    "validate_intake_normalized_objective",
]
