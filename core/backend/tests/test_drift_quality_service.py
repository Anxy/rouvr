from __future__ import annotations

from backend.services.drift_quality_service import assess_action_map_drift_quality


def test_assess_action_map_drift_quality_accepts_high_signal() -> None:
    result = assess_action_map_drift_quality(
        provider_before=["slavx"],
        provider_after=["slavx", "onlineai"],
        codeintel_context={
            "entities_total": 4,
            "citations": [{"file": "a.py"}, {"file": "b.py"}],
            "confidence_score": 0.92,
        },
    )
    assert result["should_propose"] is True
    assert result["quality_score"] >= result["threshold"]
    assert result["provider_delta"] == 1


def test_assess_action_map_drift_quality_blocks_low_signal() -> None:
    result = assess_action_map_drift_quality(
        provider_before=["slavx"],
        provider_after=["slavx", "onlineai"],
        codeintel_context={
            "entities_total": 1,
            "citations": [],
            "confidence_score": 0.22,
        },
    )
    assert result["should_propose"] is False
    assert result["quality_score"] < result["threshold"]
    assert "DRIFT_SIGNAL_BELOW_THRESHOLD" in result["reasons"]
