from __future__ import annotations

from backend.services.business_kpi_service import compute_business_kpis


def test_compute_business_kpis_returns_traceable_values() -> None:
    result = compute_business_kpis(
        definition_version="v1",
        source_metrics={
            "total_dispatches": 20,
            "successful_dispatches": 18,
            "total_cycle_time_ms": 36000,
            "completed_runs": 12,
        },
    )
    assert result["kpis"]["dispatch_success_rate"] == 0.9
    assert result["kpis"]["mean_cycle_time_ms"] == 3000.0
    assert result["trace"]["dispatch_success_rate"]["inputs"]["total_dispatches"] == 20
    assert len(result["reproducibility_hash"]) == 64
