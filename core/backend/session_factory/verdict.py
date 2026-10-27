"""Delivery/repair loop: verification verdicts with bounded repair (Stage 4).

Statuses separate the four concerns the directive names:

- implementation: ADMITTED → IMPLEMENTED
- verification: IMPLEMENTED → VERIFIED | FAILED_VERIFICATION
- repair: FAILED_VERIFICATION → REPAIR (bounded by max_attempts,
  enforced via should_retry) → IMPLEMENTED again
- blocked: attempts exhausted → BLOCKED; environment bars
  verification entirely → BLOCKED immediately (never false PASS)

Integration/deployment statuses are downstream of VERIFIED and not
assigned here. Pure transitions; unit-tested.
"""

from __future__ import annotations

IMPLEMENTED = "IMPLEMENTED"
VERIFIED = "VERIFIED"
FAILED_VERIFICATION = "FAILED_VERIFICATION"
REPAIR = "REPAIR"
BLOCKED = "BLOCKED"

OUTCOMES = frozenset({"pass", "fail", "blocked"})


def record_verdict(
    *,
    attempt_count: int,
    max_attempts: int,
    outcome: str,
) -> str:
    """Next status after independent verification of one attempt.

    - pass → VERIFIED (direct pass, no repair spent)
    - fail with budget left → REPAIR (bounded repair assignment)
    - fail with budget spent → BLOCKED (attempts exhausted)
    - blocked → BLOCKED (genuine blocker, zero repair spent,
      never reported as any kind of pass)
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome: {outcome!r}")
    if outcome == "pass":
        return VERIFIED
    if outcome == "blocked":
        return BLOCKED
    if attempt_count + 1 < max_attempts:
        return REPAIR
    return BLOCKED
