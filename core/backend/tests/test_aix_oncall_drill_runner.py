from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "aix_oncall_drill_runner.py"
    spec = importlib.util.spec_from_file_location("aix_oncall_drill_runner", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scenario_rows_have_expected_signal() -> None:
    mod = _load_module()
    failure = mod._scenario_rows("failure_mode")
    dlq = mod._scenario_rows("dlq_backlog")
    lease = mod._scenario_rows("lease_churn")
    assert failure[0]["decision_status"] == "blocked"
    assert dlq[0]["decision_payload_json"]["dlq_count"] > 0
    assert lease[0]["retry_count"] >= 3
