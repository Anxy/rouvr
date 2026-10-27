from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[5]
SCRIPTS_DIR = ROOT / "scripts"
BACKEND_DIR = ROOT / "projects" / "AIX" / "hyeve-aix-service" / "backend"
ARTIFACT_BASE = ROOT / "projects" / "AIX" / "artifacts" / "pilot_quickstart"


def _build_steps(*, project_id: str, password: str, skip_start: bool) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    if not skip_start:
        steps.append(
            {
                "name": "start_services",
                "command": [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(SCRIPTS_DIR / "start-wh-local.ps1"),
                    "-NoReload",
                ],
                "required": True,
            }
        )
    steps.extend(
        [
            {
                "name": "bootstrap_operator",
                "command": [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(SCRIPTS_DIR / "bootstrap-wh-operator.ps1"),
                    "-Password",
                    password,
                    "-ProjectId",
                    project_id,
                ],
                "required": True,
            },
            {
                "name": "preflight",
                "command": [
                    sys.executable,
                    str(BACKEND_DIR / "scripts" / "wh_fast_read.py"),
                    "preflight",
                ],
                "required": True,
            },
            {
                "name": "knowledge_summary",
                "command": [
                    sys.executable,
                    str(BACKEND_DIR / "scripts" / "wh_fast_read.py"),
                    "knowledge-summary",
                    "--project-id",
                    project_id,
                ],
                "required": True,
            },
            {
                "name": "test_slice",
                "command": [
                    "pytest",
                    str(BACKEND_DIR / "tests" / "test_model_gateway_router.py"),
                    "-q",
                ],
                "required": True,
            },
        ]
    )
    return steps


def _run_step(step: dict[str, Any], *, cwd: Path, dry_run: bool) -> dict[str, Any]:
    started = time.perf_counter()
    if dry_run:
        return {
            "name": step["name"],
            "command": step["command"],
            "returncode": 0,
            "ok": True,
            "dry_run": True,
            "stdout": "",
            "stderr": "",
            "duration_ms": 0,
        }
    proc = subprocess.run(
        step["command"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        shell=False,
        encoding="utf-8",
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    return {
        "name": step["name"],
        "command": step["command"],
        "returncode": int(proc.returncode),
        "ok": proc.returncode == 0,
        "dry_run": False,
        "stdout": proc.stdout[-12000:],
        "stderr": proc.stderr[-12000:],
        "duration_ms": duration_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="One-click AIX pilot quickstart + test slice.")
    parser.add_argument("--project-id", default="bh-migrated-7-aix")
    parser.add_argument("--password", default="simple")
    parser.add_argument("--skip-start", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-dir", default=str(ARTIFACT_BASE))
    args = parser.parse_args()

    started_at = datetime.now(UTC)
    stamp = started_at.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.output_dir) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    steps = _build_steps(project_id=args.project_id, password=args.password, skip_start=args.skip_start)
    results: list[dict[str, Any]] = []
    overall_ok = True
    for step in steps:
        result = _run_step(step, cwd=ROOT, dry_run=args.dry_run)
        results.append(result)
        if bool(step.get("required", True)) and not result["ok"]:
            overall_ok = False
            break

    report = {
        "flow": "aix_pilot_quickstart",
        "timestamp_utc": started_at.isoformat(),
        "project_id": args.project_id,
        "dry_run": bool(args.dry_run),
        "skip_start": bool(args.skip_start),
        "overall_ok": overall_ok,
        "steps": results,
    }
    report_path = out_dir / "pilot-quickstart-report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    latest = Path(args.output_dir) / "pilot-quickstart-latest.json"
    latest.write_text(json.dumps(report, indent=2), encoding="utf-8")

    summary_md = out_dir / "pilot-quickstart-summary.md"
    summary_md.write_text(
        "\n".join(
            [
                "# AIX Pilot Quickstart Report",
                "",
                f"- Timestamp (UTC): {started_at.isoformat()}",
                f"- Project: `{args.project_id}`",
                f"- Dry run: `{args.dry_run}`",
                f"- Skip start: `{args.skip_start}`",
                f"- Overall OK: `{overall_ok}`",
                "",
                "## Steps",
                *[
                    f"- `{row['name']}` -> ok={row['ok']} returncode={row['returncode']} duration_ms={row['duration_ms']}"
                    for row in results
                ],
                "",
                f"- JSON: `{report_path}`",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps({"success": overall_ok, "report": str(report_path), "latest": str(latest)}, indent=2))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
