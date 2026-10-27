"""AIX-E2E-CYCLE-001M-W04 external alert delivery tests.

Per this packet's own Forbidden Files And Actions ("Fake alert receipt"), these tests
confirm the shipped `NullAlertChannel` never claims delivery — there is no real external
provider integrated in this session, and the tests exist to keep it that way honestly.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.governance.experimental.alert_delivery import (
    AlertDeliveryError,
    AlertV1,
    NullAlertChannel,
    dispatch_alert,
    should_alert_slo_breach,
    should_alert_stuck_cycle,
)


def _alert() -> AlertV1:
    return AlertV1(
        tenant_id="tenant-1",
        cycle_id="cycle-1",
        severity="critical",
        reason="cycle stuck for 400s",
        correlation_id="corr-1",
    )


class TestNullChannelNeverClaimsDelivery:
    def test_send_returns_delivered_false(self) -> None:
        receipt = NullAlertChannel().send(_alert())
        assert receipt.delivered is False

    def test_reason_names_itself_as_non_external(self) -> None:
        receipt = NullAlertChannel().send(_alert())
        assert "NO_EXTERNAL_CHANNEL_CONFIGURED" in receipt.reason

    def test_provider_reference_is_none(self) -> None:
        receipt = NullAlertChannel().send(_alert())
        assert receipt.provider_reference is None

    def test_dispatch_alert_does_not_upgrade_the_receipt(self) -> None:
        receipt = dispatch_alert(_alert(), channel=NullAlertChannel())
        assert receipt.delivered is False


class TestShouldAlertStuckCycle:
    def test_alerts_when_stuck_and_not_already_alerted(self) -> None:
        assert should_alert_stuck_cycle(is_stuck=True, already_alerted=False) is True

    def test_does_not_realert_same_episode(self) -> None:
        assert should_alert_stuck_cycle(is_stuck=True, already_alerted=True) is False

    def test_no_alert_when_not_stuck(self) -> None:
        assert should_alert_stuck_cycle(is_stuck=False, already_alerted=False) is False


class TestShouldAlertSloBreach:
    def test_alerts_at_or_below_threshold(self) -> None:
        assert should_alert_slo_breach(error_budget_remaining_ratio=0.0) is True
        assert should_alert_slo_breach(error_budget_remaining_ratio=-0.5) is True

    def test_no_alert_above_threshold(self) -> None:
        assert should_alert_slo_breach(error_budget_remaining_ratio=0.2) is False

    def test_invalid_threshold_rejected(self) -> None:
        with pytest.raises(AlertDeliveryError):
            should_alert_slo_breach(error_budget_remaining_ratio=0.5, breach_threshold=1.5)
