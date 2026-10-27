"""Context Capsule helpers (pure builders + thin Redis calls).

Key layout (all under the ``aix:`` namespace in ``aix-redis``):

- ``aix:session:{session_id}`` — capsule JSON, TTL 30d hot.
- ``aix:sessions:recent:{tenant_id}`` — LIST, trimmed to last 3.

The capsule carries pointers, never full copies: CodeIntel artifact
IDs, WorkHub mandate IDs, lane checkpoint. Hydration resolves pointers
at boot; persistence/compaction is Phase 3 (Postgres).
"""

from __future__ import annotations

import time
from typing import Any

CAPSULE_TTL_SECONDS = 30 * 24 * 3600
RECENT_LIST_MAX = 3
CAPSULE_VERSION = 1


def capsule_key(session_id: str) -> str:
    return f"aix:session:{session_id}"


def recent_list_key(tenant_id: str) -> str:
    return f"aix:sessions:recent:{tenant_id}"


def build_capsule(
    *,
    session_id: str,
    tenant_id: str,
    intent: str,
    lane_checkpoint: str,
    lane_tag: str,
    codeintel_artifacts: list[str] | None = None,
    workhub_mandate_ids: list[str] | None = None,
    persona_bindings: list[str] | None = None,
    guardrails_hash: str = "",
    voice_transcript_ref: str | None = None,
) -> dict[str, Any]:
    """Build the capsule payload (pure: no I/O, unit-tested)."""
    return {
        "capsule_version": CAPSULE_VERSION,
        "session_id": session_id,
        "tenant_id": tenant_id,
        "intent": intent,
        "voice_transcript_ref": voice_transcript_ref,
        "lane_checkpoint": lane_checkpoint,
        "lane_tag": lane_tag,
        "codeintel_snapshot": {
            "queried": bool(codeintel_artifacts),
            "artifacts": list(codeintel_artifacts or []),
        },
        "workhub_snapshot": {
            "open_mandate_ids": list(workhub_mandate_ids or []),
        },
        "persona_bindings": list(persona_bindings or []),
        "guardrails_hash": guardrails_hash,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def write_capsule(redis_client: Any, capsule: dict[str, Any]) -> None:
    """Write capsule + push session onto the tenant recent list."""
    session_id = capsule["session_id"]
    tenant_id = capsule["tenant_id"]
    import json

    redis_client.set(
        capsule_key(session_id), json.dumps(capsule), ex=CAPSULE_TTL_SECONDS
    )
    rkey = recent_list_key(tenant_id)
    redis_client.lpush(rkey, session_id)
    redis_client.ltrim(rkey, 0, RECENT_LIST_MAX - 1)


def read_capsule(redis_client: Any, session_id: str) -> dict[str, Any] | None:
    """Read one capsule; None when expired/missing (fail-closed)."""
    import json

    raw = redis_client.get(capsule_key(session_id))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


def recent_sessions(redis_client: Any, tenant_id: str) -> list[str]:
    """Last-3 session IDs for instant boot context (no grep)."""
    items = redis_client.lrange(recent_list_key(tenant_id), 0, -1)
    return [
        i.decode("utf-8") if isinstance(i, bytes) else str(i) for i in items
    ]


def build_run_record(
    *,
    envelope_id: str,
    tenant_id: str,
    intent: str,
    trace_id: str,
) -> dict[str, Any]:
    """Build the ``aix_runs`` row for a session (pure, unit-tested).

    Matches live ``aix_runs`` NOT NULL columns: tenant_id, run_id,
    objective_id, phase, trace_id, payload (plan_revision/version
    default at the DB layer when omitted here — included explicitly).
    """
    return {
        "tenant_id": tenant_id,
        "run_id": envelope_id,
        "objective_id": envelope_id,
        "phase": "STARTING",
        "trace_id": trace_id,
        "plan_revision": 1,
        "version": 1,
        "payload": {"intent": intent},
    }
