"""Wiring proof: the consumer targets the registered worker surface (#119).

Asserts the consumer's queue/workflow constants match the canonical Temporal
runtime (the manager ``run_temporal_worker.py`` runs) and the workflow input
it builds validates against the canonical contract.
"""
from __future__ import annotations

from backend.aix_event_consumer.temporal import (
    EXECUTION_QUEUE,
    WORKFLOW_TYPE,
    build_action,
)
from backend.infrastructure.temporal import runtime as temporal_runtime
from backend.infrastructure.temporal.queue_strategy import build_queue_binding
from backend.workflows.temporal.objective_cycle_contracts import (
    ObjectiveCycleWorkflowInput,
)
from backend.workflows.temporal.objective_cycle_workflow import (
    AixObjectiveCycleWorkflow,
)
from backend.aix_event_consumer.production import OpsBusHttpEventSource


class _Settings:
    TEMPORAL_SEPARATE_QUEUES_ENABLED = False
    TEMPORAL_TASK_QUEUE_EXECUTION = "aix.execution.current"
    TEMPORAL_NAMESPACE = "hyeve-aix"


def test_consumer_targets_registered_workflow_and_queue() -> None:
    assert WORKFLOW_TYPE == temporal_runtime._WORKFLOW_NAME
    assert AixObjectiveCycleWorkflow in temporal_runtime._REGISTERED_WORKFLOW_CLASSES
    assert EXECUTION_QUEUE == "aix.execution.current"
    binding = build_queue_binding(_Settings())
    assert binding.execution_queue == EXECUTION_QUEUE


def test_built_workflow_input_validates_against_canonical_contract() -> None:
    from backend.aix_event_consumer.events import parse_event

    action = build_action(
        parse_event(
            {
                "event_id": "ev-1",
                "event_type": "workhub.task.updated",
                "tenant_id": "t-cycle-1",
                "task_number": 119,
                "objective_id": "objective-1",
                "cycle_id": "cycle-1",
                "candidate_sha": "b" * 40,
                "plan_sha256": "a" * 64,
            }
        )
    )
    assert action.workflow_input is not None
    validated = ObjectiveCycleWorkflowInput.model_validate(action.workflow_input)
    assert validated.tenant_id == "t-cycle-1"


def test_production_source_requires_service_auth() -> None:
    import pytest

    with pytest.raises(ValueError, match="OPSBUS_AUTH_REQUIRED"):
        OpsBusHttpEventSource(base_url="http://opsbus", token="", tenant_id="tenant")


def test_worker_entrypoint_wires_production_consumer() -> None:
    from pathlib import Path

    text = (Path(__file__).parents[1] / "scripts" / "run_temporal_worker.py").read_text()
    assert "OpsBusHttpEventSource" in text
    assert "TemporalClientAdapter" in text
    assert "consumer.run_once" in text
