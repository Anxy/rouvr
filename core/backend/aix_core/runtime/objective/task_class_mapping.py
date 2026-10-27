"""AIX Closed-Loop Execution Plan, Sprint W1: map `task_family.py`'s live 14-family
taxonomy onto `policy.SUPPORTED_TASK_CLASSES`'s 5-class restricted admission policy.

`ObjectiveEnvelopeV1.task_class` (contracts.py) validates against `SUPPORTED_TASK_CLASSES`
and `TaskClassPolicy` (policy.py) derives `allowed_effects`/`requires_human_approval`/
production-mutation eligibility from that same 5-class set. `task_family.py`'s classifier
(the thing that actually runs on every live objective) uses a different, broader 14-family
vocabulary. Without this module, `ObjectiveEnvelopeV1` cannot be constructed for most real
objectives.

This module deliberately does **not** widen `SUPPORTED_TASK_CLASSES` or add new
`allowed_effects` entries -- that is a real security-policy decision (new task classes need
their own reviewed `allowed_effects` set in `policy.py`) and is out of scope for a mapping
layer. Only families with a defensible, conservative fit onto an *existing* restricted class
are mapped. Everything else maps to `None`, which the existing `TaskClassPolicy.evaluate()`
already handles correctly (`CLARIFY`/`UNSUPPORTED_TASK_CLASS`) -- fail closed, do not guess.

Families intentionally left unmapped, and why:
    CREATE   -- no existing class models "author a new file"; closest is
                `single_file_helper` but that name and its `write_scoped_source` effect
                were reviewed for editing existing scope, not creating new resources.
    DELETE   -- destructive; no existing class authorizes deletion effects at all.
    COMMAND  -- arbitrary command execution; no existing class authorizes `run_command`-
                shaped effects, only `run_validation`.
    DEBUG    -- ambiguous scope (may be read-only investigation or a mutating fix);
                forcing one mapping would either over- or under-grant.
    UNKNOWN  -- classifier gave up; must not silently resolve to any authorized class.

Each of these needs a deliberately reviewed `policy.py` addition (new task class, new
`_ALLOWED_EFFECTS_BY_TASK_CLASS` entry, a decision on `_MUTATING_TASK_CLASSES` membership)
before it can be admitted -- not a default guess made here.
"""

from __future__ import annotations

from backend.aix_core.runtime.governance.policy import SUPPORTED_TASK_CLASSES
from backend.aix_core.runtime.objective.task_family import TASK_FAMILIES

# Read-only / investigative families: no write effect requested, closest existing
# restricted class is `verification_only` (read_source, run_validation, attach_evidence).
_READ_ONLY_FAMILIES = frozenset({"EXPLORE", "READ", "SEARCH", "ANALYZE", "EXPLAIN", "REVIEW"})

# Source-mutating families with a single defensible existing-class fit.
_TASK_FAMILY_TO_TASK_CLASS: dict[str, str] = {
    **{family: "verification_only" for family in _READ_ONLY_FAMILIES},
    "EDIT": "single_file_helper",
    "REFACTOR": "single_file_helper",
    "TEST": "test_generation",
}

assert set(_TASK_FAMILY_TO_TASK_CLASS.values()) <= SUPPORTED_TASK_CLASSES, (
    "task_class_mapping.py must only ever map onto an already-supported policy task class"
)
assert set(_TASK_FAMILY_TO_TASK_CLASS) <= set(TASK_FAMILIES), (
    "task_class_mapping.py references a task_family.py family that no longer exists"
)

UNMAPPED_FAMILIES: frozenset[str] = frozenset(TASK_FAMILIES) - set(_TASK_FAMILY_TO_TASK_CLASS)


def task_family_to_task_class(task_family: str | None) -> str | None:
    """Best-fit, conservative mapping from a `task_family.py` family to a
    `policy.SUPPORTED_TASK_CLASSES` member. Returns `None` (not a guess) when no
    existing restricted class defensibly covers the family -- callers must treat
    `None` exactly as "no task_class available yet", which `TaskClassPolicy.evaluate()`
    already rejects/clarifies correctly rather than silently admitting.
    """
    if task_family is None:
        return None
    normalized = str(task_family).strip().upper()
    return _TASK_FAMILY_TO_TASK_CLASS.get(normalized)


__all__ = [
    "UNMAPPED_FAMILIES",
    "task_family_to_task_class",
]
