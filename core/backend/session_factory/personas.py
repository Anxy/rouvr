"""Versioned personas with explicit authority limits (Stage 6 core).

Roles (fixed ownership: Muse proposes/executes, Verifier_Lead closes):

- planner: propose_decomposition, request_work. No implement/verify/close.
- implementer: execute_assignment, write_evidence, report_result.
  No verify/close.
- verifier: accept, reject, report_result. No implement.

Personas are versioned records (mirroring the persona_manifest
artifact shape); authority is data, checked by ``can_act`` (pure).
Messages never override Temporal progression — they request work or
report results; see events.py for transport discipline.
"""

from __future__ import annotations

from typing import Any

PERSONAS: tuple[dict[str, Any], ...] = (
    {
        "role": "planner",
        "version": "v1",
        "allowed": ["propose_decomposition", "request_work"],
        "denied": ["execute_assignment", "accept", "reject", "close"],
    },
    {
        "role": "implementer",
        "version": "v1",
        "allowed": ["execute_assignment", "write_evidence", "report_result"],
        "denied": ["accept", "reject", "close", "propose_decomposition"],
    },
    {
        "role": "verifier",
        "version": "v1",
        "allowed": ["accept", "reject", "report_result"],
        "denied": ["execute_assignment", "close", "propose_decomposition"],
    },
)


def can_act(*, role: str, action: str, version: str = "v1") -> bool:
    """True iff the versioned persona may perform the action (pure)."""
    record = next(
        (p for p in PERSONAS if p["role"] == role and p["version"] == version),
        None,
    )
    if record is None:
        return False
    if action in record["denied"]:
        return False
    return action in record["allowed"]
