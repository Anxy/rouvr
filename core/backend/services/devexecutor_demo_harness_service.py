from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from backend.legacy.orphaned_services.devexecutor_evidence_pack_service import (
    build_evidence_pack,
    collect_artifacts,
    generate_acceptance_mapping,
)
from backend.legacy.orphaned_services.devjob_state_machine_service import (
    DevJobStateMachine,
    apply_verifier_decision,
    execute_tool_loop_with_state_machine,
)
from backend.services.verifier_decision_service import build_verifier_decision

HarnessMode = Literal["dry_run", "full_run"]


def evaluate_policy_conformance_gate(
    *,
    autonomy_cap: str,
    break_glass_order_ok: bool,
    drift_guard_proof_present: bool,
) -> dict[str, Any]:
    checks = {
        "autonomy_cap_l2": autonomy_cap == "L2",
        "break_glass_order_ok": bool(break_glass_order_ok),
        "drift_guard_proof_present": bool(drift_guard_proof_present),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "failure_reasons": [k for k, ok in checks.items() if not ok],
    }


def get_golden_demo_scenario_current() -> dict[str, Any]:
    return {
        "scenario_id": "MVP6-S10-GOLDEN-DEMO-CURRENT",
        "description": "Patch-first flow with tests and evidence ending in ReadyForHuman.",
        "acceptance_items": [
            {"id": "AC-S10-1", "text": "Patch applied successfully"},
            {"id": "AC-S10-2", "text": "Command/test proof executed"},
            {"id": "AC-S10-3", "text": "Evidence pack generated with mapping"},
        ],
    }


def _init_demo_repo(workspace_root: Path) -> None:
    subprocess.run(["git", "-C", str(workspace_root), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(workspace_root), "config", "user.email", "codex@example.com"], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(workspace_root), "config", "user.name", "Codex"], check=True, capture_output=True)
    (workspace_root / "demo.py").write_text("greeting = 'hello'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace_root), "add", "demo.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(workspace_root), "commit", "-m", "seed demo"], check=True, capture_output=True)


def run_golden_demo_harness(
    *,
    workspace_root: str | Path,
    mode: HarnessMode,
    policy_gate: dict[str, Any],
    command_runtime: dict[str, Any] | None = None,
    correlation_id: str = "corr-demo",
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    scenario = get_golden_demo_scenario_current()

    if not bool(policy_gate.get("passed")):
        return {
            "mode": mode,
            "status": "blocked",
            "reason": "POLICY_CONFORMANCE_GATE_FAILED",
            "policy_gate": policy_gate,
            "scenario": scenario,
        }

    if mode == "dry_run":
        return {
            "mode": mode,
            "status": "planned",
            "scenario": scenario,
            "planned_states": ["queued", "running", "editing", "testing", "ready_for_human"],
            "policy_gate": policy_gate,
        }

    _init_demo_repo(root)
    patch = """diff --git a/demo.py b/demo.py
index ce01362..94954ab 100644
--- a/demo.py
+++ b/demo.py
@@ -1 +1 @@
-greeting = 'hello'
+greeting = 'hello world'
"""
    steps = [
        {"tool": "apply_patch", "args": {"patch_text": patch}},
        {"tool": "cmd.run", "args": {"command": [sys.executable, "-c", "print('ok')"], "timeout_seconds": 5.0}},
    ]

    (root / "diff.patch").write_text(patch, encoding="utf-8")
    (root / "tests.log").write_text("ok\n", encoding="utf-8")
    refs = collect_artifacts(workspace_root=root, required={"diff": "diff.patch", "tests": "tests.log"})
    mapping = generate_acceptance_mapping(
        acceptance_items=scenario["acceptance_items"],
        evidence_by_acceptance_id={
            "AC-S10-1": ["diff.patch"],
            "AC-S10-2": ["tests.log"],
            "AC-S10-3": ["diff.patch", "tests.log"],
        },
    )
    evidence_pack = build_evidence_pack(
        status="pass",
        summary="Golden demo run completed.",
        diff_artifact="diff.patch",
        commands_run=[{"command": [sys.executable, "-c", "print('ok')"], "returncode": 0}],
        test_results={"passed": True},
        dev_run_proof={"stdout_contains": "ok"},
        risk_notes=[],
        acceptance_mapping=mapping,
        artifacts=refs,
        metadata={"scenario_id": scenario["scenario_id"]},
    )
    result = execute_tool_loop_with_state_machine(
        workspace_root=str(root),
        steps=steps,
        allow_prefixes=[[sys.executable], ["git"]],
        correlation_id=correlation_id,
        evidence_pack=evidence_pack,
        command_runtime=command_runtime,
    )
    return {
        "mode": mode,
        "status": "completed",
        "scenario": scenario,
        "policy_gate": policy_gate,
        "result": result,
        "evidence_pack": evidence_pack.model_dump(mode="python"),
    }


def run_failure_golden_path_policy_deny(
    *,
    workspace_root: str | Path,
    command_runtime: dict[str, Any] | None = None,
    correlation_id: str = "corr-failure-policy-deny",
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    _init_demo_repo(root)

    steps = [{"tool": "cmd.run", "args": {"command": ["whoami"], "timeout_seconds": 2.0}}]
    (root / "failure.log").write_text("policy denied command whoami\n", encoding="utf-8")
    refs = collect_artifacts(workspace_root=root, required={"failure_log": "failure.log"})
    mapping = generate_acceptance_mapping(
        acceptance_items=[
            {"id": "AC-FAIL-1", "text": "Failure ends in deterministic non-ready state"},
            {"id": "AC-FAIL-2", "text": "Failure evidence produced"},
        ],
        evidence_by_acceptance_id={"AC-FAIL-1": ["failure.log"], "AC-FAIL-2": ["failure.log"]},
    )
    evidence_pack = build_evidence_pack(
        status="fail",
        summary="Policy deny failure path executed.",
        diff_artifact="failure.log",
        commands_run=[{"command": ["whoami"], "returncode": None, "error": "COMMAND_DENIED"}],
        test_results={"passed": False},
        dev_run_proof={"status": "policy_deny"},
        risk_notes=["command denied by policy"],
        acceptance_mapping=mapping,
        artifacts=refs,
        metadata={"scenario_id": "MVP6-S10-FAILURE-POLICY-DENY-CURRENT"},
    )
    result = execute_tool_loop_with_state_machine(
        workspace_root=str(root),
        steps=steps,
        allow_prefixes=[["git"]],
        correlation_id=correlation_id,
        evidence_pack=evidence_pack,
        command_runtime=command_runtime,
    )
    return {
        "status": "completed",
        "result": result,
        "evidence_pack": evidence_pack.model_dump(mode="python"),
    }


def run_failure_golden_path_verifier_reject(
    *,
    workspace_root: str | Path,
    command_runtime: dict[str, Any] | None = None,
    correlation_id: str = "corr-failure-verifier-reject",
) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    _init_demo_repo(root)

    patch = """diff --git a/demo.py b/demo.py
index ce01362..94954ab 100644
--- a/demo.py
+++ b/demo.py
@@ -1 +1 @@
-greeting = 'hello'
+greeting = 'hello world'
"""
    steps = [
        {"tool": "apply_patch", "args": {"patch_text": patch}},
        {"tool": "cmd.run", "args": {"command": [sys.executable, "-c", "print('ok')"], "timeout_seconds": 5.0}},
    ]
    (root / "reject.diff.patch").write_text(patch, encoding="utf-8")
    (root / "reject.tests.log").write_text("ok\n", encoding="utf-8")
    refs = collect_artifacts(
        workspace_root=root,
        required={"diff": "reject.diff.patch", "tests": "reject.tests.log"},
    )
    mapping = generate_acceptance_mapping(
        acceptance_items=[
            {"id": "AC-RJ-1", "text": "Patch applies"},
            {"id": "AC-RJ-2", "text": "Verifier can reject with line-referenced reason"},
        ],
        evidence_by_acceptance_id={
            "AC-RJ-1": ["reject.diff.patch"],
            "AC-RJ-2": ["reject.tests.log"],
        },
    )
    evidence_pack = build_evidence_pack(
        status="pass",
        summary="Coder phase reached ready_for_human before verifier rejection.",
        diff_artifact="reject.diff.patch",
        commands_run=[{"command": [sys.executable, "-c", "print('ok')"], "returncode": 0}],
        test_results={"passed": True},
        dev_run_proof={"stdout_contains": "ok"},
        risk_notes=["verifier rejection simulated"],
        acceptance_mapping=mapping,
        artifacts=refs,
        metadata={"scenario_id": "MVP6-S10-FAILURE-VERIFIER-REJECT-CURRENT"},
    )
    coder_result = execute_tool_loop_with_state_machine(
        workspace_root=str(root),
        steps=steps,
        allow_prefixes=[[sys.executable], ["git"]],
        correlation_id=correlation_id,
        evidence_pack=evidence_pack,
        command_runtime=command_runtime,
    )

    verifier_sm = DevJobStateMachine(correlation_id=f"{correlation_id}-verifier", initial_state="queued")
    verifier_sm.transition(to_state="running", reason_code="VERIFIER_JOB_STARTED")
    verifier_sm.transition(to_state="testing", reason_code="READY_FOR_HUMAN_IMPORTED")
    verifier_sm.transition(to_state="ready_for_human", reason_code="READY_FOR_HUMAN_CONFIRMED")
    verifier_result = apply_verifier_decision(
        state_machine=verifier_sm,
        decision_payload=build_verifier_decision(
            decision="needs_fix",
            summary="Reject golden path: missing explicit edge-case test.",
            reasons=[
                {
                    "kind": "missing_test",
                    "message": "Add explicit negative-path assertion.",
                    "file_path": "demo.py",
                    "line": 1,
                }
            ],
            acceptance_mapping=[{"acceptance_id": "AC-RJ-2", "met": False}],
            patch_refs=["reject.diff.patch"],
            test_refs=["reject.tests.log"],
            metadata={"scenario_id": "MVP6-S10-FAILURE-VERIFIER-REJECT-CURRENT"},
        ),
    )
    return {
        "status": "completed",
        "coder_result": coder_result,
        "verifier_result": verifier_result,
        "evidence_pack": evidence_pack.model_dump(mode="python"),
    }


def write_harness_report(*, path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
