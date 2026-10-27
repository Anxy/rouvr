"""Persona event discipline: dedup, traceability, bounded failure (Stage 6).

Messages request work or report results; they never drive progression
directly (Temporal owns that). Guarantees, all unit-tested:

- Deduplication: re-applying a message_id yields no new effects.
- Traceability: every effect carries trace_id + message_id.
- Bounded failure: reports referencing unknown requests are parked,
  never executed; unknown kinds rejected.
- Reorder-safety: report-before-request parks until the request
  arrives; applying the same shuffled batch twice converges.
"""

from __future__ import annotations

from typing import Any

KNOWN_KINDS = frozenset({"work_request", "work_report", "verdict"})


class EventLog:
    """In-memory application of persona messages (fakes in tests)."""

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.requests: dict[str, dict[str, Any]] = {}
        self.parked: list[dict[str, Any]] = []
        self.effects: list[dict[str, Any]] = []

    def apply(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        """Apply one message; returns new effects (possibly empty)."""
        mid = str(message.get("message_id", ""))
        if not mid or mid in self.seen:
            return []
        kind = str(message.get("kind", ""))
        if kind not in KNOWN_KINDS:
            return []
        self.seen.add(mid)
        trace = str(message.get("trace_id", ""))
        new: list[dict[str, Any]] = []
        if kind == "work_request":
            rid = str(message.get("request_id", mid))
            self.requests[rid] = message
            new.append({"effect": "requested", "request_id": rid,
                        "trace_id": trace, "message_id": mid})
            for report in list(self.parked):
                if str(report.get("request_id")) == rid:
                    self.parked.remove(report)
                    new.append({"effect": "unparked", "request_id": rid,
                                "trace_id": trace, "message_id": mid})
        else:
            rid = str(message.get("request_id", ""))
            if rid not in self.requests:
                self.parked.append(message)
                new.append({"effect": "parked", "request_id": rid,
                            "trace_id": trace, "message_id": mid})
            else:
                new.append({"effect": "recorded", "kind": kind,
                            "request_id": rid, "trace_id": trace,
                            "message_id": mid})
        self.effects.extend(new)
        return new
