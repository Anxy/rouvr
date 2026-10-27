from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from backend.services.orchestration_release_controls_service import (
    ROLLOUT_FLAG_SEQUENCE,
    ValidationCheckResult,
    build_rollback_powershell_commands,
    build_rollout_plan,
    evaluate_release_controls,
)

ROOT = Path(__file__).resolve().parents[3]
RUNLOGS = ROOT / "_runlogs"

CONTRACT_TESTS = (
    "backend/tests/test_request_header_utils.py",
    "backend/tests/test_integration_contract_router_omp.py",
    "backend/tests/test_langgraph_orchestration_router.py",
)
REPLAY_TESTS = (
    "backend/tests/test_temporal_bootstrap_runtime.py",
    "backend/tests/test_temporal_determinism_policy.py",
    "backend/tests/test_orchestration_dependency_guard.py",
)


def _run_command(command: list[str], *, cwd: Path) -> ValidationCheckResult:
    started = time.time()
    proc = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, check=False)
    duration_ms = int((time.time() - started) * 1000)
    output = (proc.stdout or "").strip()
    if not output:
        output = (proc.stderr or "").strip()
    detail = output.splitlines()[-1] if output else ""
    return ValidationCheckResult(
        name=" ".join(command[:3]),
        passed=proc.returncode == 0,
        duration_ms=duration_ms,
        command=" ".join(command),
        details=detail,
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_markdown(path: Path, payload: dict[str, Any]) -> None:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    checks = result.get("checks") if isinstance(result.get("checks"), dict) else {}
    lines = [
        "# AIX Orchestration Validation and Release Controls",
        "",
        f"- generated_at_utc: `{payload.get('generated_at_utc')}`",
        f"- ready: `{result.get('ready')}`",
        "",
        "## Gate Checks",
        f"- contract_tests: `{checks.get('contract_tests')}`",
        f"- replay_failure_recovery: `{checks.get('replay_failure_recovery')}`",
        f"- rollout_flags_defined: `{checks.get('rollout_flags_defined')}`",
        f"- rollback_script_generated: `{checks.get('rollback_script_generated')}`",
        "",
        "## Rollout Flags",
    ]
    for flag in payload.get("rollout_flags", []):
        lines.append(f"- `{flag}`")
    lines.extend(["", "## Rollback Script", f"- `{payload.get('rollback_script_path')}`"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _to_dict(items: list[ValidationCheckResult]) -> list[dict[str, Any]]:
    return [
        {
            "name": item.name,
            "passed": item.passed,
            "duration_ms": item.duration_ms,
            "command": item.command,
            "details": item.details,
        }
        for item in items
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run orchestration validation and release controls pack.")
    parser.add_argument("--workspace-root", default=str(ROOT))
    parser.add_argument("--report-json", default=str(RUNLOGS / "aix-orchestration-release-controls-latest.json"))
    parser.add_argument("--report-md", default=str(RUNLOGS / "aix-orchestration-release-controls-latest.md"))
    parser.add_argument("--skip-contract-tests", action="store_true")
    parser.add_argument("--skip-replay-tests", action="store_true")
    parser.add_argument("--skip-runtime-check", action="store_true")
    args = parser.parse_args()

    workspace = Path(args.workspace_root).resolve()
    contract_results: list[ValidationCheckResult] = []
    replay_results: list[ValidationCheckResult] = []

    if not args.skip_runtime_check:
        contract_results.append(
            _run_command(
                [
                    "python",
                    "backend/scripts/verify_orchestration_runtime.py",
                ],
                cwd=workspace,
            )
        )
    if not args.skip_contract_tests:
        contract_results.append(_run_command(["python", "-m", "pytest", *CONTRACT_TESTS, "-q"], cwd=workspace))
    if not args.skip_replay_tests:
        replay_results.append(_run_command(["python", "-m", "pytest", *REPLAY_TESTS, "-q"], cwd=workspace))

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    rollback_script = RUNLOGS / f"aix-orchestration-rollback-{timestamp}.ps1"
    rollback_script.parent.mkdir(parents=True, exist_ok=True)
    rollback_script.write_text("\n".join(build_rollback_powershell_commands()) + "\n", encoding="utf-8")

    result = evaluate_release_controls(
        contract_checks=contract_results,
        replay_checks=replay_results,
        rollout_flags=list(ROLLOUT_FLAG_SEQUENCE),
        rollback_script_path=str(rollback_script),
    )
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "suite": "aix_orchestration_release_controls",
        "result": result,
        "contract_checks": _to_dict(contract_results),
        "replay_checks": _to_dict(replay_results),
        "rollout_flags": list(ROLLOUT_FLAG_SEQUENCE),
        "rollout_plan": build_rollout_plan(),
        "rollback_script_path": str(rollback_script),
    }
    json_path = Path(args.report_json)
    md_path = Path(args.report_md)
    _write_json(json_path, payload)
    _write_markdown(md_path, payload)
    print(json.dumps(payload, indent=2))
    return 0 if bool(result.get("ready")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
