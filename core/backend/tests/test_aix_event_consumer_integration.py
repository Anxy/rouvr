"""Isolated integration proofs for the maintained consumer (#119 -> #72).

Runs the four #72 acceptance elements against the in-repo doubles via
``backend.aix_event_consumer.integration_runner`` — no live Temporal, no
live WorkHub, no broker process:

- durable cursor recovery
- duplicate and reorder handling
- admission rejection
- deterministic Temporal start or signal
"""
from __future__ import annotations

from pathlib import Path

from backend.aix_event_consumer.integration_runner import PROOF_NAMES, run_proofs


def test_all_four_proofs_pass_isolated(tmp_path: Path) -> None:
    report = run_proofs(state_dir=tmp_path / "state")

    assert report["passed"] is True
    assert sorted(report["proofs"]) == sorted(PROOF_NAMES)
    for name in PROOF_NAMES:
        assert report["proofs"][name]["passed"] is True, name


def test_proofs_are_deterministic(tmp_path: Path) -> None:
    first = run_proofs(state_dir=tmp_path / "a")
    second = run_proofs(state_dir=tmp_path / "b")

    assert first["proofs"] == second["proofs"]


def test_proof_report_names_temporal_queue_and_workflow(tmp_path: Path) -> None:
    report = run_proofs(state_dir=tmp_path / "state")
    temporal_proof = report["proofs"]["deterministic_temporal_start_signal"]

    assert temporal_proof["detail"]["task_queue"] == "aix.execution.current"
    assert temporal_proof["detail"]["workflow_type"] == "AixObjectiveCycleWorkflow"
