"""Shared adapter contracts for 001G-W02/W03/W04."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


AdapterDisposition = Literal["SUCCEEDED", "FAILED", "DENIED"]


@dataclass(frozen=True)
class MoveTokenV1:
    token_id: str
    step_id: str
    attempt_id: str
    executor_id: str
    expires_at: str
    consumed: bool = False


@dataclass(frozen=True)
class StepAdapterRequestV1:
    cycle_id: str
    step_id: str
    attempt_id: str
    move_token: MoveTokenV1
    workspace_root: str
    command_allowlist: tuple[str, ...] = ()
    network_allowed: bool = False
    timeout_seconds: int = 60
    dry_run: bool = True


@dataclass(frozen=True)
class AdapterResultV1:
    disposition: AdapterDisposition
    executor_id: str
    attempt_id: str
    evidence_ref: str | None
    artifact_digest: str | None
    reason_codes: tuple[str, ...]
    completed_at: str = field(default_factory=_utc)


class MoveTokenGuard:
    """One Move Token authorizes exactly one attempt; reuse/expiry fail closed."""

    def __init__(self) -> None:
        self._consumed: set[str] = set()

    def authorize(self, token: MoveTokenV1, *, now: str | None = None) -> tuple[bool, tuple[str, ...]]:
        clock = now or _utc()
        if token.token_id in self._consumed or token.consumed:
            return False, ("MOVE_TOKEN_REUSED",)
        if token.expires_at <= clock:
            return False, ("MOVE_TOKEN_EXPIRED",)
        if not token.attempt_id or not token.step_id:
            return False, ("MOVE_TOKEN_INCOMPLETE",)
        self._consumed.add(token.token_id)
        return True, ("MOVE_TOKEN_CONSUMED",)
