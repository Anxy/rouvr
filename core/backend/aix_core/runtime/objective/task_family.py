"""Universal task-family root router.

Implements `CURSOR-CLASS-EXTENSION.md` Sprint 002 S2: every objective is
classified into a task family *before* any fine-grained operation intent
(`READ_FILE`, `EDIT_SYMBOL`, ...). Deterministic patterns only, matching
`AIX-REAL-EXECUTION-002.md`'s "high-confidence file commands use a
deterministic parser first" rule -- model-assisted classification for
genuinely ambiguous text is a separate, later concern and is never invoked
here. Low confidence resolves to `UNKNOWN`, never a guessed family.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TASK_FAMILIES: tuple[str, ...] = (
    "EXPLORE",
    "READ",
    "SEARCH",
    "ANALYZE",
    "EXPLAIN",
    "EDIT",
    "CREATE",
    "DELETE",
    "TEST",
    "COMMAND",
    "DEBUG",
    "REVIEW",
    "REFACTOR",
    "UNKNOWN",
)

# Resource-bearing families: real work against a *specific* target resource.
# Used by callers deciding whether a missing extractable resource is grounds
# for `INTENT_UNCLEAR` rather than a silent broad-repository guess.
RESOURCE_REQUIRED_FAMILIES: frozenset[str] = frozenset({"READ", "EDIT", "CREATE", "DELETE", "REFACTOR"})

DETERMINISTIC_METHOD = "deterministic"


@dataclass(frozen=True)
class TaskFamilyResult:
    task_family: str
    classification_method: str
    classification_confidence: float


# "run the affected tests" / "run test_foo.py" must win over the generic
# COMMAND "run "/"execute " bucket -- checked as a regex ahead of the plain
# substring patterns below since the run/execute verb and the word "test(s)"
# are not always adjacent.
_TEST_RE = re.compile(r"\b(run|execute)\b.*\btests?\b")

# Order matters: earlier buckets win on overlap. Debug phrasing ("why is X
# failing") must not fall through to the more generic EDIT "fix " bucket.
_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DELETE", ("delete ", "remove ", "get rid of")),
    ("DEBUG", ("why is", "why does", "why isn't", "why doesn't", "debug ", "fix the bug", "not working", "isn't working", "failing", "broken", "throws an error", "throwing an error")),
    ("REFACTOR", ("refactor ", "refactoring ", "rename ", "restructure ", "extract ", "clean up ")),
    ("CREATE", ("create ", "add a new ", "generate a new ", "scaffold ", "build a new ", "mkdir ", "empty directory", "new folder")),
    ("COMMAND", ("run ", "execute ", "start ", "build ", "deploy ", "install ")),
    ("EDIT", ("fix ", "update ", "change ", "modify ", "patch ", "edit ", "rewrite ", "replace ", "implement ", "append ", "add ", "integrate ", "configure ", "wire ", "set up ", "connect ", "enable ")),
    ("REVIEW", ("review ",)),
    ("EXPLAIN", ("explain ", "what does", "how does", "walk me through")),
    ("ANALYZE", ("analyze ", "analyse ", "assess ", "evaluate ")),
    ("SEARCH", ("find ", "search ", "where is", "where are", "locate ", "grep ")),
    (
        "READ",
        (
            "read this file",
            "read the file",
            "read this",
            "please read",
            "show me the contents",
            "show the contents",
            "what's in",
            "whats in",
            "what is in",
            "inspect this file",
            "view this file",
            "display this file",
            "summarize this file",
            "summarise this file",
            "check the file",
            "check this file",
            "read ",
        ),
    ),
    ("EXPLORE", ("explore ", "inspect ", "look at", "walk through", "understand ", "familiarize", "check ")),
)


def classify_task_family(objective_text: str) -> TaskFamilyResult:
    """Deterministic-only classification. Empty text or no pattern match both
    resolve to `UNKNOWN` with zero confidence -- never a default guess."""
    lowered = (objective_text or "").strip().lower()
    if not lowered:
        return TaskFamilyResult("UNKNOWN", DETERMINISTIC_METHOD, 0.0)
    if _TEST_RE.search(lowered):
        return TaskFamilyResult("TEST", DETERMINISTIC_METHOD, 0.9)
    for family, phrases in _PATTERNS:
        if any(phrase in lowered for phrase in phrases):
            return TaskFamilyResult(family, DETERMINISTIC_METHOD, 0.9)
    return TaskFamilyResult("UNKNOWN", DETERMINISTIC_METHOD, 0.0)


__all__ = [
    "TASK_FAMILIES",
    "RESOURCE_REQUIRED_FAMILIES",
    "TaskFamilyResult",
    "classify_task_family",
]
