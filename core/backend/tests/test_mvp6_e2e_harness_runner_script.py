from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_mvp6_harness_runner_executes_from_repo_root(tmp_path: Path) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    repo_root = backend_root.parents[3]
    script_path = backend_root / "scripts" / "mvp6_e2e_harness_runner.py"
    report_path = tmp_path / "mvp6_harness_report.json"

    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--mode",
            "dry_run",
            "--break-glass-order-ok",
            "--drift-guard-proof-present",
            "--report",
            str(report_path),
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["policy_gate"]["passed"] is True
    assert payload["golden"]["status"] == "planned"
