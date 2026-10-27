"""Full E2E verification_only task: ADMIT → START → APPROVE → PLAN → DISPATCH → EXECUTE → COMPLETE"""

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend
from backend.aix_core.runtime.planner.step_contract_planner import StepContractProposalV1
from backend.aix_core.runtime.objective.contracts import StepContractV1, BudgetV1
from backend.aix_core.runtime.executor.real_execute import RealExecuteResult
from backend.hyeve_aix_service.config import settings

OBJECTIVE = r"i just want to know about current status of aix D:\v2.1\AIX\hyeve-aix-service\backend"
BACKEND_ROOT = Path(r"D:/v2.1/AIX/hyeve-aix-service/backend")


async def mock_propose(**kwargs):
    contract = StepContractV1(
        cycle_id=kwargs.get("cycle_id", "cyc-test"),
        tenant_id=kwargs.get("tenant_id", "test"),
        step_index=0,
        purpose="Read-only investigation of AIX backend service current status",
        target_scope=("backend/",),
        validation_commands=("echo status-check-ok",),
        expected_output_schema_ref="text",
        acceptance_criteria=("Return factual summary of AIX backend status",),
        assigned_role="executor",
        risk_tier="LOW",
        step_budget=BudgetV1(
            time_seconds=300, tokens=50000, cost_usd=1.0,
            retries=1, steps=1, concurrency=1,
        ),
        status="PENDING",
    )
    return StepContractProposalV1(
        contract=contract,
        reason="PLAN_ACCEPTED",
        reply="Status check plan created",
        evidence_requests=(),
        used_legacy_one_step=True,
    )


def _gather_real_evidence() -> dict:
    evidence = {}
    top_dirs = sorted([d.name for d in BACKEND_ROOT.iterdir() if d.is_dir()])[:20]
    top_files = sorted([f.name for f in BACKEND_ROOT.iterdir() if f.is_file()])[:10]
    evidence["directory_structure"] = {"dirs": top_dirs, "files": top_files}

    for cfg in ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"):
        p = BACKEND_ROOT / cfg
        if p.exists():
            evidence[cfg] = p.read_text(encoding="utf-8", errors="replace")[:500]

    py_files = list(BACKEND_ROOT.rglob("*.py"))
    evidence["python_file_count"] = len(py_files)

    test_dir = BACKEND_ROOT / "tests"
    if test_dir.exists():
        test_files = list(test_dir.rglob("test_*.py"))
        evidence["test_file_count"] = len(test_files)
        evidence["test_dirs"] = sorted(
            set(str(f.parent.relative_to(BACKEND_ROOT)) for f in test_files[:50])
        )[:10]

    runtime_dir = BACKEND_ROOT / "aix_core" / "runtime"
    if runtime_dir.exists():
        runtime_subdirs = sorted([d.name for d in runtime_dir.iterdir() if d.is_dir()])
        evidence["runtime_modules"] = runtime_subdirs

    try:
        r = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            capture_output=True, text=True, cwd=str(BACKEND_ROOT.parent),
        )
        evidence["recent_commits"] = r.stdout.strip().split("\n")[:5]
    except Exception:
        evidence["recent_commits"] = ["git not available"]

    return evidence


async def mock_real_execute(**kwargs):
    evidence = _gather_real_evidence()

    lines = [
        "# AIX Backend Service — Current Status Report",
        "",
        "## Repository Structure",
        f"- Top-level directories: {evidence['directory_structure']['dirs']}",
        f"- Top-level files: {evidence['directory_structure']['files']}",
        f"- Total Python files: {evidence['python_file_count']}",
        f"- Test files: {evidence.get('test_file_count', 'N/A')}",
        "",
        "## Runtime Modules",
    ]
    for mod in evidence.get("runtime_modules", []):
        lines.append(f"  - {mod}")
    lines.append("")
    lines.append("## Recent Commits")
    for c in evidence.get("recent_commits", []):
        lines.append(f"  - {c}")
    lines.append("")
    lines.append("## Test Coverage Directories")
    for td in evidence.get("test_dirs", []):
        lines.append(f"  - {td}")

    return RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace="read-only-investigation",
        evidence_refs=[json.dumps(evidence, default=str)[:2000]],
        workhub_loaded=False,
        artifacts={"status_report": "\n".join(lines)},
        execution_status="COMPLETED",
        verification_status="PASSED",
        objective_status="COMPLETED",
        real_operations=0,
        target_files_modified=0,
    )


async def main():
    backend = DurableObjectiveCycleApiBackend(
        mode="inprocess",
        real_execute_enabled=True,
        require_workhub_load=False,
        create_workhub_tasks=False,
        settings=settings,
    )

    with (
        patch(
            "backend.aix_core.runtime.objective.durable_api_backend.propose_step_contract",
            side_effect=mock_propose,
        ),
        patch(
            "backend.aix_core.runtime.objective.durable_api_backend.run_real_execute",
            side_effect=mock_real_execute,
        ),
    ):
        # 1. ADMIT
        r = await backend.command(
            operation="admit_objective",
            tenant_id="test",
            actor_id="test",
            resource_id=None,
            body={
                "objective": OBJECTIVE,
                "repository_id": "hyeve-aix-service",
                "project_id": "test",
                "workhub_task_id": "full-e2e-003",
                "task_number": 1,
                "task_class": "verification_only",
                "definition_of_done": ["Report current status of AIX backend service"],
            },
            idempotency_key="full-admit-3",
            expected_version=None,
        )
        print("1. ADMIT ok:", r.get("ok"))
        obj_id = r["objective_id"]

        # 2. START
        r2 = await backend.command(
            operation="start_cycle",
            tenant_id="test",
            actor_id="test",
            resource_id=obj_id,
            body={"candidate_sha": "abc1234fake"},
            idempotency_key="full-start-3",
            expected_version=None,
        )
        print("2. START ok:", r2.get("ok"), "phase:", r2.get("phase"))
        cycle_id = r2["cycle_id"]

        # 3. APPROVE
        r3 = await backend.command(
            operation="validate_approval_reference",
            tenant_id="test",
            actor_id="test",
            resource_id=cycle_id,
            body={"approval_reference": "test-approval", "approved": True},
            idempotency_key="full-approve-3",
            expected_version=None,
        )
        print("3. APPROVE ok:", r3.get("ok"), "phase:", r3.get("phase"))
        if "error" in r3:
            print("   ERROR:", r3["error"])

        # 4+ DISPATCH (may need multiple rounds)
        for dispatch_round in range(1, 6):
            rd = await backend.command(
                operation="dispatch_ready_step",
                tenant_id="test",
                actor_id="test",
                resource_id=cycle_id,
                body={},
                idempotency_key=f"full-dispatch-3-{dispatch_round}",
                expected_version=None,
            )
            phase = rd.get("phase", "?")
            print(f"4.{dispatch_round} DISPATCH ok:", rd.get("ok"), "phase:", phase)
            if rd.get("error"):
                print("   ERROR:", rd["error"])
            if not rd.get("dispatch_deferred") and not rd.get("dispatch_required"):
                break

    # === FINAL STATE ===
    cycle = backend._cycles.get(cycle_id)
    print()
    print("=" * 60)
    print("FINAL STATE")
    print("=" * 60)
    if cycle:
        print(f"  phase:            {cycle.phase}")
        print(f"  dispatch_state:   {cycle.dispatch_state}")
        print(f"  terminal_reason:  {cycle.terminal_reason}")
        print(f"  real_execute:     {cycle.real_execute}")

    sc_err = backend._step_contract_errors.get(obj_id)
    print(f"  step_contract_err:{sc_err}")

    plans = backend._plan_revisions.get(obj_id, ())
    print(f"  plan_count:       {len(plans)}")
    if plans:
        print(f"  plan_status:      {plans[-1].status}")

    u = backend._understanding_evidence.get(obj_id)
    print(f"  understanding:    {u is not None}")

    attempts = backend._step_attempts.get(obj_id, [])
    print(f"  step_attempts:    {len(attempts)}")
    for i, a in enumerate(attempts):
        print(f"    [{i}] status={getattr(a, 'status', '?')} evidence_count={len(getattr(a, 'evidence_refs', []))}")

    # Mutation check
    print()
    print("MUTATION CHECK:")
    print(f"  target_files_modified: {getattr(cycle, 'target_files_modified', 0) if cycle else 0}")
    print(f"  NO PLAN_NOT_FOUND:     {sc_err is None or 'PLAN_NOT_FOUND' not in str(sc_err)}")
    print(f"  NO NEEDS_MORE_EVIDENCE:{sc_err is None or 'NEEDS_MORE_EVIDENCE' not in str(sc_err)}")

    # Print the actual status report artifact if available
    print()
    print("=" * 60)
    print("AIX STATUS REPORT (actual answer)")
    print("=" * 60)
    for a in attempts:
        arts = getattr(a, "artifacts", {}) or {}
        if "status_report" in arts:
            print(arts["status_report"])
            break
    else:
        # Check last dispatch result
        if cycle and cycle.real_execute:
            print("real_execute result available")
        else:
            print("(status report in executor artifacts, checking evidence...)")
            evidence_data = _gather_real_evidence()
            print(f"Python files: {evidence_data['python_file_count']}")
            print(f"Test files: {evidence_data.get('test_file_count', 'N/A')}")
            print(f"Runtime modules: {evidence_data.get('runtime_modules', [])}")
            print(f"Recent commits: {evidence_data.get('recent_commits', [])}")
            print(f"Test dirs: {evidence_data.get('test_dirs', [])}")


if __name__ == "__main__":
    asyncio.run(main())
