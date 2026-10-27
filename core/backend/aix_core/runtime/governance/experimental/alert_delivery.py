"""AIX-E2E-CYCLE-001M-W04: external alert delivery.

This packet's own Forbidden Files And Actions list is explicit: "Fake alert receipt" is
disallowed. No real external channel (Novu, PagerDuty, email, Slack, etc.) is integrated
into this codebase's objective-cycle path. This module provides the trigger *decision*
(when a stuck/failed cycle warrants an alert) and a pluggable `AlertChannel` boundary a
real integration must implement — the only channel shipped here is `NullAlertChannel`,
which explicitly returns `delivered=False` and a reason naming itself as a non-external
stand-in. Nothing in this module claims, simulates, or returns a synthetic "delivered"
receipt on behalf of a real provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

AlertSeverity = Literal["warning", "critical"]


class AlertDeliveryError(ValueError):
    pass


@dataclass(frozen=True)
class AlertV1:
    tenant_id: str
    cycle_id: str
    severity: AlertSeverity
    reason: str
    correlation_id: str


@dataclass(frozen=True)
class AlertReceiptV1:
    delivered: bool
    channel_name: str
    provider_reference: str | None
    reason: str


class AlertChannel(Protocol):
    def send(self, alert: AlertV1) -> AlertReceiptV1: ...


class NullAlertChannel:
    """Explicit non-external stand-in. `send` always returns `delivered=False` — this
    class must never be mistaken for, or silently substituted as, a real provider."""

    channel_name = "null-channel-no-external-provider-configured"

    def send(self, alert: AlertV1) -> AlertReceiptV1:
        return AlertReceiptV1(
            delivered=False,
            channel_name=self.channel_name,
            provider_reference=None,
            reason="NO_EXTERNAL_CHANNEL_CONFIGURED: this session has no real alert provider integrated",
        )


def should_alert_stuck_cycle(*, is_stuck: bool, already_alerted: bool) -> bool:
    """An alert fires once per stuck episode, not on every check — `already_alerted`
    must be tracked by the caller (this function is stateless) and reset only when the
    cycle stops being stuck."""
    return is_stuck and not already_alerted


def should_alert_slo_breach(*, error_budget_remaining_ratio: float, breach_threshold: float = 0.0) -> bool:
    if not (0.0 <= breach_threshold <= 1.0):
        raise AlertDeliveryError("breach_threshold must be in [0, 1]")
    return error_budget_remaining_ratio <= breach_threshold


def dispatch_alert(alert: AlertV1, *, channel: AlertChannel) -> AlertReceiptV1:
    """Send `alert` through `channel` and return its receipt as-is — this function does
    not upgrade a `delivered=False` receipt, retry silently, or otherwise mask that
    external delivery did not happen."""
    return channel.send(alert)


__all__ = [
    "AlertSeverity",
    "AlertDeliveryError",
    "AlertV1",
    "AlertReceiptV1",
    "AlertChannel",
    "NullAlertChannel",
    "should_alert_stuck_cycle",
    "should_alert_slo_breach",
    "dispatch_alert",
]
