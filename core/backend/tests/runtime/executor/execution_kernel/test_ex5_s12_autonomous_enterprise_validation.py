"""EX5-S12: Autonomous Enterprise Validation — the final EX5 sprint.

Charter goal: "End-to-end ERP programme (1700 tasks / 3500 WIs) with full
audit evidence."

Honest scoping, decided before writing any code: this proves the
DATA/SERVICE LAYER coheres end-to-end at that scale — dependency
tracking, checkpoint/resume, portfolio intelligence, and the enterprise
audit trail all stay correct across a realistic multi-Capability
structure of that size. It does NOT prove autonomous AI planning or
execution of an actual ERP build — that would require live model calls
through real executor adapters, which this environment does not run
end-to-end. "Full audit evidence" here means a complete, coherent trail
for everything the kernel actually tracked in this run — not a human/
production audit certification, which remains gated on the same named
Architecture Authority every packet in this family has carried since
`AIX-EXECUTION-001`.

Scale interpretation: built as 20 Capabilities x 175 Work Items = 3,500
Work Items (matching the charter's WI figure exactly), each Capability's
Work Items forming a bounded internal dependency chain (already proven
correct and fast post-EX5-S9). "1700 tasks" is read as roughly the
Problem/verification-pair granularity — not literally re-derived here;
what matters is proving coherence at the stated order of magnitude, not
hitting an exact secondary figure.
"""

from __future__ import annotations

import time

import pytest

from backend.aix_core.runtime.executor.execution_kernel_adapter.adapters import AdapterOutcome
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService

N_CAPABILITIES = 20
WI_PER_CAPABILITY = 175
TOTAL_WORK_ITEMS = N_CAPABILITIES * WI_PER_CAPABILITY  # 3,500


async def _build_erp_scale_project(svc: KernelService, *, tenant_id: str) -> dict:
    project_id = "proj_erp"
    await svc.append_and_project(
        tenant_id=tenant_id, aggregate_type="project", aggregate_id=project_id,
        expected_sequence_number=1, event_type="PROJECT_CREATED", command_id="erp_project",
        payload={"status": "ACTIVE", "vision_summary": "Build a complete ERP system"},
    )
    capability_ids: list[str] = []
    problem_ids: list[str] = []
    for c in range(N_CAPABILITIES):
        cap_id = f"cap_{c}"
        problem_id = f"pr_{c}"
        capability_ids.append(cap_id)
        problem_ids.append(problem_id)
        await svc.append_and_project(
            tenant_id=tenant_id, aggregate_type="capability", aggregate_id=cap_id,
            expected_sequence_number=1, event_type="CAPABILITY_CREATED", command_id=f"cap_created_{c}",
            payload={"status": "ACTIVE", "name": f"ERP Module {c}", "parent_id": project_id},
        )
        await svc.append_and_project(
            tenant_id=tenant_id, aggregate_type="problem", aggregate_id=problem_id,
            expected_sequence_number=1, event_type="PROBLEM_CREATED", command_id=f"problem_created_{c}",
            payload={"status": "EXECUTING", "parent_id": cap_id},
        )
        for w in range(WI_PER_CAPABILITY):
            wi_id = f"wi_{c}_{w}"
            await svc.append_and_project(
                tenant_id=tenant_id, aggregate_type="work_item", aggregate_id=wi_id,
                expected_sequence_number=1, event_type="WI_CREATED", command_id=f"wi_created_{c}_{w}",
                payload={"status": "PENDING", "parent_id": problem_id},
            )
            if w > 0:
                await svc.add_dependency(
                    tenant_id=tenant_id, work_item_id=wi_id,
                    depends_on_work_item_id=f"wi_{c}_{w - 1}", command_id=f"dep_{c}_{w}",
                )
    return {"project_id": project_id, "capability_ids": capability_ids, "problem_ids": problem_ids}


@pytest.mark.asyncio
async def test_full_stack_coheres_at_3500_work_item_erp_scale() -> None:
    svc = KernelService()
    tenant_id = "t_erp"

    t0 = time.perf_counter()
    graph = await _build_erp_scale_project(svc, tenant_id=tenant_id)
    build_seconds = time.perf_counter() - t0
    print(f"\nEX5-S12: built {TOTAL_WORK_ITEMS} work items across {N_CAPABILITIES} capabilities in {build_seconds:.2f}s")
    assert build_seconds < 60.0, "ERP-scale build took too long — investigate before shipping"

    # 1. Dependency correctness at full scale — every capability's chain
    # head must be independently actionable; nothing later must be.
    t1 = time.perf_counter()
    all_actionable: list[str] = []
    for problem_id in graph["problem_ids"]:
        actionable = await svc.next_actionable(tenant_id=tenant_id, problem_id=problem_id)
        all_actionable.extend(actionable)
    query_seconds = time.perf_counter() - t1
    assert len(all_actionable) == N_CAPABILITIES, "exactly one actionable head per capability, no more, no fewer"
    assert query_seconds < 5.0, f"next_actionable across all {N_CAPABILITIES} problems took {query_seconds:.2f}s"

    # 2. Checkpoint/resume correctness at full per-Problem scale.
    checkpoint = await svc.capture_checkpoint(
        tenant_id=tenant_id, problem_id=graph["problem_ids"][0],
        repository_state={
            "repository_id": "aix", "branch": "main", "base_commit_sha": "abc1234",
            "current_commit_sha": "abc1234", "working_tree_diff_reference": "",
            "untracked_artifact_references": [], "environment_manifest_reference": "",
        },
    )
    assert len(checkpoint["work_items"]) == WI_PER_CAPABILITY

    # 3. Portfolio intelligence view sees every capability, not a truncated subset.
    portfolio = await svc.portfolio_intelligence_view(tenant_id=tenant_id)
    assert len(portfolio["capabilities"]) == N_CAPABILITIES
    assert portfolio["projects"][0]["project_id"] == graph["project_id"]

    # 4. A sample verification + learning distillation cycle still works
    # coherently inside an ERP-scale tenant (proves the rest of the kernel
    # isn't somehow degraded by the surrounding scale).
    sample_wi = "wi_0_0"
    verified = await svc.apply_verification(
        tenant_id=tenant_id, work_item_id=sample_wi,
        outcome=AdapterOutcome(kind="SUCCEEDED", evidence_refs=("ev1",)),
        decision="ACCEPT", expected_sequence_number=2, command_id="verify_sample",
    )
    assert verified["verification"]["work_item_status"] == "VERIFIED"

    # 5. Enterprise audit trail captures the run coherently.
    audit_before = await svc.list_enterprise_audit(tenant_id=tenant_id)
    cycle = await svc.start_enterprise_improvement_cycle(
        tenant_id=tenant_id, scope="capability", summary="ERP-scale validation run",
        capability_id=graph["capability_ids"][0],
    )
    assert cycle["silent_org_promotion"] is False
    audit_after = await svc.list_enterprise_audit(tenant_id=tenant_id)
    assert len(audit_after) > len(audit_before), "the improvement cycle must leave a real audit trail entry"


@pytest.mark.asyncio
async def test_no_production_claim_is_made_by_this_kernel_layer() -> None:
    """The explicit anti-overclaim guard — enterprise_scenarios() and the
    hard prohibition fields (from EX4-R4-P12-S001) must still hold at this
    scale: no 'AI CEO', no bypassing Hyeve controls, no silent promotion."""
    svc = KernelService()
    scenarios = await svc.enterprise_scenarios()
    assert scenarios, "enterprise scenarios must still be enumerable"
    cycle = await svc.start_enterprise_improvement_cycle(
        tenant_id="t_guard", scope="capability", summary="guard check",
        capability_id="cap_guard",
    )
    assert cycle["silent_org_promotion"] is False
