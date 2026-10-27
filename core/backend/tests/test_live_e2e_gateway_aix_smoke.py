from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest


@pytest.mark.e2e
def test_live_gateway_to_aix_devexecutor_smoke(tmp_path: Path) -> None:
    flag = str(os.getenv("HYEVE_LIVE_E2E_SMOKE", "")).strip().lower()
    if flag not in {"1", "true", "yes"}:
        pytest.skip("Set HYEVE_LIVE_E2E_SMOKE=1 to run live smoke tests.")

    gateway_base = str(os.getenv("HYEVE_LIVE_GATEWAY_BASE_URL", "http://127.0.0.1:8000")).rstrip("/")
    auth = str(os.getenv("HYEVE_LIVE_AUTHORIZATION", "Bearer test-token"))
    tenant_id = str(os.getenv("HYEVE_LIVE_TENANT_ID", "default"))
    project_id = str(os.getenv("HYEVE_LIVE_PROJECT_ID", "bh-migrated-7-aix"))

    headers = {
        "Authorization": auth,
        "X-Tenant-ID": tenant_id,
        "X-Correlation-ID": "corr-live-aix-smoke",
        "Idempotency-Key": "idem-live-aix-smoke-1",
        "Content-Type": "application/json",
    }
    body = {
        "project_id": project_id,
        "task_number": 1107,
        "workspace_root": str(tmp_path),
        "steps": [{"tool": "get_status", "args": {}}],
    }

    with httpx.Client(timeout=30) as client:
        create = client.post(f"{gateway_base}/api/aix/devexecutor/runs", headers=headers, json=body)
        assert create.status_code == 200, create.text
        create_payload = create.json()
        assert create_payload.get("success") is True
        run_id = create_payload.get("data", {}).get("run_id")
        assert isinstance(run_id, str) and run_id

        transcript = client.get(f"{gateway_base}/api/aix/devexecutor/runs/{run_id}/transcript", headers=headers)
        assert transcript.status_code == 200, transcript.text
        tx_payload = transcript.json()
        items = tx_payload.get("data", {}).get("transcript") or []
        assert items
        assert bool(items[0].get("ok")) is True
