"""Hermetic Stage-1 tests: chain, resolution, bundle (no I/O)."""

import pytest

from backend.session_factory.bundle import build_task_bundle, workspace_path
from backend.session_factory.chain import build_attempt_record, validate_chain
from backend.session_factory.resolve import (
    configure_supported_models,
    reset_supported_models,
    resolve_assignment,
)

ROWS = [
    {"capability_key": "code-edit", "executor_binding": "muse-lane",
     "model": "Muse", "status": "ACTIVE"},
    {"capability_key": "quota", "executor_binding": "spark-lane",
     "model": "", "status": "ACTIVE"},
    {"capability_key": "retired", "executor_binding": "muse-lane",
     "model": "Muse", "status": "RETIRED"},
    {"capability_key": "weird", "executor_binding": "muse-lane",
     "model": "nope-9", "status": "ACTIVE"},
]


def setup_function(_):
    reset_supported_models()


def test_chain_build_and_validate() -> None:
    rec = build_attempt_record(
        attempt_id="att-1", tenant_id="t", objective_id="obj-1",
        session_id="env-1", mandate_id="m-1",
    )
    assert rec["status"] == "ADMITTED"
    assert rec["workflow_id"] == ""
    missing = validate_chain({**rec, "evidence_ref": "ev/1.md"})
    assert [m for m in missing if m != "workflow_id"] == []


def test_chain_reports_missing_in_order() -> None:
    assert validate_chain({})[0] == "objective_id"
    assert validate_chain({"objective_id": "o", "session_id": "s"})[0] == "mandate_id"


def test_resolve_known_and_executor_default() -> None:
    assert resolve_assignment(ROWS, "code-edit") == ("ok", "muse-lane", "Muse")
    assert resolve_assignment(ROWS, "quota") == ("ok", "spark-lane", "spark-1.3")


def test_resolve_unknown_retired_disallowed_stop() -> None:
    assert resolve_assignment(ROWS, "nope") == ("needs_classification", None, None)
    assert resolve_assignment(ROWS, "retired") == ("needs_classification", None, None)
    assert resolve_assignment(ROWS, "weird") == ("model_not_allowed", "muse-lane", None)
    configure_supported_models({"Muse"})
    assert resolve_assignment(ROWS, "quota")[0] == "model_not_allowed"


def test_workspace_isolation_and_traversal_reject() -> None:
    a = workspace_path(root="/w", tenant_id="t", mandate_id="m1", attempt_id="a1")
    b = workspace_path(root="/w", tenant_id="t", mandate_id="m2", attempt_id="a1")
    assert a != b and a.startswith("/w/t/m1/")
    with pytest.raises(ValueError):
        workspace_path(root="/w", tenant_id="t", mandate_id="../x", attempt_id="a1")


def test_bundle_carries_context() -> None:
    rec = build_attempt_record(
        attempt_id="a1", tenant_id="t", objective_id="o",
        session_id="s", mandate_id="m", executor="muse-lane", model="Muse",
    )
    bundle = build_task_bundle(
        attempt=rec, objective_text="do x", criteria=["green"],
        constraints=["no main push"], evidence_path="ev/1.md",
        workspace="/w/t/m/a1",
    )
    assert bundle["workspace"] == "/w/t/m/a1"
    assert bundle["acceptance_criteria"] == ["green"]
    assert bundle["model"] == "Muse"
