from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[5]
SERVICE_ROOT = ROOT / "projects" / "AIX" / "hyeve-aix-service"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

import backend.routers.model_gateway_router as model_gateway_router  # noqa: E402
from backend.routers.model_gateway_router import router  # noqa: E402

ARTIFACT_BASE = ROOT / "projects" / "AIX" / "artifacts" / "oncall_drills"


def _scenario_rows(scenario: str) -> list[dict[str, Any]]:
    if scenario == "failure_mode":
        return [
            {
                "id": "d-fail-1",
                "correlation_id": "corr-fail-1",
                "decision_status": "blocked",
                "decision_reason": "DOWNSTREAM_FAILURE",
                "retry_count": 1,
                "decision_payload_json": {},
            }
        ]
    if scenario == "dlq_backlog":
        return [
            {
                "id": "d-dlq-1",
                "correlation_id": "corr-dlq-1",
                "decision_status": "selected",
                "decision_reason": "QUEUE_DLQ_TRIGGERED",
                "retry_count": 2,
                "decision_payload_json": {"dlq_count": 3},
            }
        ]
    if scenario == "lease_churn":
        return [
            {
                "id": "d-lease-1",
                "correlation_id": "corr-lease-1",
                "decision_status": "selected",
                "decision_reason": "LEASE_RENEWAL_RETRY",
                "retry_count": 4,
                "decision_payload_json": {},
            }
        ]
    return []


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/aix/model-gateway/"):
            continue
        deps = getattr(route, "dependant", None)
        if not deps or not deps.dependencies:
            continue
        auth_dep = deps.dependencies[0].call
        app.dependency_overrides[auth_dep] = lambda: {
            "id": "drill-operator",
            "tenant_id": "default",
            "roles": ["admin"],
            "permissions": ["*"],
        }
    return app


def _run_single_drill(*, scenario: str, project_id: str) -> dict[str, Any]:
    app = _build_app()

    async def _tenant(_current_user):
        return "default"

    async def _list_decisions(**_kwargs):
        return {
            "items": _scenario_rows(scenario),
            "total": len(_scenario_rows(scenario)),
            "has_more": False,
        }

    model_gateway_router.validate_tenant_access = _tenant
    model_gateway_router.aix_gateway_decisions_service.list = _list_decisions

    started = time.perf_counter()
    with TestClient(app) as client:
        response = client.get(
            "/api/aix/model-gateway/slo/alerts/digest",
            params={"project_id": project_id, "failure_threshold": 1, "dlq_threshold": 1, "lease_churn_threshold": 1},
        )
    duration_ms = int((time.perf_counter() - started) * 1000)
    payload = response.json()
    alerts = payload.get("data", {}).get("alerts", [])
    fired = [row["rule_id"] for row in alerts if row.get("state") == "fired"]
    return {
        "scenario": scenario,
        "http_status": response.status_code,
        "duration_ms": duration_ms,
        "fired_rules": fired,
        "ok": response.status_code == 200 and len(fired) >= 1,
        "response": payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run on-call escalation playbook drills for AIX.")
    parser.add_argument("--project-id", default="bh-migrated-7-aix")
    parser.add_argument("--output-dir", default=str(ARTIFACT_BASE))
    args = parser.parse_args()

    scenarios = ["failure_mode", "dlq_backlog", "lease_churn"]
    started_at = datetime.now(UTC)
    stamp = started_at.strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.output_dir) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    results = [_run_single_drill(scenario=item, project_id=args.project_id) for item in scenarios]
    overall_ok = all(row["ok"] for row in results)
    report = {
        "flow": "aix_oncall_drill_runner",
        "timestamp_utc": started_at.isoformat(),
        "project_id": args.project_id,
        "overall_ok": overall_ok,
        "drills": results,
    }
    report_path = out_dir / "oncall-drill-report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    latest = Path(args.output_dir) / "oncall-drill-latest.json"
    latest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"success": overall_ok, "report": str(report_path), "latest": str(latest)}, indent=2))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
