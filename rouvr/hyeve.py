"""Hyeve service seams for the demo (S023).

Pure outbound shapes for Hyeve services; no network here.
The live transport (mint against the VPS1 auth origin, comms send plus
provider status readback) lands when lane-ops-Codex supplies credentials.

Seam contract a live backend must honor:
  deliver(report, key, sink) -> {"ok": True, "message_id": str}
  readback(msg_id) -> str | None   (absent is never success; fail closed)
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from uuid import uuid4

# Inbound caller identity must never flow downstream.
IDENTITY_HEADERS = frozenset({"authorization", "x-hyeve-user-id", "x-permissions"})

TENANT_HEADER = "X-Hyeve-Tenant-Id"
IDEMPOTENCY_HEADER = "Idempotency-Key"


def merge_headers(headers: dict[str, str] | None, *, tenant: str, token: str,
                  idempotency_key: str | None = None) -> dict[str, str]:
    """Build outbound Hyeve headers from untrusted inbound headers.

    Strips caller identity case-insensitively, requires tenant plus token,
    keeps a caller idempotency key or mints one. Pure: no network, no secrets
    invented (the token comes from the host credential, never from inputs).
    """
    if not tenant.strip():
        raise ValueError("tenant is required: no default tenant")
    if not token.strip():
        raise ValueError("service token is required: mint first, never default")
    merged = {name: value for name, value in (headers or {}).items()
              if name.casefold() not in IDENTITY_HEADERS}
    merged["Authorization"] = f"Bearer {token.strip()}"
    merged[TENANT_HEADER] = tenant.strip()
    if IDEMPOTENCY_HEADER not in merged:
        merged[IDEMPOTENCY_HEADER] = idempotency_key or str(uuid4())
    return merged


def require_scope(tenant: str, project: str) -> tuple[str, str]:
    """Mandatory demo scope. Both fields required; no defaults anywhere."""
    if not tenant.strip() or not project.strip():
        raise ValueError("demo scope needs tenant plus project: set both, never default")
    return tenant.strip(), project.strip()


def message_id(key: str, sink: str) -> str:
    """Deterministic provider-style message id (same formula as the demo)."""
    return sha256(f"{key}:{sink}".encode()).hexdigest()[:16]


class FileBackend:
    """Offline delivery backend: the local file outbox behind the seam."""

    def __init__(self, outbox: Path) -> None:
        self.outbox = outbox

    def deliver(self, report: str, key: str, sink: str) -> dict:
        """Idempotent write-once delivery; reruns return the same receipt."""
        msg_id = message_id(key, sink)
        target = self.outbox / f"{msg_id}.txt"
        self.outbox.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(report)
        return {"ok": True, "message_id": msg_id}

    def readback(self, msg_id: str) -> str | None:
        """Read back a delivered message; absent is None, never success."""
        target = self.outbox / f"{msg_id}.txt"
        return target.read_text() if target.is_file() else None
