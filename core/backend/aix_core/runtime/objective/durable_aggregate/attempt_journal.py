"""C013: write-ahead attempt journal + orphan compensation.

Transaction boundaries (fail-closed):
1. BEGIN — durable OPEN row before any mutable effect
2. EFFECTS — mutations may run only while OPEN
3. COMMIT — terminal SUCCEEDED/FAILED only after effects are recorded
4. Crash with OPEN and no COMMIT → ORPHAN → compensate (never treat as complete)
5. Same (tenant_id, command_id) cannot apply effects twice
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Mapping
from backend.aix_core.runtime.objective.ulid_compat import new_ulid
JOURNAL_SCHEMA = "aix.attempt_journal.v1"

AttemptJournalStatus = Literal[
    "OPEN",
    "COMMITTED",
    "ABORTED",
    "ORPHAN_COMPENSATED",
]

TerminalOutcome = Literal["SUCCEEDED", "FAILED", "CANCELLED", "ABORTED"]

TRANSACTION_BOUNDARIES: tuple[str, ...] = (
    "BEGIN must precede any mutable effect for a step attempt.",
    "COMMIT is the only authority that may mark an attempt complete.",
    "OPEN without COMMIT after restart is an orphan and must be compensated.",
    "Compensated orphans are never treated as SUCCEEDED.",
    "Identical (tenant_id, command_id) replay must not re-apply effects.",
    "Duplicate (tenant_id, run_id, step_id, attempt_number) while OPEN/COMMITTED fails closed.",
)


class AttemptJournalError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class AttemptJournalEntry:
    tenant_id: str
    run_id: str
    objective_id: str
    step_id: str
    attempt_id: str
    attempt_number: int
    command_id: str
    status: AttemptJournalStatus
    opened_at: str
    schema_version: str = JOURNAL_SCHEMA
    outcome: TerminalOutcome | None = None
    effects_applied: bool = False
    committed_at: str | None = None
    compensation_reason: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "objective_id": self.objective_id,
            "step_id": self.step_id,
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "command_id": self.command_id,
            "status": self.status,
            "outcome": self.outcome,
            "effects_applied": self.effects_applied,
            "opened_at": self.opened_at,
            "committed_at": self.committed_at,
            "compensation_reason": self.compensation_reason,
            "payload": deepcopy(self.payload),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AttemptJournalEntry":
        version = str(raw.get("schema_version") or "")
        if version != JOURNAL_SCHEMA:
            raise AttemptJournalError("UNKNOWN_JOURNAL_SCHEMA", version or "<missing>")
        return cls(
            tenant_id=str(raw["tenant_id"]),
            run_id=str(raw["run_id"]),
            objective_id=str(raw["objective_id"]),
            step_id=str(raw["step_id"]),
            attempt_id=str(raw["attempt_id"]),
            attempt_number=int(raw["attempt_number"]),
            command_id=str(raw["command_id"]),
            status=str(raw["status"]),  # type: ignore[arg-type]
            opened_at=str(raw["opened_at"]),
            schema_version=version,
            outcome=raw.get("outcome"),  # type: ignore[arg-type]
            effects_applied=bool(raw.get("effects_applied") or False),
            committed_at=raw.get("committed_at"),
            compensation_reason=raw.get("compensation_reason"),
            payload=dict(raw.get("payload") or {}),
        )


class InMemoryAttemptJournal:
    """Process-local write-ahead journal; share across backends to simulate restart."""

    def __init__(self) -> None:
        self._by_attempt: dict[tuple[str, str], AttemptJournalEntry] = {}
        self._by_command: dict[tuple[str, str], str] = {}
        self._by_step_attempt: dict[tuple[str, str, str, int], str] = {}

    def get(self, *, tenant_id: str, attempt_id: str) -> AttemptJournalEntry | None:
        return self._by_attempt.get((tenant_id, attempt_id))

    def get_by_command(self, *, tenant_id: str, command_id: str) -> AttemptJournalEntry | None:
        attempt_id = self._by_command.get((tenant_id, command_id))
        if attempt_id is None:
            return None
        return self.get(tenant_id=tenant_id, attempt_id=attempt_id)

    def list_for_run(self, *, tenant_id: str, run_id: str) -> list[AttemptJournalEntry]:
        return [
            e
            for (t, _aid), e in self._by_attempt.items()
            if t == tenant_id and e.run_id == run_id
        ]

    def begin(
        self,
        *,
        tenant_id: str,
        run_id: str,
        objective_id: str,
        step_id: str,
        attempt_number: int,
        command_id: str,
        attempt_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> AttemptJournalEntry:
        if not tenant_id.strip() or not run_id.strip() or not command_id.strip():
            raise AttemptJournalError("INVALID_JOURNAL_BEGIN", "tenant/run/command required")
        existing_cmd = self.get_by_command(tenant_id=tenant_id, command_id=command_id)
        if existing_cmd is not None:
            if existing_cmd.status == "COMMITTED":
                raise AttemptJournalError(
                    "DUPLICATE_ATTEMPT_APPLY",
                    f"command_id={command_id} already COMMITTED as {existing_cmd.attempt_id}",
                )
            if existing_cmd.status == "OPEN":
                raise AttemptJournalError(
                    "DUPLICATE_ATTEMPT_APPLY",
                    f"command_id={command_id} already OPEN as {existing_cmd.attempt_id}",
                )
            # ABORTED / ORPHAN_COMPENSATED: allow a new attempt with a new command_id only.
            # Same command_id still fails closed.
            raise AttemptJournalError(
                "DUPLICATE_ATTEMPT_APPLY",
                f"command_id={command_id} already used status={existing_cmd.status}",
            )
        step_key = (tenant_id, run_id, step_id, int(attempt_number))
        if step_key in self._by_step_attempt:
            prior_id = self._by_step_attempt[step_key]
            prior = self.get(tenant_id=tenant_id, attempt_id=prior_id)
            if prior is not None and prior.status in {"OPEN", "COMMITTED"}:
                raise AttemptJournalError(
                    "DUPLICATE_STEP_ATTEMPT",
                    f"step={step_id} attempt_number={attempt_number} status={prior.status}",
                )
        aid = attempt_id or f"att_{new_ulid()[:12]}"
        entry = AttemptJournalEntry(
            tenant_id=tenant_id,
            run_id=run_id,
            objective_id=objective_id,
            step_id=step_id,
            attempt_id=aid,
            attempt_number=int(attempt_number),
            command_id=command_id,
            status="OPEN",
            opened_at=_utc(),
            payload=dict(payload or {}),
        )
        self._by_attempt[(tenant_id, aid)] = entry
        self._by_command[(tenant_id, command_id)] = aid
        self._by_step_attempt[step_key] = aid
        return entry

    def mark_effects_applied(self, *, tenant_id: str, attempt_id: str) -> AttemptJournalEntry:
        entry = self._require_open(tenant_id=tenant_id, attempt_id=attempt_id)
        entry.effects_applied = True
        return entry

    def commit(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        outcome: TerminalOutcome,
        payload: Mapping[str, Any] | None = None,
    ) -> AttemptJournalEntry:
        entry = self._require_open(tenant_id=tenant_id, attempt_id=attempt_id)
        if outcome == "SUCCEEDED" and not entry.effects_applied:
            raise AttemptJournalError(
                "COMMIT_WITHOUT_EFFECTS",
                "cannot COMMIT SUCCEEDED before effects_applied",
            )
        entry.status = "COMMITTED"
        entry.outcome = outcome
        entry.committed_at = _utc()
        if payload:
            entry.payload.update(dict(payload))
        return entry

    def abort(
        self,
        *,
        tenant_id: str,
        attempt_id: str,
        reason: str,
        payload: Mapping[str, Any] | None = None,
    ) -> AttemptJournalEntry:
        entry = self._require(tenant_id=tenant_id, attempt_id=attempt_id)
        if entry.status != "OPEN":
            raise AttemptJournalError("ABORT_NOT_OPEN", entry.status)
        entry.status = "ABORTED"
        entry.outcome = "ABORTED"
        entry.committed_at = _utc()
        entry.compensation_reason = reason
        if payload:
            entry.payload.update(dict(payload))
        return entry

    def compensate_orphans(
        self, *, tenant_id: str, run_id: str, reason: str = "PROCESS_RESTART_ORPHAN"
    ) -> list[AttemptJournalEntry]:
        """Mark OPEN attempts as ORPHAN_COMPENSATED. Never invent SUCCEEDED."""
        compensated: list[AttemptJournalEntry] = []
        for entry in self.list_for_run(tenant_id=tenant_id, run_id=run_id):
            if entry.status != "OPEN":
                continue
            entry.status = "ORPHAN_COMPENSATED"
            entry.outcome = "ABORTED"
            entry.committed_at = _utc()
            entry.compensation_reason = reason
            # Crash before commit: effects must not be treated as complete.
            entry.payload["orphan"] = True
            entry.payload["effects_complete"] = False
            compensated.append(entry)
        return compensated

    def is_complete_success(self, *, tenant_id: str, attempt_id: str) -> bool:
        entry = self.get(tenant_id=tenant_id, attempt_id=attempt_id)
        return bool(
            entry is not None
            and entry.status == "COMMITTED"
            and entry.outcome == "SUCCEEDED"
            and entry.effects_applied
        )

    def _require(self, *, tenant_id: str, attempt_id: str) -> AttemptJournalEntry:
        entry = self.get(tenant_id=tenant_id, attempt_id=attempt_id)
        if entry is None:
            raise AttemptJournalError("ATTEMPT_NOT_FOUND", attempt_id)
        return entry

    def _require_open(self, *, tenant_id: str, attempt_id: str) -> AttemptJournalEntry:
        entry = self._require(tenant_id=tenant_id, attempt_id=attempt_id)
        if entry.status != "OPEN":
            raise AttemptJournalError("ATTEMPT_NOT_OPEN", entry.status)
        return entry


__all__ = [
    "JOURNAL_SCHEMA",
    "TRANSACTION_BOUNDARIES",
    "AttemptJournalError",
    "AttemptJournalEntry",
    "InMemoryAttemptJournal",
]
