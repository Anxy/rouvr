"""Isolated integration runner for the maintained consumer (#72 proofs).

Runs the four #72 acceptance elements against in-repo doubles only — no
live Temporal, no live WorkHub, no broker process::

    python -m backend.aix_event_consumer.integration_runner --scenario all

``run_proofs`` is the importable entry; ``main`` is the CLI entry. Proof
details are deterministic (ids, counts, queue/workflow names) so repeated
runs compare equal.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from .admission import DefaultAdmissionPolicy
from .consumer import AixEventConsumer, DictEventSource
from .cursor import DurableCursor
from .temporal import EXECUTION_QUEUE, WORKFLOW_TYPE, InMemoryTemporalDouble

PROOF_NAMES = (
    "durable_cursor_recovery",
    "duplicate_and_reorder_handling",
    "admission_rejection",
    "deterministic_temporal_start_signal",
)

PLAN = "a" * 64
TENANT = "t-cycle-1"


def make_event(event_id: str, seq: int = 0, **over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_id": event_id,
        "event_type": "workhub.task.updated",
        "tenant_id": TENANT,
        "task_number": 119,
        "objective_id": "objective-1",
        "cycle_id": "cycle-1",
        "correlation_id": event_id,
        "candidate_sha": "b" * 40,
        "plan_sha256": PLAN,
        "seq": seq,
    }
    payload.update(over)
    return payload


def _proof(name: str, passed: bool, detail: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def prove_cursor_recovery(state_dir: Path) -> dict[str, Any]:
    cursor_path = state_dir / "cursor.json"
    consumer = AixEventConsumer(
        event_source=DictEventSource([make_event("ev-1", seq=1)]),
        admission=DefaultAdmissionPolicy(),
        cursor=DurableCursor(cursor_path),
        temporal=InMemoryTemporalDouble(),
    )
    first = consumer.run_once()
    restarted = AixEventConsumer(
        event_source=DictEventSource(
            [make_event("ev-1", seq=1), make_event("ev-2", seq=2)]
        ),
        admission=DefaultAdmissionPolicy(),
        cursor=DurableCursor(cursor_path),
        temporal=InMemoryTemporalDouble(),
    )
    outcome = restarted.run_once()
    # Crash window: cursor file durable, Temporal handles lost entirely.
    crashed = AixEventConsumer(
        event_source=DictEventSource([]),
        admission=DefaultAdmissionPolicy(),
        cursor=DurableCursor(cursor_path),
        temporal=InMemoryTemporalDouble(),
    )
    report = crashed.recover()
    passed = (
        first.started == 1
        and outcome.duplicates == 1
        and outcome.started == 1
        # Both events share one deterministic workflow: requeuing ev-1
        # restores the handle, so ev-2 correctly needs no second action.
        and report.requeued == ["ev-1"]
    )
    return _proof(
        "durable_cursor_recovery",
        passed,
        {
            "first_started": first.started,
            "restart_duplicates": outcome.duplicates,
            "restart_started": outcome.started,
            "requeued": report.requeued,
        },
    )


def prove_dupe_reorder(state_dir: Path) -> dict[str, Any]:
    temporal: InMemoryTemporalDouble = InMemoryTemporalDouble()
    consumer = AixEventConsumer(
        event_source=DictEventSource(
            [
                make_event("ev-2", seq=2),
                make_event("ev-1", seq=1),
                make_event("ev-2", seq=2),
                make_event("ev-1", seq=1),
            ]
        ),
        admission=DefaultAdmissionPolicy(),
        cursor=DurableCursor(state_dir / "cursor.json"),
        temporal=temporal,
    )
    outcome = consumer.run_once()
    passed = (
        outcome.started == 1
        and outcome.signalled == 1
        and outcome.duplicates == 2
        and len(temporal.starts) == 1
        and len(temporal.signals) == 1
    )
    return _proof(
        "duplicate_and_reorder_handling",
        passed,
        {
            "started": outcome.started,
            "signalled": outcome.signalled,
            "duplicates": outcome.duplicates,
            "temporal_starts": len(temporal.starts),
            "temporal_signals": len(temporal.signals),
        },
    )


def prove_admission_rejection(state_dir: Path) -> dict[str, Any]:
    temporal: InMemoryTemporalDouble = InMemoryTemporalDouble()
    consumer = AixEventConsumer(
        event_source=DictEventSource([make_event("ev-1")]),
        admission=DefaultAdmissionPolicy(allowed_tenants=("no-such-tenant",)),
        cursor=DurableCursor(state_dir / "cursor.json"),
        temporal=temporal,
    )
    outcome = consumer.run_once()
    passed = (
        outcome.rejected == 1
        and outcome.started == 0
        and outcome.signalled == 0
        and len(temporal.starts) == 0
        and not consumer.cursor.is_processed("ev-1")
    )
    reason = outcome.results[0].get("reason") if outcome.results else None
    return _proof(
        "admission_rejection",
        passed,
        {"rejected": outcome.rejected, "reason": reason},
    )


def prove_deterministic_temporal(state_dir: Path) -> dict[str, Any]:
    first_temporal: InMemoryTemporalDouble = InMemoryTemporalDouble()
    first = AixEventConsumer(
        event_source=DictEventSource([make_event("ev-1", seq=1)]),
        admission=DefaultAdmissionPolicy(),
        cursor=DurableCursor(state_dir / "cursor.json"),
        temporal=first_temporal,
    )
    first.run_once()
    second_temporal: InMemoryTemporalDouble = InMemoryTemporalDouble()
    second = AixEventConsumer(
        event_source=DictEventSource(
            [make_event("ev-1", seq=1), make_event("ev-2", seq=2)]
        ),
        admission=DefaultAdmissionPolicy(),
        cursor=DurableCursor(state_dir / "cursor-second.json"),
        temporal=second_temporal,
    )
    outcome = second.run_once()
    (first_id, first_record), = first_temporal.starts.items()
    (second_id, second_record), = second_temporal.starts.items()
    passed = (
        first_id == second_id
        and first_record["task_queue"] == EXECUTION_QUEUE == "aix.execution.current"
        and first_record["workflow_type"] == WORKFLOW_TYPE == "AixObjectiveCycleWorkflow"
        and second_record["run_id"] == "run-0001"
        and outcome.signalled == 1
        and second_temporal.signals[0]["signal_name"] == "aix_event_received"
    )
    return _proof(
        "deterministic_temporal_start_signal",
        passed,
        {
            "task_queue": first_record["task_queue"],
            "workflow_type": first_record["workflow_type"],
            "workflow_id": first_id,
            "second_run_signalled": outcome.signalled,
        },
    )


_PROVERS = {
    "durable_cursor_recovery": prove_cursor_recovery,
    "duplicate_and_reorder_handling": prove_dupe_reorder,
    "admission_rejection": prove_admission_rejection,
    "deterministic_temporal_start_signal": prove_deterministic_temporal,
}


def run_proofs(state_dir: str | Path | None = None) -> dict[str, Any]:
    """Run every proof in an isolated state dir; deterministic report."""
    if state_dir is None:
        tmp = tempfile.TemporaryDirectory(prefix="aix-consumer-proofs-")
        root = Path(tmp.name)
    else:
        tmp = None
        root = Path(state_dir)
        root.mkdir(parents=True, exist_ok=True)
    try:
        proofs: dict[str, dict[str, Any]] = {}
        for name in PROOF_NAMES:
            proofs[name] = _PROVERS[name](root / name.replace("_", "-"))
        return {"passed": all(item["passed"] for item in proofs.values()), "proofs": proofs}
    finally:
        if tmp is not None:
            tmp.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AIX consumer isolated proof runner")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument(
        "--scenario", default="all", choices=("all", *PROOF_NAMES),
    )
    args = parser.parse_args(argv)
    if args.scenario == "all":
        report = run_proofs(args.state_dir)
    else:
        root = Path(args.state_dir) if args.state_dir else Path(tempfile.mkdtemp())
        report = {"proofs": {args.scenario: _PROVERS[args.scenario](root)}}
        report["passed"] = bool(report["proofs"][args.scenario]["passed"])
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
