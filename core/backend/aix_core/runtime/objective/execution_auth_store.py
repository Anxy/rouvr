"""Durable execution authorization store for the Objective Cycle.

Replaces the former ``_cycle_auth`` in-memory dict. The human/operator JWT
arrives only at the HTTP edge (approve / resume / clarify). This store seals
it to restart-safe disk under the cycle workspace base so Temporal
``dispatch_step`` / ``verify_step`` activities — which deliberately carry no
credentials in workflow history — can resolve execution authorization by
``(tenant_id, cycle_id)`` after arbitrary backend + worker restarts.

Security properties:
- Secrets never enter Temporal workflow/activity payloads (only tenant +
  cycle ids travel, which the activity input already carries).
- Files live in a ``0700`` directory with ``0600`` records; records expire
  (default TTL 6h) and fail closed (``None``) when absent or expired.
- No process-memory fallback: a fresh process with the same workspace base
  resolves the same records.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_TTL_HOURS = 6
STORE_DIRNAME = ".aix-exec-auth"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_ts(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


@dataclass
class ExecutionAuthRecord:
    execution_id: str
    tenant_id: str
    cycle_id: str
    actor_id: str
    issued_at: str
    expires_at: str
    bearer: str
    correlation_id: str = ""


class ExecutionAuthStore:
    """Restart-safe execution authorization, keyed by (tenant_id, cycle_id)."""

    def __init__(self, workspace_base: str | os.PathLike[str] | None) -> None:
        base = Path(str(workspace_base or "")).expanduser() if workspace_base else None
        self._dir = base / STORE_DIRNAME if base else None
        self._cache: dict[str, ExecutionAuthRecord] = {}

    @property
    def durable(self) -> bool:
        return self._dir is not None

    def _path_for(self, tenant_id: str, cycle_id: str) -> Path | None:
        if self._dir is None or not tenant_id or not cycle_id:
            return None
        safe_tenant = "".join(c for c in tenant_id if c.isalnum() or c in ("-", "_")).strip() or "tenant"
        safe_cycle = "".join(c for c in cycle_id if c.isalnum() or c in ("-", "_")).strip()
        if not safe_cycle:
            return None
        return self._dir / f"{safe_tenant}__{safe_cycle}.json"

    def seal(
        self,
        *,
        tenant_id: str,
        cycle_id: str,
        bearer: str,
        actor_id: str = "",
        correlation_id: str = "",
        execution_id: str = "",
        ttl_hours: int = DEFAULT_TTL_HOURS,
    ) -> ExecutionAuthRecord | None:
        """Persist execution authorization. No-op (None) when bearer is empty."""
        token = str(bearer or "").strip()
        if not token or not str(tenant_id or "").strip() or not str(cycle_id or "").strip():
            return None
        now = _utc_now()
        record = ExecutionAuthRecord(
            execution_id=execution_id or f"exec_{now.strftime('%Y%m%d%H%M%S%f')}",
            tenant_id=str(tenant_id).strip(),
            cycle_id=str(cycle_id).strip(),
            actor_id=str(actor_id or "").strip(),
            issued_at=now.isoformat(),
            expires_at=(now + timedelta(hours=max(1, int(ttl_hours or DEFAULT_TTL_HOURS)))).isoformat(),
            bearer=token,
            correlation_id=str(correlation_id or "").strip(),
        )
        self._cache[f"{record.tenant_id}\x00{record.cycle_id}"] = record
        path = self._path_for(record.tenant_id, record.cycle_id)
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.chmod(path.parent, 0o700)
                except OSError:
                    pass
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(asdict(record)), encoding="utf-8")
                os.chmod(tmp, 0o600)
                os.replace(tmp, path)
            except OSError:
                pass
        return record

    def _read_record(self, tenant_id: str, cycle_id: str) -> ExecutionAuthRecord | None:
        key = f"{tenant_id}\x00{cycle_id}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        path = self._path_for(tenant_id, cycle_id)
        if path is None or not path.is_file():
            return None
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict) or not str(raw.get("bearer") or "").strip():
            return None
        try:
            record = ExecutionAuthRecord(
                execution_id=str(raw.get("execution_id") or ""),
                tenant_id=str(raw.get("tenant_id") or tenant_id),
                cycle_id=str(raw.get("cycle_id") or cycle_id),
                actor_id=str(raw.get("actor_id") or ""),
                issued_at=str(raw.get("issued_at") or ""),
                expires_at=str(raw.get("expires_at") or ""),
                bearer=str(raw.get("bearer") or ""),
                correlation_id=str(raw.get("correlation_id") or ""),
            )
        except (TypeError, ValueError):
            return None
        self._cache[key] = record
        return record

    def resolve(self, *, tenant_id: str, cycle_id: str) -> str | None:
        """Return the live bearer for (tenant, cycle), or None (fail closed)."""
        tenant = str(tenant_id or "").strip()
        cycle = str(cycle_id or "").strip()
        if not tenant or not cycle:
            return None
        record = self._read_record(tenant, cycle)
        if record is None:
            return None
        expires = _parse_ts(record.expires_at)
        if expires is None or _utc_now() >= expires:
            self.revoke(tenant_id=tenant, cycle_id=cycle)
            return None
        return record.bearer or None

    def revoke(self, *, tenant_id: str, cycle_id: str) -> None:
        key = f"{str(tenant_id or '').strip()}\x00{str(cycle_id or '').strip()}"
        self._cache.pop(key, None)
        path = self._path_for(str(tenant_id or "").strip(), str(cycle_id or "").strip())
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
