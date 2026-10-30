"""rouvr/ids.py — deterministic identifiers (doc: workflow_id / activity_id / handoff fence)."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(obj: Any, n: int) -> str:
    data = obj if isinstance(obj, bytes) else canonical_json(obj)
    return hashlib.sha256(data).hexdigest()[:n]


def workflow_id(tenant: str, task_id: str, contract_rev: str) -> str:
    return f"rouvr-task-{_SAFE.sub('_', tenant)}-{_SAFE.sub('_', task_id)}-{sha([tenant, task_id, contract_rev], 16)}"


def activity_id(name: str, *parts: Any) -> str:
    return f"rouvr-activity-{name}-{sha([name, *parts], 20)}"


def idempotency_key(op: str, *parts: Any) -> str:
    return f"rouvr-{op}-{sha([op, *parts], 32)}"


def handoff_fence(attempt: int, epoch: int) -> str:
    return f"temporal-handoff-{attempt}-e{epoch}"


_KEY = (r"(?:access[ _-]?token|refresh[ _-]?token|id[ _-]?token|auth[ _-]?token|"
        r"secret[ _-]?key|access[ _-]?key|private[ _-]?key|signing[ _-]?key|api[ _-]?key|"
        r"client[ _-]?secret|token|secret|password|passwd|passphrase|pwd|credential)"
        r"(?:[ _-]?[A-Za-z0-9]+)?")
_SECRETS = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]+"
    r"|authorization[\"']?\s*[:=]\s*[\"']?(?:[A-Za-z][\w.-]*\s+)?[\"']?[^\s\"',}\]]+"
    r"|[\"']?" + _KEY + r"[\"']?\s*(?:=|:|\bis\b)\s*([\"'])[^\n]*?\1"
    r"|\"?" + _KEY + r"\"?\s*(?:=|:|\bis\b)\s*\"?[^\s\"',}\]]+"
    r"|'" + _KEY + r"'\s*(?:=|:|\bis\b)\s*'?[^\s\"',}\]]+"
    r"|" + _KEY + r"\s*(?:=|:|\bis\b)\s*[^\s\"',}\]]+)")


def sanitize_for_log(text: str, limit: int = 2000) -> str:
    return _SECRETS.sub("[REDACTED]", text)[:limit]
