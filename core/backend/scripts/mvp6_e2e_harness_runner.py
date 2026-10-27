from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
BACKEND_ROOT = SCRIPT_PATH.parent.parent
REPO_ROOT = BACKEND_ROOT.parents[3]
APP_ROOT = BACKEND_ROOT.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from backend.hyeve_aix_service.config import settings  # noqa: E402
from backend.services.devexecutor_demo_harness_service import (
    evaluate_policy_conformance_gate,
    run_failure_golden_path_policy_deny,
    run_golden_demo_harness,
    write_harness_report,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MVP6 S10 harness (golden + failure path).")
    parser.add_argument("--workspace-root", default=str(REPO_ROOT))
    parser.add_argument("--mode", choices=["dry_run", "full_run"], default="full_run")
    parser.add_argument("--run-failure-path", action="store_true")
    parser.add_argument("--autonomy-cap", default="L2")
    parser.add_argument("--break-glass-order-ok", action="store_true")
    parser.add_argument("--drift-guard-proof-present", action="store_true")
    parser.add_argument("--report", default=str(REPO_ROOT / "_runlogs" / "mvp6_e2e_harness_latest.json"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    gate = evaluate_policy_conformance_gate(
        autonomy_cap=args.autonomy_cap,
        break_glass_order_ok=args.break_glass_order_ok,
        drift_guard_proof_present=args.drift_guard_proof_present,
    )
    payload = {
        "policy_gate": gate,
        "golden": run_golden_demo_harness(
            workspace_root=args.workspace_root,
            mode=args.mode,
            policy_gate=gate,
            command_runtime=settings.devexecutor_command_runtime,
        ),
    }
    if args.run_failure_path:
        payload["failure_policy_deny"] = run_failure_golden_path_policy_deny(
            workspace_root=args.workspace_root,
            command_runtime=settings.devexecutor_command_runtime,
        )
    write_harness_report(path=Path(args.report), payload=payload)
    print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
