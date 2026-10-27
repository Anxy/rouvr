"""F1-P5: WorkHub bridge read-contract mapping (F1-AC-05)."""

from __future__ import annotations

from backend.aix_core.framework.work.models import ExternalWorkRef
from backend.aix_core.framework.work.workhub import WorkHubTaskBridge
from backend.aix_core.runtime.objective.contracts import BudgetV1, ObjectiveEnvelopeV1


def test_resolve_external_ref_is_read_only() -> None:
    ref = ExternalWorkRef(
        system="workhub",
        external_task_id="task-99",
        project_id="proj-1",
        correlation_id="corr-1",
    )
    resolved = WorkHubTaskBridge.resolve_external_ref(ref)
    assert resolved is not None
    assert resolved.authority_allowed is True
    assert resolved.external_task_id == "task-99"
    assert "task-99" in (resolved.read_contract_endpoint or "")
    WorkHubTaskBridge.assert_read_only()


def test_from_envelope_maps_workhub_fields() -> None:
    envelope = ObjectiveEnvelopeV1(
        tenant_id="t1",
        actor_ref="a1",
        project_id="p1",
        workhub_task_ref="wh-42",
        task_class="verification_only",
        definition_of_done="done",
        acceptance_criteria=("ok",),
        repository_scope=("aix",),
        budget_ceiling=BudgetV1(
            time_seconds=30, tokens=100, cost_usd=0.1, retries=0, steps=1, concurrency=1
        ),
        idempotency_key="k1",
        correlation_id="c1",
    )
    contract = WorkHubTaskBridge.from_envelope(envelope)
    assert contract.external_task_id == "wh-42"
    assert contract.project_id == "p1"
    assert contract.correlation_id == "c1"
    assert contract.system == "workhub"


def test_lifecycle_writes_remain_blocked_by_default() -> None:
    # Raises only if a lifecycle write is unexpectedly authorized without flags.
    WorkHubTaskBridge.assert_read_only()
