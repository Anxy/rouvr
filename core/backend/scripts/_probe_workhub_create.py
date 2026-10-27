"""One-off probe: WorkHub POST /api/v1/tasks with env JWT. Do not commit secrets."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / ".env"
FE_CANDIDATES = [
    ROOT / "AIX-Frontend" / ".env.local",
    ROOT / "AIX-Frontend" / ".env",
]


def _load_token() -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("HYEVE_AIX_SERVICE_WORKHUB_API_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("WORKHUB_API_TOKEN missing")


def _jwt_payload(tok: str) -> dict:
    parts = tok.split(".")
    pad = "=" * ((4 - len(parts[1]) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(parts[1] + pad))


def _project_id() -> str | None:
    for path in FE_CANDIDATES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if "PROJECT" in key:
                print(f"fe_env {path.name} {key}={value[:48]}")
            if key in ("AIX_DEFAULT_PROJECT_ID", "NEXT_PUBLIC_AIX_DEFAULT_PROJECT_ID"):
                return value
    return None


async def main() -> None:
    tok = _load_token()
    payload = _jwt_payload(tok)
    now = int(time.time())
    exp = int(payload.get("exp") or 0)
    perms = payload.get("permissions") or []
    task_perms = sorted(p for p in perms if "task" in str(p).lower())
    print("tenant", payload.get("tenant_id"))
    print("roles", payload.get("roles"))
    print("task_perms", task_perms)
    print("expired", exp < now, "hours_left", round((exp - now) / 3600, 2) if exp else None)

    pid = _project_id()
    print("project_id", pid)
    if not pid:
        return

    headers = {
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
        "X-Hyeve-Tenant-Id": str(payload.get("tenant_id") or ""),
        "X-Project-Id": pid,
        "Idempotency-Key": f"aix-probe-{now}",
    }
    body = {
        "project_id": pid,
        "title": "AIX probe",
        "description": "probe",
        "priority": "medium",
        "status": "queued",
        "verifier_required": True,
        "evidence_required": True,
        "allow_weak_closure": False,
        "metadata_json": {"source": "probe"},
        "tags_json": ["probe"],
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            "http://127.0.0.1:8226/api/v1/tasks",
            headers=headers,
            json=body,
        )
        print("status", response.status_code)
        print("body", response.text[:800])


if __name__ == "__main__":
    asyncio.run(main())
