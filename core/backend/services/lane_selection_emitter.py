"""AIX-side lane-selection emitter (PROPOSAL).

Pure payload builder: validates one backlog unit against
``lanes/aix-selection-policy.md`` and returns WorkHub mandate-create and
governor-selection evidence payloads. Performs no I/O; the AIX caller
persists the returned payloads.

Standard library only.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

POLICY_PATH = "lanes/aix-selection-policy.md"

ALLOWED_CLASSES = frozenset(
    {
        "docs-registration",
        "diagnostic-cleanup",
        "config-hygiene",
        "test-dep-gap",
    }
)

FORBIDDEN_PATTERNS = (
    (r"runtime.{0,12}(change|edit|patch|fix|update|restart)", "runtime change"),
    (r"service.{0,12}(change|edit|patch|fix|update)", "service change"),
    (r"\bmerge\b", "merge"),
    (r"\bdeploy\b|\bdeployment\b|\bredeploy\b", "deploy"),
    (r"\brestart\b", "restart"),
    (r"\bgrant\b", "grant change"),
    (r"closeout|close.{0,12}done|mark.{0,12}done", "closeout to done"),
    (r"production.{0,12}(mutat|writ|change|edit|delet)",
     "production mutation"),
)

# Bare service/runtime proper names (hyeve-*-service repos, SERVICE_ROOT
# identifiers) are not changes; the patterns above require action verbs.
FORBIDDEN_TOKENS = (
    "merge",
    "deploy",
    "restart",
    "grant",
    "closeout",
)

MIN_ATTEMPTS = 1
MAX_ATTEMPTS = 3

MANDATE_PROJECT = "lane-ops"
MANDATE_KIND = "AUDIT"
MANDATE_CODE = "code"

_MANDATE_KEYS = frozenset(
    {
        "project",
        "kind",
        "code",
        "title",
        "lane_class",
        "scope",
        "acceptance",
        "max_attempts",
        "correlation_id",
        "idempotency_key",
    }
)

_GOVERNOR_KEYS = frozenset(
    {
        "policy",
        "lane_class",
        "decision",
        "bounds",
        "correlation_id",
    }
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _haystack(unit: dict[str, Any]) -> str:
    parts = [
        unit.get("lane_class"),
        unit.get("title"),
        unit.get("scope"),
        unit.get("details"),
        unit.get("description"),
        unit.get("acceptance"),
        unit.get("acceptance_criteria"),
    ]
    flat: list[str] = []
    for part in parts:
        if isinstance(part, (list, tuple)):
            flat.extend(str(p) for p in part)
        else:
            flat.append(str(part or ""))
    return "\n".join(flat).lower()


def _forbidden_hits(unit: dict[str, Any]) -> list[str]:
    hay = _haystack(unit)
    hits: list[str] = []
    for token in FORBIDDEN_TOKENS:
        if re.search(r"\b" + re.escape(token) + r"\b", hay):
            hits.append(token)
    for pattern, label in FORBIDDEN_PATTERNS:
        if re.search(pattern, hay):
            hits.append(label)
    return hits


def validate_selection(unit: Any) -> dict[str, Any]:
    """Validate a single backlog unit. Returns a verdict dict; never raises."""
    reasons: list[str] = []
    lane_class = ""
    if not isinstance(unit, dict):
        return {
            "ok": False,
            "lane_class": "",
            "reasons": ["unit must be a mapping describing exactly one backlog item"],
        }
    if isinstance(unit.get("units"), list):
        if len(unit["units"]) != 1:
            reasons.append(
                f"single unit required: got {len(unit['units'])} units; "
                "split into one emit per unit"
            )
        else:
            reasons.append("unit must be the item mapping itself, not a wrapper with 'units'")
    lane_class = _text(unit.get("lane_class") or unit.get("class")).lower()
    if not lane_class:
        reasons.append("lane_class is required")
    elif lane_class not in ALLOWED_CLASSES:
        reasons.append(
            f"lane_class {lane_class!r} not in policy classes "
            f"{sorted(ALLOWED_CLASSES)} ({POLICY_PATH})"
        )
    scope = _text(unit.get("scope") or unit.get("details") or unit.get("description"))
    if not scope:
        reasons.append("explicit scope is required (scope/details/description must be non-empty)")
    acceptance = _text(unit.get("acceptance") or unit.get("acceptance_criteria"))
    if not acceptance:
        reasons.append(
            "explicit acceptance is required (acceptance/acceptance_criteria must be non-empty)"
        )
    attempts = unit.get("max_attempts", unit.get("attempts", 1))
    try:
        attempts_int = int(attempts)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        attempts_int = -1
    if attempts_int < MIN_ATTEMPTS or attempts_int > MAX_ATTEMPTS:
        reasons.append(
            f"max_attempts must be an integer {MIN_ATTEMPTS}-{MAX_ATTEMPTS}, got {attempts!r}"
        )
    for token in _forbidden_hits(unit):
        reasons.append(
            f"refused: forbidden {token!r} content is outside {POLICY_PATH} classes"
        )
    return {"ok": not reasons, "lane_class": lane_class, "reasons": reasons}


def idempotency_key(correlation_id: str) -> str:
    """Deterministic idempotency key derived from the correlation id."""
    digest = hashlib.sha256(_text(correlation_id).encode("utf-8")).hexdigest()
    return f"lane-select-{digest[:32]}"


def build_mandate(unit: dict[str, Any], *, correlation_id: str) -> dict[str, Any]:
    """Build WorkHub mandate-create + governor-selection payloads.

    Raises ValueError when the unit fails validation. Performs no I/O.
    """
    verdict = validate_selection(unit)
    if not verdict["ok"]:
        raise ValueError(f"invalid lane selection: {verdict['reasons']}")
    cid = _text(correlation_id)
    if not cid:
        raise ValueError("correlation_id is required")
    attempts = int(unit.get("max_attempts", unit.get("attempts", 1)))
    mandate = {
        "project": MANDATE_PROJECT,
        "kind": MANDATE_KIND,
        "code": MANDATE_CODE,
        "title": _text(unit.get("title") or unit.get("lane_class")),
        "lane_class": verdict["lane_class"],
        "scope": _text(unit.get("scope") or unit.get("details") or unit.get("description")),
        "acceptance": _text(unit.get("acceptance") or unit.get("acceptance_criteria")),
        "max_attempts": attempts,
        "correlation_id": cid,
        "idempotency_key": idempotency_key(cid),
    }
    governor_selection = {
        "policy": POLICY_PATH,
        "lane_class": verdict["lane_class"],
        "decision": "emit",
        "bounds": {
            "allowed_classes": sorted(ALLOWED_CLASSES),
            "forbidden_tokens": list(FORBIDDEN_TOKENS),
            "min_attempts": MIN_ATTEMPTS,
            "max_attempts": MAX_ATTEMPTS,
            "single_unit": True,
            "requires_explicit_scope": True,
            "requires_explicit_acceptance": True,
        },
        "correlation_id": cid,
    }
    assert set(mandate) == set(_MANDATE_KEYS), set(mandate) ^ set(_MANDATE_KEYS)
    assert set(governor_selection) == set(_GOVERNOR_KEYS), (
        set(governor_selection) ^ set(_GOVERNOR_KEYS)
    )
    return {"mandate": mandate, "governor_selection": governor_selection}
