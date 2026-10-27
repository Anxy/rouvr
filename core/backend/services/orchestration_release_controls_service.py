from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ROLLOUT_FLAG_SEQUENCE = (
    "HYEVE_AIX_SERVICE_TEMPORAL_ENABLED",
    "HYEVE_AIX_SERVICE_LANGGRAPH_ENABLED",
    "HYEVE_AIX_SERVICE_HAYSTACK_RETRIEVAL_ENABLED",
)


@dataclass
class ValidationCheckResult:
    name: str
    passed: bool
    duration_ms: int
    command: str
    details: str = ""


def evaluate_release_controls(
    *,
    contract_checks: list[ValidationCheckResult],
    replay_checks: list[ValidationCheckResult],
    rollout_flags: list[str],
    rollback_script_path: str,
) -> dict[str, Any]:
    contract_ok = all(item.passed for item in contract_checks) and len(contract_checks) > 0
    replay_ok = all(item.passed for item in replay_checks) and len(replay_checks) > 0
    rollout_ok = len(rollout_flags) > 0
    rollback_ok = bool(str(rollback_script_path or "").strip())
    return {
        "ready": contract_ok and replay_ok and rollout_ok and rollback_ok,
        "checks": {
            "contract_tests": contract_ok,
            "replay_failure_recovery": replay_ok,
            "rollout_flags_defined": rollout_ok,
            "rollback_script_generated": rollback_ok,
        },
    }


def build_rollout_plan() -> list[dict[str, Any]]:
    return [
        {
            "stage": "stage_1_dark_launch",
            "description": "Enable Temporal runtime only, keep LangGraph/Haystack disabled.",
            "set_flags": {"HYEVE_AIX_SERVICE_TEMPORAL_ENABLED": "true"},
        },
        {
            "stage": "stage_2_planner_canary",
            "description": "Enable LangGraph planner/coder chain for selected projects.",
            "set_flags": {
                "HYEVE_AIX_SERVICE_TEMPORAL_ENABLED": "true",
                "HYEVE_AIX_SERVICE_LANGGRAPH_ENABLED": "true",
            },
        },
        {
            "stage": "stage_3_retrieval_canary",
            "description": "Enable Haystack retrieval adapter under observation.",
            "set_flags": {
                "HYEVE_AIX_SERVICE_TEMPORAL_ENABLED": "true",
                "HYEVE_AIX_SERVICE_LANGGRAPH_ENABLED": "true",
                "HYEVE_AIX_SERVICE_HAYSTACK_RETRIEVAL_ENABLED": "true",
            },
        },
    ]


def build_rollback_powershell_commands(*, include_restart: bool = True) -> list[str]:
    lines = [
        '$env:HYEVE_AIX_SERVICE_TEMPORAL_ENABLED = "false"',
        '$env:HYEVE_AIX_SERVICE_LANGGRAPH_ENABLED = "false"',
        '$env:HYEVE_AIX_SERVICE_HAYSTACK_RETRIEVAL_ENABLED = "false"',
    ]
    if include_restart:
        lines.append("powershell -ExecutionPolicy Bypass -File C:\\dev\\hyeve\\scripts\\start-hyeve-group.ps1")
    return lines
