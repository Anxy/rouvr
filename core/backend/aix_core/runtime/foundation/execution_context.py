"""AIX-UGEF-001: execution context propagated from dispatch through authorization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PrincipalType = Literal["human", "agent", "service", "system"]


@dataclass(frozen=True)
class PrincipalV1:
    type: PrincipalType
    id: str
    role: str = ""


@dataclass(frozen=True)
class ExecutionContextV1:
    principal: PrincipalV1
    tenant_id: str = ""
    project_id: str = ""
    repository_id: str = ""
    cycle_id: str = ""
    step_id: str = ""
    attempt_id: str = ""
    allowed_files: tuple[str, ...] = ()
    apply_edits: bool = True
    approved: bool = False
    session_grants: tuple[str, ...] = ()


SYSTEM_PRINCIPAL = PrincipalV1(type="system", id="aix-system", role="system")


def build_execution_context(
    *,
    principal: PrincipalV1 | None = None,
    tenant_id: str = "",
    project_id: str = "",
    repository_id: str = "",
    cycle_id: str = "",
    step_id: str = "",
    attempt_id: str = "",
    allowed_files: tuple[str, ...] = (),
    apply_edits: bool = True,
    approved: bool = False,
    session_grants: tuple[str, ...] = (),
) -> ExecutionContextV1:
    return ExecutionContextV1(
        principal=principal or SYSTEM_PRINCIPAL,
        tenant_id=tenant_id,
        project_id=project_id,
        repository_id=repository_id,
        cycle_id=cycle_id,
        step_id=step_id,
        attempt_id=attempt_id,
        allowed_files=allowed_files,
        apply_edits=apply_edits,
        approved=approved,
        session_grants=session_grants,
    )


__all__ = [
    "PrincipalType",
    "PrincipalV1",
    "ExecutionContextV1",
    "SYSTEM_PRINCIPAL",
    "build_execution_context",
]
