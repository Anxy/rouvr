"""Task bundle: isolated workspace + context for one Muse attempt.

Stage 1 core. Pure builders: workspace path derivation (tenant-scoped,
traversal-rejecting) and the context document Muse receives alongside
the positional envelope_id. Enforcement of isolation stays with the
launcher; this module makes collisions and escapes representable and
testable.
"""

from __future__ import annotations

import re
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def workspace_path(*, root: str, tenant_id: str, mandate_id: str, attempt_id: str) -> str:
    """Isolated workspace dir; raises on unsafe IDs (unit-tested)."""
    for label, value in (
        ("tenant_id", tenant_id),
        ("mandate_id", mandate_id),
        ("attempt_id", attempt_id),
    ):
        if not _SAFE_ID.match(value):
            raise ValueError(f"unsafe {label}: {value!r}")
    return f"{root}/{tenant_id}/{mandate_id}/{attempt_id}"


def build_task_bundle(
    *,
    attempt: dict[str, Any],
    objective_text: str,
    criteria: list[str],
    constraints: list[str],
    evidence_path: str,
    workspace: str,
) -> dict[str, Any]:
    """Context document for the executor (pure, unit-tested)."""
    return {
        "attempt_id": attempt["attempt_id"],
        "tenant_id": attempt["tenant_id"],
        "mandate_id": attempt["mandate_id"],
        "executor": attempt.get("executor", ""),
        "model": attempt.get("model", ""),
        "workspace": workspace,
        "objective": objective_text,
        "acceptance_criteria": list(criteria),
        "constraints": list(constraints),
        "evidence_path": evidence_path,
    }
