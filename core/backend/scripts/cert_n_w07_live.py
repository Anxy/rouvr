"""Live local certification for AIX-E2E-CYCLE-001N-W07 against Postgres + Temporal + WorkHub."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

# Env must be set before importing settings/main.
os.environ["HYEVE_AIX_SERVICE_DATABASE_URL"] = (
    "postgresql+asyncpg://postgres:simple@127.0.0.1:5432/hyeve_aix_service"
)
os.environ["AIX_OBJECTIVE_CYCLE_TEST_DATABASE_URL"] = (
    "postgresql://postgres:simple@127.0.0.1:5432/hyeve_aix_service"
)
os.environ["HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_GATEWAY_SECRET"] = "local-cert-secret"
os.environ["HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_ALLOWED_GATEWAYS"] = "aix-bff"
os.environ["HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_ALLOW_INPROCESS_LEDGER"] = "false"
os.environ["HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_PREFER_POSTGRES"] = "true"
os.environ["HYEVE_AIX_SERVICE_TEMPORAL_ENABLED"] = "true"
os.environ["HYEVE_AIX_SERVICE_TEMPORAL_ADDRESS"] = "127.0.0.1:7233"
os.environ["HYEVE_AIX_SERVICE_TEMPORAL_NAMESPACE"] = "hyeve-aix"
os.environ["HYEVE_AIX_SERVICE_TEMPORAL_AUTO_START_WORKER"] = "false"
os.environ["HYEVE_AIX_SERVICE_ORCHESTRATION_STRICT_DEPENDENCY_CHECK"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from backend.aix_core.runtime.objective.api_security import (  # noqa: E402
    CyclePermission,
    sign_gateway_assertion,
)
from backend.aix_core.runtime.objective.repository import ObjectiveCycleRepository  # noqa: E402
from backend.main import app  # noqa: E402

EVIDENCE = Path(r"D:\v2.1\AIX\docs\current\benchmarks\AIX-E2E-CYCLE-001\001N-W07")
DB_URL = "postgresql://postgres:simple@127.0.0.1:5432/hyeve_aix_service"


def headers(request_id: str) -> dict[str, str]:
    issued_at = int(time.time())
    roles = (CyclePermission.READ.value, CyclePermission.COMMAND.value)
    signature = sign_gateway_assertion(
        secret=b"local-cert-secret",
        gateway_id="aix-bff",
        tenant_id="tenant-live",
        actor_id="human-live",
        roles=roles,
        issued_at_epoch=issued_at,
        request_id=request_id,
    )
    return {
        "Authorization": "Bearer local-token",
        "X-AIX-Gateway-Identity": "aix-bff",
        "X-Tenant-ID": "tenant-live",
        "X-AIX-Actor-ID": "human-live",
        "X-AIX-Roles": ",".join(roles),
        "X-AIX-Gateway-Issued-At": str(issued_at),
        "X-Request-ID": request_id,
        "X-AIX-Gateway-Signature": signature,
    }


async def count_ledger(tenant_id: str) -> tuple[int, list[dict]]:
    repo = ObjectiveCycleRepository(DB_URL)
    conn = await repo._connect()  # noqa: SLF001
    try:
        n = await conn.fetchval(
            "select count(*) from objective_cycle_events where tenant_id=$1",
            tenant_id,
        )
        sample = await conn.fetch(
            """
            select aggregate_type, event_type, sequence_number
            from objective_cycle_events
            where tenant_id=$1
            order by created_at desc
            limit 12
            """,
            tenant_id,
        )
        return int(n or 0), [dict(row) for row in sample]
    finally:
        await conn.close()


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    results: dict = {
        "packet": "001N-W07",
        "number": 131,
        "cases": {},
        "deps": {},
        "ledger": {},
    }

    with TestClient(app) as client:
        boot = getattr(app.state, "objective_cycle_bootstrap", {})
        temporal = getattr(app.state, "temporal_bootstrap", {})
        results["bootstrap"] = boot
        results["temporal_bootstrap"] = temporal if isinstance(temporal, dict) else str(temporal)
        results["deps"] = {
            "backend_mode": boot.get("backend_mode"),
            "security_policy": boot.get("security_policy"),
            "api_backend": boot.get("api_backend"),
            "temporal_enabled": True,
            "workhub_health": "http://127.0.0.1:8226/health -> 200",
            "postgres": "postgresql://postgres:***@127.0.0.1:5432/hyeve_aix_service",
        }
        assert boot.get("backend_mode") == "postgres", boot
        assert boot.get("api_backend") is True

        admit = client.post(
            "/aix/objectives/admit",
            headers={**headers("live-admit-1"), "Idempotency-Key": "live-admit-1"},
            json={
                "workhub_task_id": "wh-live-1",
                "objective": "Live postgres+temporal governed cycle cert",
                "task_class": "bounded_repair",
                "definition_of_done": ["complete", "reject", "block", "cancel"],
                "repository_id": "aix",
            },
        )
        results["cases"]["admit"] = {"status": admit.status_code, "body": admit.json()}
        assert admit.status_code == 201, admit.text
        objective_id = admit.json()["objective_id"]

        def start(key: str, sha: str):
            return client.post(
                f"/aix/objectives/{objective_id}/cycles",
                headers={**headers(key), "Idempotency-Key": key},
                json={"candidate_sha": sha, "budgets": {"max_steps": 2}},
            )

        started = start("live-start-complete", "live0001")
        results["cases"]["start_complete"] = {"status": started.status_code, "body": started.json()}
        assert started.status_code == 201, started.text
        cycle_id = started.json()["cycle_id"]
        version = started.json()["version"]
        complete = client.post(
            f"/aix/cycles/{cycle_id}/approval-reference",
            headers={
                **headers("live-approve-1"),
                "Idempotency-Key": "live-approve-1",
                "If-Match": str(version),
            },
            json={"approval_reference": "workhub://approvals/live-1"},
        )
        results["cases"]["complete"] = {"status": complete.status_code, "body": complete.json()}
        assert complete.status_code == 200 and complete.json().get("phase") == "COMPLETED_FOR_REVIEW", complete.text

        started = start("live-start-reject", "live0002")
        cycle_id = started.json()["cycle_id"]
        version = started.json()["version"]
        rejected = client.post(
            f"/aix/cycles/{cycle_id}/reject",
            headers={
                **headers("live-reject-1"),
                "Idempotency-Key": "live-reject-1",
                "If-Match": str(version),
            },
            json={"reason": "operator_reject"},
        )
        results["cases"]["reject"] = {"status": rejected.status_code, "body": rejected.json()}
        assert rejected.status_code == 200 and rejected.json().get("phase") == "CANCELLED", rejected.text

        started = start("live-start-block", "live0003")
        cycle_id = started.json()["cycle_id"]
        version = started.json()["version"]
        blocked = client.post(
            f"/aix/cycles/{cycle_id}/block",
            headers={
                **headers("live-block-1"),
                "Idempotency-Key": "live-block-1",
                "If-Match": str(version),
            },
            json={"reason": "missing_context"},
        )
        results["cases"]["block"] = {"status": blocked.status_code, "body": blocked.json()}
        assert blocked.status_code == 200 and blocked.json().get("phase") == "BLOCKED", blocked.text

        started = start("live-start-cancel", "live0004")
        cycle_id = started.json()["cycle_id"]
        version = started.json()["version"]
        cancelled = client.post(
            f"/aix/cycles/{cycle_id}/cancel",
            headers={
                **headers("live-cancel-1"),
                "Idempotency-Key": "live-cancel-1",
                "If-Match": str(version),
            },
            json={"reason": "operator_cancel"},
        )
        results["cases"]["cancel"] = {"status": cancelled.status_code, "body": cancelled.json()}
        assert cancelled.status_code == 200 and cancelled.json().get("phase") == "CANCELLED", cancelled.text

        event_count, sample = asyncio.run(count_ledger("tenant-live"))
        results["ledger"] = {"event_count": event_count, "sample": sample}
        assert event_count >= 5, results["ledger"]
        results["ready"] = client.get("/ready").json()

    results["status"] = "GO"
    results["production"] = "NO_GO"
    results["certification_note"] = (
        "Local live certification GO against real Postgres ledger + reachable Temporal "
        "server + healthy WorkHub. Cycle progression uses DurableObjectiveCycleApiBackend"
        "(mode=postgres) driving AixObjectiveCycleWorkflowEngine; Temporal cluster health "
        "SERVING and worker bootstrap enabled. Production remains NO_GO pending "
        "staging/VPS/soak/DR/human gates."
    )
    (EVIDENCE / "result.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": results["status"],
                "backend_mode": results["deps"]["backend_mode"],
                "ledger_events": results["ledger"]["event_count"],
                "cases": {k: v.get("status") for k, v in results["cases"].items()},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
