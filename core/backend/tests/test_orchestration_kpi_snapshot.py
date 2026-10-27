from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
SCRIPT = ROOT / "projects" / "AIX" / "hyeve-aix-service" / "backend" / "scripts" / "build_orchestration_kpi_snapshot.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("aix_kpi_snapshot_mod", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_build_snapshot_computes_targets_and_unknowns() -> None:
    mod = _load_module()
    runlogs = [
        {
            "generated_at_utc": "2026-03-03T10:00:00+00:00",
            "success": False,
            "results": [{"action": "planner_step", "elapsed_ms": 5000, "correlation_id": "c1"}],
        },
        {
            "generated_at_utc": "2026-03-03T10:10:00+00:00",
            "success": True,
            "results": [{"action": "planner_step", "elapsed_ms": 5500, "correlation_id": "c2"}],
        },
        {
            "generated_at_utc": "2026-03-03T10:20:00+00:00",
            "success": True,
            "results": [{"action": "devexecutor_run", "elapsed_ms": 9000, "correlation_id": "c3"}],
        },
    ]
    snapshot = mod.build_snapshot(runlogs=runlogs)
    assert snapshot["sample_size"]["runlogs"] == 3
    assert snapshot["kpis"]["workflow_success_rate"] > 0
    assert snapshot["kpis"]["p95_planner_latency_ms"] is not None
    assert snapshot["kpis"]["p95_coder_step_latency_ms"] is not None
    assert snapshot["kpis"]["mttr_minutes"] is not None
    assert snapshot["targets"]["traceability_coverage_gte_95pct"] is True


def test_build_snapshot_marks_unknown_targets_when_data_missing() -> None:
    mod = _load_module()
    runlogs = [{"generated_at_utc": "2026-03-03T10:00:00+00:00", "success": True, "results": []}]
    snapshot = mod.build_snapshot(runlogs=runlogs)
    unknowns = set(snapshot["unknown_targets"])
    assert "p95_planner_latency_lte_6000ms" in unknowns
    assert "p95_coder_step_latency_lte_12000ms" in unknowns
    assert "mttr_minutes_lte_15" in unknowns
