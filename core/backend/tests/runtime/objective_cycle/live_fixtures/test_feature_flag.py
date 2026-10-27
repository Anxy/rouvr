from datetime import date, timedelta

from backend.tests.runtime.objective_cycle.live_fixtures.feature_flag import (
    FEATURE,
    is_feature_enabled,
)


def test_feature_flag_enabled_and_version() -> None:
    # Baseline assertions match the checked-in FEATURE defaults. The live multi-file
    # objective must update BOTH this test and feature_flag.py together.
    assert FEATURE["enabled"] is True
    assert FEATURE["version"] == 3


def test_feature_flag_with_future_expiry_is_active() -> None:
    feature = {**FEATURE, "expires_at": "2025-01-02"}

    assert is_feature_enabled(feature, today=date(2025, 1, 1)) is True


def test_feature_flag_with_past_expiry_is_inactive() -> None:
    feature = {**FEATURE, "expires_at": "2024-12-31"}

    assert is_feature_enabled(feature, today=date(2025, 1, 1)) is False


def test_feature_flag_without_expiry_preserves_enabled_behavior() -> None:
    feature = {"name": "no_expiry", "enabled": True, "version": 1}

    assert is_feature_enabled(feature, today=date.today() + timedelta(days=365)) is True
    assert is_feature_enabled({**feature, "expires_at": None}, today=date.today() + timedelta(days=365)) is True
