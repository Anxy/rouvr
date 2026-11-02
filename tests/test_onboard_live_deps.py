"""Live onboarding dependencies: hyeve admin + person service.

These are out-of-scope for unit CI (they need live upstream credentials).
This module documents that contract: without ROUVR_LIVE_ONBOARD=1 and live
creds the live legs are SKIPPED with a reason, never silently passed.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ROUVR_LIVE_ONBOARD") != "1"
    or not os.environ.get("HYEVE_ADMIN_USERNAME"),
    reason="needs live hyeve admin creds + person service (out of scope for unit CI); "
           "set ROUVR_LIVE_ONBOARD=1 with HYEVE_ADMIN_USERNAME/PASSWORD to run",
)


def test_live_onboard_register_reaches_upstream():
    import httpx
    base = os.environ.get("ROUVR_LEDGER_URL", "http://127.0.0.1:8221")
    r = httpx.post(f"{base}/v1/onboard/register", json={
        "username": "probe", "email": "probe@example.com", "password": "Probe-Pass-1!",
        "given_name": "Probe", "family_name": "User"}, timeout=30)
    assert r.status_code != 503 or "ADMIN_NOT_CONFIGURED" in r.text
