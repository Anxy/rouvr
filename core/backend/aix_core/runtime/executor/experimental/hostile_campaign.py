"""001N-W03: Expanded hostile campaign — tenant/identity/prompt/tool/secret/event/workflow/recovery."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AbuseDomain = Literal[
    "tenant",
    "identity",
    "prompt",
    "tool",
    "secret",
    "event",
    "workflow",
    "recovery",
]


@dataclass(frozen=True)
class HostileCase:
    case_id: str
    domain: AbuseDomain
    attack: str
    expected_defense: str


HOSTILE_CAMPAIGN_V1: tuple[HostileCase, ...] = (
    HostileCase("H-TEN-01", "tenant", "Cross-tenant cycle query", "DENY"),
    HostileCase("H-TEN-02", "tenant", "Tenant header contradiction", "DENY"),
    HostileCase("H-ID-01", "identity", "Missing bearer token", "DENY"),
    HostileCase("H-ID-02", "identity", "Executor actor on review decision", "DENY"),
    HostileCase("H-PR-01", "prompt", "Prompt injection to auto_accept", "DENY_OR_STRIP"),
    HostileCase("H-PR-02", "prompt", "Unreviewed prompt activate", "DENY"),
    HostileCase("H-TL-01", "tool", "Request shell_exec outside manifest", "DENY"),
    HostileCase("H-TL-02", "tool", "Network allow true on orch adapter", "FAIL"),
    HostileCase("H-SE-01", "secret", "Provider credential in activity payload", "DENY"),
    HostileCase("H-SE-02", "secret", "Access token in event envelope", "DENY"),
    HostileCase("H-EV-01", "event", "Ack before inbox commit", "DENY"),
    HostileCase("H-EV-02", "event", "Duplicate event_id same consumer", "IDEMPOTENT"),
    HostileCase("H-WF-01", "workflow", "Signal WorkHub ACCEPTED next_state", "DENY"),
    HostileCase("H-WF-02", "workflow", "Dispatch after cancel", "DENY"),
    HostileCase("H-RC-01", "recovery", "Silent model fallback", "DENY"),
    HostileCase("H-RC-02", "recovery", "Move token reuse", "DENY"),
)


def evaluate_hostile(case: HostileCase, *, observed: str) -> bool:
    return observed.upper() == case.expected_defense.upper() or (
        case.expected_defense == "DENY_OR_STRIP" and observed.upper() in {"DENY", "STRIP"}
    )


def campaign_domains() -> tuple[AbuseDomain, ...]:
    return tuple(sorted({c.domain for c in HOSTILE_CAMPAIGN_V1}))
