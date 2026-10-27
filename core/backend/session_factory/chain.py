"""Identity chain: Objective -> Session -> Mandate -> Attempt -> Workflow -> Evidence.

Stage 1 core. All builders/validators pure (no I/O); persistence stays
behind the stores behind Depends. Links are plain dicts so every plane
(WorkHub, Temporal, PG) can carry them without importing each other.
"""

from __future__ import annotations

from typing import Any

CHAIN_ORDER = (
    "objective_id",
    "session_id",
    "mandate_id",
    "attempt_id",
    "workflow_id",
    "evidence_ref",
)


def build_attempt_record(
    *,
    attempt_id: str,
    tenant_id: str,
    objective_id: str,
    session_id: str,
    mandate_id: str,
    workflow_id: str = "",
    executor: str = "",
    model: str = "",
) -> dict[str, Any]:
    """One attempt binding the full chain (pure, unit-tested)."""
    return {
        "attempt_id": attempt_id,
        "tenant_id": tenant_id,
        "objective_id": objective_id,
        "session_id": session_id,
        "mandate_id": mandate_id,
        "workflow_id": workflow_id,
        "executor": executor,
        "model": model,
        "status": "ADMITTED",
    }


def validate_chain(links: dict[str, str]) -> list[str]:
    """Missing links in chain order (empty = complete, unit-tested)."""
    return [key for key in CHAIN_ORDER if not links.get(key)]
