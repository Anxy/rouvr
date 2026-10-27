"""P20: Observability & Control Room.

Single-view operational workspace for AIX. Aggregates state from
objectives, agents, capabilities, policies, costs, and evidence
into a queryable snapshot. Provides human control actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

ControlAction = Literal[
    "pause", "resume", "stop", "approve", "deny",
    "redirect", "rollback", "inspect", "take_over",
]


@dataclass(frozen=True)
class ObjectiveStatusV1:
    objective_id: str
    tenant_id: str
    title: str
    state: str  # admitted, planning, executing, verifying, completed, failed, paused
    current_step: str | None = None
    current_agent: str | None = None
    current_capability: str | None = None
    current_resource: str | None = None
    progress_pct: float = 0.0
    elapsed_seconds: float = 0.0
    cost_usd: float = 0.0
    tokens_used: int = 0
    steps_completed: int = 0
    steps_total: int = 0
    failures: int = 0
    repairs: int = 0
    pending_approvals: int = 0


@dataclass(frozen=True)
class TimelineEventV1:
    timestamp: str
    objective_id: str
    event_type: str  # plan_created, step_started, action_authorized, execution_completed, etc.
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    severity: Literal["info", "warn", "error", "critical"] = "info"


@dataclass(frozen=True)
class ControlCommandV1:
    """Human control command."""
    command_id: str
    action: ControlAction
    target_type: Literal["objective", "step", "agent", "capability"]
    target_id: str
    issued_by: str
    issued_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )
    reason: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ControlResultV1:
    command_id: str
    accepted: bool
    message: str = ""


@dataclass(frozen=True)
class SystemHealthV1:
    """Aggregate system health snapshot."""
    active_objectives: int = 0
    queued_objectives: int = 0
    active_agents: int = 0
    pending_approvals: int = 0
    total_cost_usd_24h: float = 0.0
    total_tokens_24h: int = 0
    error_rate_pct: float = 0.0
    avg_latency_ms: float = 0.0
    circuit_breakers_tripped: int = 0


class ControlRoom:
    """Central observability and control interface."""

    def __init__(self) -> None:
        self._objectives: dict[str, ObjectiveStatusV1] = {}
        self._timeline: list[TimelineEventV1] = []
        self._commands: list[ControlCommandV1] = []
        self._command_results: dict[str, ControlResultV1] = {}

    # ── Status updates ───────────────────────────────────────────────────

    def update_objective(self, status: ObjectiveStatusV1) -> None:
        self._objectives[status.objective_id] = status

    def remove_objective(self, objective_id: str) -> None:
        self._objectives.pop(objective_id, None)

    def record_event(self, event: TimelineEventV1) -> None:
        self._timeline.append(event)

    # ── Queries ──────────────────────────────────────────────────────────

    def get_objective(self, objective_id: str) -> ObjectiveStatusV1 | None:
        return self._objectives.get(objective_id)

    def list_objectives(
        self, *, tenant_id: str | None = None, state: str | None = None,
    ) -> list[ObjectiveStatusV1]:
        items = list(self._objectives.values())
        if tenant_id:
            items = [o for o in items if o.tenant_id == tenant_id]
        if state:
            items = [o for o in items if o.state == state]
        return items

    def get_timeline(
        self, *, objective_id: str | None = None, limit: int = 100,
    ) -> list[TimelineEventV1]:
        events = self._timeline
        if objective_id:
            events = [e for e in events if e.objective_id == objective_id]
        return events[-limit:]

    def get_health(self) -> SystemHealthV1:
        objectives = list(self._objectives.values())
        active = [o for o in objectives if o.state in ("planning", "executing", "verifying")]
        queued = [o for o in objectives if o.state == "admitted"]
        approvals = sum(o.pending_approvals for o in objectives)
        total_cost = sum(o.cost_usd for o in objectives)
        total_tokens = sum(o.tokens_used for o in objectives)
        errors = [e for e in self._timeline[-1000:] if e.severity in ("error", "critical")]
        recent = self._timeline[-1000:]
        error_rate = (len(errors) / max(len(recent), 1)) * 100

        return SystemHealthV1(
            active_objectives=len(active),
            queued_objectives=len(queued),
            pending_approvals=approvals,
            total_cost_usd_24h=total_cost,
            total_tokens_24h=total_tokens,
            error_rate_pct=round(error_rate, 2),
        )

    # ── Human controls ───────────────────────────────────────────────────

    def issue_command(self, command: ControlCommandV1) -> ControlResultV1:
        self._commands.append(command)
        result = self._dispatch_command(command)
        self._command_results[command.command_id] = result
        self.record_event(TimelineEventV1(
            timestamp=command.issued_at,
            objective_id=command.target_id if command.target_type == "objective" else "",
            event_type=f"control.{command.action}",
            summary=f"{command.action} issued by {command.issued_by}: {command.reason}",
            severity="warn" if command.action in ("stop", "rollback") else "info",
        ))
        return result

    def _dispatch_command(self, command: ControlCommandV1) -> ControlResultV1:
        if command.target_type == "objective":
            obj = self._objectives.get(command.target_id)
            if obj is None:
                return ControlResultV1(command.command_id, False, "objective not found")

            if command.action == "pause":
                self._objectives[command.target_id] = ObjectiveStatusV1(
                    **{**obj.__dict__, "state": "paused"}  # type: ignore[arg-type]
                )
                return ControlResultV1(command.command_id, True, "objective paused")
            if command.action == "resume":
                self._objectives[command.target_id] = ObjectiveStatusV1(
                    **{**obj.__dict__, "state": "executing"}  # type: ignore[arg-type]
                )
                return ControlResultV1(command.command_id, True, "objective resumed")
            if command.action == "stop":
                self._objectives[command.target_id] = ObjectiveStatusV1(
                    **{**obj.__dict__, "state": "failed"}  # type: ignore[arg-type]
                )
                return ControlResultV1(command.command_id, True, "objective stopped")

        return ControlResultV1(command.command_id, True, f"{command.action} accepted")

    def get_command_history(self) -> list[ControlCommandV1]:
        return list(self._commands)


__all__ = [
    "ControlAction", "ObjectiveStatusV1", "TimelineEventV1",
    "ControlCommandV1", "ControlResultV1", "SystemHealthV1", "ControlRoom",
]
