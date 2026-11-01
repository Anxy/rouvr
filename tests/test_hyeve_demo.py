"""Hyeve demo seams (S023): header merge, scope-required, file backend.

Pure tests run offline. Live legs run only with ROUVR_HYEVE_LIVE=1 plus
HYEVE_AUTH_BASE_URL plus HYEVE_WORKHUB_BASE_URL plus
ROUVR_HYEVE_CREDS_FILE, and skip otherwise. Comms delivery is parked:
the demo project does not exist on WorkHub yet (deployment gate), so
the send leg asserts the park instead of simulating success.
"""
import os
from pathlib import Path

import pytest

from rouvr import hyeve
from rouvr.hyeve import FileBackend, merge_headers, require_scope

SKIP = ("live Hyeve legs need ROUVR_HYEVE_LIVE=1, HYEVE_AUTH_BASE_URL, "
        "HYEVE_WORKHUB_BASE_URL and ROUVR_HYEVE_CREDS_FILE")


def test_merge_headers_strips_identity_and_sets_scope():
    merged = merge_headers(
        {"Authorization": "Bearer decoy-inbound", "X-Hyeve-User-Id": "u",
         "X-Permissions": "p", "Content-Type": "application/json"},
        tenant="t1", token="tok")
    assert merged["Authorization"] == "Bearer tok"
    assert merged["X-Hyeve-Tenant-Id"] == "t1"
    assert merged["Content-Type"] == "application/json"
    assert "X-Hyeve-User-Id" not in merged
    assert "X-Permissions" not in merged
    assert merged["Idempotency-Key"]


def test_merge_headers_keeps_idempotency_when_given():
    merged = merge_headers({}, tenant="t1", token="tok",
                           idempotency_key="delivery:abc")
    assert merged["Idempotency-Key"] == "delivery:abc"
    with pytest.raises(ValueError):
        merge_headers({}, tenant=" ", token="tok")
    with pytest.raises(ValueError):
        merge_headers({}, tenant="t1", token=" ")


def test_require_scope_refuses_missing():
    assert require_scope("t1", "p1") == ("t1", "p1")
    with pytest.raises(ValueError):
        require_scope("", "p1")
    with pytest.raises(ValueError):
        require_scope("t1", "")


def test_file_backend_delivery_roundtrip(tmp_path):
    backend = FileBackend(tmp_path / "outbox")
    first = backend.deliver("report-a", "run:2026-11-01", "primary")
    second = backend.deliver("report-B", "run:2026-11-01", "primary")
    assert first == second  # idempotent: same key plus sink, same receipt
    assert backend.readback(first["message_id"]) == "report-a"


def test_file_backend_readback_absent_is_none(tmp_path):
    assert FileBackend(tmp_path / "outbox").readback("no-such-id") is None


def _live_env():
    if os.environ.get("ROUVR_HYEVE_LIVE") != "1":
        return None
    base = os.environ.get("HYEVE_AUTH_BASE_URL", "").strip()
    workhub = os.environ.get("HYEVE_WORKHUB_BASE_URL", "").strip()
    creds = os.environ.get("ROUVR_HYEVE_CREDS_FILE", "").strip()
    if not base or not workhub or not creds:
        return None
    return base, workhub, creds


def _load_creds(creds_path):
    import json

    return json.loads(Path(creds_path).read_text(encoding="utf-8"))


def test_live_mint_runs_with_creds():
    env = _live_env()
    if env is None:
        pytest.skip(SKIP)
    import asyncio

    import httpx

    from rouvr.workhub.client import TokenSource

    base, _workhub, creds_path = env
    payload = _load_creds(creds_path)

    async def mint():
        async with httpx.AsyncClient(timeout=15) as http:
            source = TokenSource(
                http, payload["credential_id"], payload["client_secret"],
                auth_base=base, tenant=payload["tenant_id"],
                audience=payload["audience"])
            return await source.get()

    assert asyncio.run(mint()).strip()


def test_live_send_parked_on_project_gate():
    """Delivery stays parked until the demo project exists on WorkHub.

    Asserts the park (no accessible project). When the deployment gate
    opens this fails by design: replace it with the real send/readback
    test then. Comms stays parked until then.
    """
    env = _live_env()
    if env is None:
        pytest.skip(SKIP)
    import asyncio

    import httpx

    from rouvr.workhub.client import TokenSource

    base, workhub, creds_path = env
    payload = _load_creds(creds_path)

    async def probe():
        async with httpx.AsyncClient(timeout=15) as http:
            source = TokenSource(
                http, payload["credential_id"], payload["client_secret"],
                auth_base=base, tenant=payload["tenant_id"],
                audience=payload["audience"])
            token = await source.get()
            headers = merge_headers({}, tenant=payload["tenant_id"], token=token)
            return await http.get(f"{workhub}/api/v1/capabilities",
                                  headers=headers)

    response = asyncio.run(probe())
    assert response.status_code in (401, 403, 404), (
        f"project gate looks open (HTTP {response.status_code}); "
        "write the real send/readback test")
    assert hyeve is not None
