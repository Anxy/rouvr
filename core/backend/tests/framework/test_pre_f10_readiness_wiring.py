"""Pre-F10 readiness — F1/F5/F6/F7/F9 flag OFF/ON wiring parity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.aix_core.framework.agent.role_map import agent_binding_enabled, to_framework_role
from backend.aix_core.framework.integration.hyeve.llm_ingress import hyeve_capabilities_enabled
from backend.aix_core.framework.recovery.runtime_bridge import decide_retry_routed
from backend.aix_core.framework.verification.runtime_bridge import (
    evaluate_completion_gates_routed,
    verification_catalog_enabled,
)
from backend.aix_core.framework.work import work_adapters_enabled
from backend.aix_core.framework.work.adapters import WorkDomainAdapter
from backend.aix_core.runtime.agents.handoff import HandoffError, HandoffRequestV1, validate_handoff
from backend.aix_core.runtime.objective.contracts import (
    BudgetV1,
    ObjectiveEnvelopeV1,
    PlanRevisionV1,
    StepContractV1,
)
from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.recovery.retry_repair import decide_retry
from backend.aix_core.runtime.verification.failure_taxonomy import FailureClassificationV1
from backend.hyeve_aix_service.config import FrameworkFlagsSnapshot, settings


def test_flags_default_off() -> None:
    assert work_adapters_enabled(settings) is False
    assert verification_catalog_enabled(settings) is False
    assert agent_binding_enabled(settings) is False
    assert hyeve_capabilities_enabled(settings) is False
    assert bool(getattr(settings, "AIX_FRAMEWORK_DURABLE_CHECKPOINT", True)) is False


def _minimal_envelope(objective_id: str = "obj_1") -> ObjectiveEnvelopeV1:
    return ObjectiveEnvelopeV1(
        id=objective_id,
        tenant_id="t1",
        actor_ref="human-1",
        project_id="p1",
        workhub_task_ref="wh-1",
        task_class="verification_only",
        definition_of_done="Report status",
        acceptance_criteria=("status reported",),
        repository_scope=("aix",),
        budget_ceiling=BudgetV1(
            time_seconds=60, tokens=1000, cost_usd=1.0, retries=1, steps=3, concurrency=1
        ),
        idempotency_key="idem-1",
    )


def test_f1_work_projection_flag_off_no_store() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        framework_flags=FrameworkFlagsSnapshot(work_adapters=False),
    )
    env = _minimal_envelope()
    backend._envelopes[env.id] = env  # noqa: SLF001
    # Without a plan, still exercise the gate — no store when flag off.
    from backend.aix_core.framework.work.adapters import WorkDomainAdapter as _
    assert env.id not in backend._works  # noqa: SLF001
    backend._maybe_project_work(  # noqa: SLF001
        PlanRevisionV1(
            plan_id="plan_1",
            objective_id=env.id,
            cycle_id="cyc_1",
            revision=1,
            status="ACCEPTED",
            steps=(
                StepContractV1(
                    cycle_id="cyc_1",
                    tenant_id="t1",
                    step_index=0,
                    purpose="seeded",
                    target_scope=("helper.py",),
                    expected_output_schema_ref="aix.step_contract.v1.generic_edit",
                    acceptance_criteria=("ok",),
                    validation_commands=("python -m py_compile helper.py",),
                    assigned_role="executor",
                    step_budget=BudgetV1(
                        time_seconds=3600,
                        tokens=100_000,
                        cost_usd=5.0,
                        retries=2,
                        steps=10,
                        concurrency=1,
                    ),
                    obligation_refs=(),
                    status="PENDING",
                ),
            ),
        )
    )
    assert env.id not in backend._works  # noqa: SLF001


def test_f1_work_projection_flag_on_stores_work() -> None:
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        framework_flags=FrameworkFlagsSnapshot(work_adapters=True),
    )
    env = _minimal_envelope()
    backend._envelopes[env.id] = env  # noqa: SLF001
    plan = PlanRevisionV1(
        plan_id="plan_1",
        objective_id=env.id,
        cycle_id="cyc_1",
        revision=1,
        status="ACCEPTED",
        steps=(
            StepContractV1(
                cycle_id="cyc_1",
                tenant_id="t1",
                step_index=0,
                purpose="seeded",
                target_scope=("helper.py",),
                expected_output_schema_ref="aix.step_contract.v1.generic_edit",
                acceptance_criteria=("ok",),
                validation_commands=("python -m py_compile helper.py",),
                assigned_role="executor",
                step_budget=BudgetV1(
                    time_seconds=3600,
                    tokens=100_000,
                    cost_usd=5.0,
                    retries=2,
                    steps=10,
                    concurrency=1,
                ),
                obligation_refs=(),
                status="PENDING",
            ),
        ),
    )
    backend._maybe_project_work(plan)  # noqa: SLF001
    assert env.id in backend._works  # noqa: SLF001
    work = backend._works[env.id]  # noqa: SLF001
    assert work.definition is not None
    direct = WorkDomainAdapter.work_from_envelope(env, None, plan=plan)
    assert work.work_id == direct.work_id


def test_f5_completion_gates_off_on_parity() -> None:
    from backend.aix_core.runtime.objective.discovery import DiscoveryReport

    discovery = DiscoveryReport(manifests={"README.md": "# hi"})
    kwargs = {
        "discovery": discovery,
        "diff_stats": {},
        "diff_text": "",
        "test_result": None,
        "already_satisfied": True,
        "already_satisfied_reason": "objective already satisfied by existing repository content",
        "already_satisfied_evidence_refs": ("file://evidence",),
        "task_family": "READ",
        "objective": "Report status",
    }
    off = evaluate_completion_gates_routed(use_catalog=False, **kwargs)
    on = evaluate_completion_gates_routed(use_catalog=True, **kwargs)
    assert off.passed == on.passed
    assert off.gates.get("source_changed") == on.gates.get("source_changed")


def test_f6_retry_adapter_parity() -> None:
    classification = FailureClassificationV1("RETRYABLE", ("TIMEOUT",))
    budget = BudgetV1(
        time_seconds=3600, tokens=1000, cost_usd=1.0, retries=2, steps=10, concurrency=1
    )
    direct = decide_retry(
        classification, current_attempt_number=1, remaining_budget=budget
    )
    routed = decide_retry_routed(
        classification, current_attempt_number=1, remaining_budget=budget
    )
    assert direct.disposition == routed.disposition
    assert direct.next_attempt_number == routed.next_attempt_number


def test_f7_handoff_flag_off_uses_registry() -> None:
    req = HandoffRequestV1(
        handoff_id="h1",
        from_agent_id="a",
        from_role="planner",
        to_role="coder",
        cycle_id="c1",
        step_id="s1",
        objective="do work",
    )
    validate_handoff(req, settings=MagicMock(AIX_FRAMEWORK_AGENT_BINDING=False))


def test_f7_handoff_flag_on_uses_framework() -> None:
    req = HandoffRequestV1(
        handoff_id="h1",
        from_agent_id="a",
        from_role="planner",
        to_role="coder",
        cycle_id="c1",
        step_id="s1",
        objective="do work",
    )
    validate_handoff(req, settings=MagicMock(AIX_FRAMEWORK_AGENT_BINDING=True))
    assert to_framework_role("coder") is not None


def test_f7_handoff_flag_on_rejects_unmapped_roles() -> None:
    req = HandoffRequestV1(
        handoff_id="h1",
        from_agent_id="a",
        from_role="researcher",
        to_role="operations_agent",
        cycle_id="c1",
        step_id="s1",
        objective="do work",
    )
    with pytest.raises(HandoffError) as exc:
        validate_handoff(req, settings=MagicMock(AIX_FRAMEWORK_AGENT_BINDING=True))
    assert exc.value.code == "FRAMEWORK_ROLE_UNMAPPED"


@pytest.mark.asyncio
async def test_f9_governed_chat_flag_on_uses_adapter() -> None:
    from backend.aix_core.framework.integration.hyeve.llm_ingress import governed_chat_completion
    from backend.infrastructure.model_gateway.hyeve_integration_client import ChatMessage

    fake = MagicMock()
    fake.output_text = "{}"
    fake.model = "m"
    fake.provider = "p"
    with patch(
        "backend.aix_core.framework.integration.hyeve.adapters.integration_llm.chat_completion",
        new=AsyncMock(return_value=fake),
    ) as mocked:
        with patch(
            "backend.aix_core.framework.integration.hyeve.adapters.integration_llm.GovernedProviderRouter"
        ) as Router:
            route = MagicMock()
            route.disposition = "ALLOW"
            route.route = MagicMock(provider_ref="openrouter", model_ref="m")
            route.reason_codes = ()
            Router.return_value.resolve.return_value = route
            Router.return_value.resolve_role.return_value = route
            out = await governed_chat_completion(
                role="planner",
                messages=[ChatMessage(role="system", content="hi")],
                tenant_id="t",
                actor_id="a",
                authorization_header="Bearer x",
                model_ref="m",
                use_hyeve_adapter=True,
            )
            assert out is fake
            assert mocked.await_count == 1
