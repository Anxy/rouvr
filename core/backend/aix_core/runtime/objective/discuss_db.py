"""Postgres persistence for objective-discuss threads/turns + admin audit rows."""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import urlparse
import asyncpg  # type: ignore[import-untyped]

from backend.infrastructure.postgres.db_schema_utils import create_index_tolerating_non_owner
from backend.aix_core.runtime.objective.discuss_store import normalize_usage, new_thread_id
from backend.aix_core.runtime.objective.repository import resolve_test_database_url
from backend.aix_core.runtime.objective.ulid_compat import new_ulid

logger = logging.getLogger(__name__)

DISCUSS_THREADS_TABLE = "aix_discuss_threads"
DISCUSS_TURNS_TABLE = "aix_discuss_turns"
ADMIN_AUDIT_TABLE = "admin_audit_log"

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


class DiscussDbError(RuntimeError):
    """Raised when discuss Postgres is unreachable or query setup fails fatally."""


def _to_asyncpg_dsn(url: str) -> str:
    value = str(url or "").strip()
    if value.startswith("postgresql+asyncpg://"):
        return "postgresql://" + value[len("postgresql+asyncpg://") :]
    if value.startswith("postgres+asyncpg://"):
        return "postgresql://" + value[len("postgres+asyncpg://") :]
    return value


def resolve_discuss_database_url() -> str | None:
    for key in (
        "AIX_OBJECTIVE_CYCLE_TEST_DATABASE_URL",
        "AIX_LEDGER_DAL_DATABASE_URL",
        "HYEVE_AIX_SERVICE_DATABASE_URL",
        "DATABASE_URL",
    ):
        value = os.getenv(key, "").strip()
        if value:
            return _to_asyncpg_dsn(value)
    test_url = resolve_test_database_url()
    return _to_asyncpg_dsn(test_url) if test_url else None


def _dsn_is_local(database_url: str) -> bool:
    try:
        host = (urlparse(database_url).hostname or "").strip().lower()
    except ValueError:
        return False
    return host in _LOCAL_HOSTS


async def _connect_discuss(database_url: str) -> asyncpg.Connection:
    """Open a short-lived discuss connection.

    Local DSNs force ``ssl=False`` — asyncpg's SSL handshake path can hang against
    plain local Postgres until the connect timeout, which surfaces as API 500s.
    """
    connect_kwargs: dict[str, Any] = {
        "command_timeout": 5.0,
        "timeout": 5.0,
    }
    if _dsn_is_local(database_url):
        connect_kwargs["ssl"] = False
    try:
        return await asyncpg.connect(database_url, **connect_kwargs)
    except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
        logger.warning("discuss_db_connect_failed", extra={"error": type(exc).__name__})
        raise DiscussDbError("discuss database connection failed") from exc


async def ensure_discuss_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {DISCUSS_THREADS_TABLE} (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            cumulative_prompt_tokens INTEGER,
            cumulative_completion_tokens INTEGER,
            cumulative_total_tokens INTEGER,
            metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, thread_id)
        )
        """
    )
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {DISCUSS_TURNS_TABLE} (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            actor_id TEXT NOT NULL,
            messages JSONB NOT NULL,
            assistant_reply TEXT NOT NULL,
            task_class TEXT,
            proposed_scope JSONB NOT NULL DEFAULT '[]'::jsonb,
            ambiguous BOOLEAN NOT NULL DEFAULT FALSE,
            provider TEXT,
            model TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, thread_id, turn_index)
        )
        """
    )
    await conn.execute(
        f"""
        ALTER TABLE {DISCUSS_TURNS_TABLE}
        ADD COLUMN IF NOT EXISTS intake_raw_json JSONB
        """
    )
    await conn.execute(
        f"""
        ALTER TABLE {DISCUSS_TURNS_TABLE}
        ADD COLUMN IF NOT EXISTS normalized_objective TEXT
        """
    )
    await create_index_tolerating_non_owner(
        conn,
        index_name=f"idx_{DISCUSS_TURNS_TABLE}_tenant_thread",
        statement=(
            f"CREATE INDEX IF NOT EXISTS idx_{DISCUSS_TURNS_TABLE}_tenant_thread "
            f"ON {DISCUSS_TURNS_TABLE} (tenant_id, thread_id, turn_index)"
        ),
        logger=logger,
    )
    # Ensure admin_audit_log exists for discuss audit writes (idempotent).
    await conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ADMIN_AUDIT_TABLE} (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            record_version INTEGER NOT NULL DEFAULT 1,
            project_id TEXT,
            actor_sub TEXT NOT NULL,
            actor_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
            action TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id TEXT,
            request_id TEXT,
            metadata_json JSONB DEFAULT '{{}}'::jsonb,
            custom_data JSONB DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_by TEXT,
            updated_by TEXT,
            deleted_at TIMESTAMPTZ
        )
        """
    )


async def persist_discuss_turn_db(
    *,
    tenant_id: str,
    actor_id: str,
    thread_id: str | None,
    messages: list[dict[str, str]],
    reply: str,
    task_class: str | None,
    proposed_scope: list[str],
    ambiguous: bool,
    provider: str | None,
    model: str | None,
    usage: dict[str, Any] | None,
    request_id: str | None = None,
    roles: list[str] | None = None,
    raw_model_output: str | None = None,
    normalized_objective: str | None = None,
    intake_scope_code: str | None = None,
    intake_scope_fallback: bool = False,
) -> dict[str, Any]:
    """Persist discuss thread/turn + admin audit_log row into hyeve_aix_service."""
    database_url = resolve_discuss_database_url()
    if not database_url:
        raise RuntimeError("AIX discuss database URL is not configured")

    resolved_thread = (thread_id or "").strip() or new_thread_id()
    turn_usage = normalize_usage(usage)
    thread_pk = f"dthr_{new_ulid()[:16]}"
    turn_pk = f"dtrn_{new_ulid()[:16]}"
    audit_pk = f"aud_{new_ulid()[:16]}"
    raw_text = str(raw_model_output or "")
    raw_parsed: Any = None
    if raw_text.strip():
        try:
            raw_parsed = json.loads(raw_text)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_parsed = {"raw_text": raw_text}
    intake_raw = {
        "raw_model_output": raw_text or None,
        "raw_model_json": raw_parsed,
        "normalized_objective": normalized_objective,
        "intake_scope_code": intake_scope_code,
        "intake_scope_fallback": bool(intake_scope_fallback),
    }

    conn = await _connect_discuss(database_url)
    try:
        await ensure_discuss_schema(conn)
        async with conn.transaction():
            existing = await conn.fetchrow(
                f"""
                SELECT id, cumulative_prompt_tokens, cumulative_completion_tokens, cumulative_total_tokens
                FROM {DISCUSS_THREADS_TABLE}
                WHERE tenant_id = $1 AND thread_id = $2
                FOR UPDATE
                """,
                tenant_id,
                resolved_thread,
            )
            if existing is None:
                await conn.execute(
                    f"""
                    INSERT INTO {DISCUSS_THREADS_TABLE} (
                        id, tenant_id, thread_id, actor_id, status,
                        cumulative_prompt_tokens, cumulative_completion_tokens, cumulative_total_tokens,
                        metadata_json
                    ) VALUES ($1, $2, $3, $4, 'open', $5, $6, $7, $8::jsonb)
                    """,
                    thread_pk,
                    tenant_id,
                    resolved_thread,
                    actor_id,
                    turn_usage.get("prompt_tokens"),
                    turn_usage.get("completion_tokens"),
                    turn_usage.get("total_tokens"),
                    json.dumps({"source": "aix.objectives.discuss"}),
                )
                turn_index = 1
                cumulative = dict(turn_usage)
            else:
                thread_pk = existing["id"]
                turn_index = int(
                    await conn.fetchval(
                        f"""
                        SELECT COALESCE(MAX(turn_index), 0) + 1
                        FROM {DISCUSS_TURNS_TABLE}
                        WHERE tenant_id = $1 AND thread_id = $2
                        """,
                        tenant_id,
                        resolved_thread,
                    )
                    or 1
                )

                def _add(a: Any, b: Any) -> int | None:
                    if a is None and b is None:
                        return None
                    return int(a or 0) + int(b or 0)

                cumulative = {
                    "prompt_tokens": _add(existing["cumulative_prompt_tokens"], turn_usage.get("prompt_tokens")),
                    "completion_tokens": _add(
                        existing["cumulative_completion_tokens"], turn_usage.get("completion_tokens")
                    ),
                    "total_tokens": _add(existing["cumulative_total_tokens"], turn_usage.get("total_tokens")),
                }
                await conn.execute(
                    f"""
                    UPDATE {DISCUSS_THREADS_TABLE}
                    SET actor_id = $3,
                        cumulative_prompt_tokens = $4,
                        cumulative_completion_tokens = $5,
                        cumulative_total_tokens = $6,
                        updated_at = now()
                    WHERE tenant_id = $1 AND thread_id = $2
                    """,
                    tenant_id,
                    resolved_thread,
                    actor_id,
                    cumulative.get("prompt_tokens"),
                    cumulative.get("completion_tokens"),
                    cumulative.get("total_tokens"),
                )

            await conn.execute(
                f"""
                INSERT INTO {DISCUSS_TURNS_TABLE} (
                    id, tenant_id, thread_id, turn_index, actor_id,
                    messages, assistant_reply, task_class, proposed_scope, ambiguous,
                    provider, model, prompt_tokens, completion_tokens, total_tokens,
                    intake_raw_json, normalized_objective
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6::jsonb, $7, $8, $9::jsonb, $10,
                    $11, $12, $13, $14, $15,
                    $16::jsonb, $17
                )
                """,
                turn_pk,
                tenant_id,
                resolved_thread,
                turn_index,
                actor_id,
                json.dumps(messages),
                reply,
                task_class,
                json.dumps(proposed_scope),
                bool(ambiguous),
                provider,
                model,
                turn_usage.get("prompt_tokens"),
                turn_usage.get("completion_tokens"),
                turn_usage.get("total_tokens"),
                json.dumps(intake_raw),
                normalized_objective,
            )

            metadata = {
                "thread_id": resolved_thread,
                "turn_id": turn_pk,
                "turn_index": turn_index,
                "task_class": task_class,
                "ambiguous": ambiguous,
                "provider": provider,
                "model": model,
                "usage": turn_usage,
                "cumulative_usage": cumulative,
                "normalized_objective": normalized_objective,
                "intake_scope_code": intake_scope_code,
                "intake_scope_fallback": bool(intake_scope_fallback),
                "intake_raw": intake_raw,
            }
            await conn.execute(
                f"""
                INSERT INTO {ADMIN_AUDIT_TABLE} (
                    id, tenant_id, record_version, actor_sub, actor_roles,
                    action, resource_type, resource_id, request_id,
                    metadata_json, custom_data, created_by, updated_by
                ) VALUES (
                    $1, $2, 1, $3, $4::jsonb,
                    $5, $6, $7, $8,
                    $9::jsonb, $10::jsonb, $3, $3
                )
                """,
                audit_pk,
                tenant_id,
                actor_id,
                json.dumps(list(roles or [])),
                "aix.objectives.discuss.turn",
                "aix_discuss_thread",
                resolved_thread,
                request_id,
                json.dumps(metadata),
                json.dumps({"source": "objective_cycle_router.discuss"}),
            )
    finally:
        await conn.close()

    return {
        "thread_id": resolved_thread,
        "thread_db_id": thread_pk,
        "turn_id": turn_pk,
        "turn_index": turn_index,
        "audit_id": audit_pk,
        "usage": turn_usage,
        "cumulative_usage": cumulative,
        "db": "hyeve_aix_service",
    }


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _latest_user_text(messages: list[Any]) -> str:
    for item in reversed(messages):
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        text = str(item.get("content") or "").strip()
        if text:
            return text
    return ""


async def list_discuss_threads_db(
    *,
    tenant_id: str,
    limit: int = 24,
    actor_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent discuss threads for the tenant (newest first), with last-turn preview."""
    database_url = resolve_discuss_database_url()
    if not database_url:
        return []

    capped = max(1, min(int(limit or 24), 100))
    conn = await _connect_discuss(database_url)
    try:
        await ensure_discuss_schema(conn)
        if actor_id:
            rows = await conn.fetch(
                f"""
                SELECT
                    t.thread_id,
                    t.actor_id,
                    t.status,
                    t.cumulative_prompt_tokens,
                    t.cumulative_completion_tokens,
                    t.cumulative_total_tokens,
                    t.created_at,
                    t.updated_at,
                    u.turn_index,
                    u.messages,
                    u.assistant_reply,
                    u.task_class,
                    u.provider,
                    u.model,
                    u.ambiguous
                FROM {DISCUSS_THREADS_TABLE} t
                LEFT JOIN LATERAL (
                    SELECT turn_index, messages, assistant_reply, task_class, provider, model, ambiguous
                    FROM {DISCUSS_TURNS_TABLE}
                    WHERE tenant_id = t.tenant_id AND thread_id = t.thread_id
                    ORDER BY turn_index DESC
                    LIMIT 1
                ) u ON TRUE
                WHERE t.tenant_id = $1 AND t.actor_id = $2
                ORDER BY t.updated_at DESC
                LIMIT $3
                """,
                tenant_id,
                actor_id,
                capped,
            )
        else:
            rows = await conn.fetch(
                f"""
                SELECT
                    t.thread_id,
                    t.actor_id,
                    t.status,
                    t.cumulative_prompt_tokens,
                    t.cumulative_completion_tokens,
                    t.cumulative_total_tokens,
                    t.created_at,
                    t.updated_at,
                    u.turn_index,
                    u.messages,
                    u.assistant_reply,
                    u.task_class,
                    u.provider,
                    u.model,
                    u.ambiguous
                FROM {DISCUSS_THREADS_TABLE} t
                LEFT JOIN LATERAL (
                    SELECT turn_index, messages, assistant_reply, task_class, provider, model, ambiguous
                    FROM {DISCUSS_TURNS_TABLE}
                    WHERE tenant_id = t.tenant_id AND thread_id = t.thread_id
                    ORDER BY turn_index DESC
                    LIMIT 1
                ) u ON TRUE
                WHERE t.tenant_id = $1
                ORDER BY t.updated_at DESC
                LIMIT $2
                """,
                tenant_id,
                capped,
            )
    finally:
        await conn.close()

    items: list[dict[str, Any]] = []
    for row in rows:
        messages = _json_list(row["messages"])
        items.append(
            {
                "thread_id": row["thread_id"],
                "actor_id": row["actor_id"],
                "status": row["status"] or "open",
                "turn_count": int(row["turn_index"] or 0),
                "task_class": row["task_class"],
                "ambiguous": bool(row["ambiguous"]) if row["ambiguous"] is not None else None,
                "provider": row["provider"],
                "model": row["model"],
                "latest_user_message": _latest_user_text(messages),
                "latest_assistant_reply": str(row["assistant_reply"] or "").strip(),
                "cumulative_usage": {
                    "prompt_tokens": row["cumulative_prompt_tokens"],
                    "completion_tokens": row["cumulative_completion_tokens"],
                    "total_tokens": row["cumulative_total_tokens"],
                },
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }
        )
    return items


async def get_discuss_thread_db(*, tenant_id: str, thread_id: str) -> dict[str, Any] | None:
    """Load one discuss thread and reconstruct chat messages from the latest turn."""
    database_url = resolve_discuss_database_url()
    if not database_url:
        return None

    resolved = str(thread_id or "").strip()
    if not resolved:
        return None

    conn = await _connect_discuss(database_url)
    try:
        await ensure_discuss_schema(conn)
        thread = await conn.fetchrow(
            f"""
            SELECT thread_id, actor_id, status,
                   cumulative_prompt_tokens, cumulative_completion_tokens, cumulative_total_tokens,
                   created_at, updated_at
            FROM {DISCUSS_THREADS_TABLE}
            WHERE tenant_id = $1 AND thread_id = $2
            """,
            tenant_id,
            resolved,
        )
        if thread is None:
            return None
        turn = await conn.fetchrow(
            f"""
            SELECT turn_index, messages, assistant_reply, task_class, proposed_scope, ambiguous,
                   provider, model
            FROM {DISCUSS_TURNS_TABLE}
            WHERE tenant_id = $1 AND thread_id = $2
            ORDER BY turn_index DESC
            LIMIT 1
            """,
            tenant_id,
            resolved,
        )
    finally:
        await conn.close()

    messages = _json_list(turn["messages"]) if turn else []
    chat: list[dict[str, str]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            chat.append({"role": role, "content": content})
    if turn and str(turn["assistant_reply"] or "").strip():
        reply = str(turn["assistant_reply"]).strip()
        if not chat or chat[-1].get("role") != "assistant" or chat[-1].get("content") != reply:
            chat.append({"role": "assistant", "content": reply})

    return {
        "thread_id": thread["thread_id"],
        "actor_id": thread["actor_id"],
        "status": thread["status"] or "open",
        "task_class": turn["task_class"] if turn else None,
        "proposed_scope": _json_list(turn["proposed_scope"]) if turn else [],
        "ambiguous": bool(turn["ambiguous"]) if turn and turn["ambiguous"] is not None else None,
        "provider": turn["provider"] if turn else None,
        "model": turn["model"] if turn else None,
        "messages": chat,
        "cumulative_usage": {
            "prompt_tokens": thread["cumulative_prompt_tokens"],
            "completion_tokens": thread["cumulative_completion_tokens"],
            "total_tokens": thread["cumulative_total_tokens"],
        },
        "created_at": thread["created_at"].isoformat() if thread["created_at"] else None,
        "updated_at": thread["updated_at"].isoformat() if thread["updated_at"] else None,
    }


__all__ = [
    "DISCUSS_THREADS_TABLE",
    "DISCUSS_TURNS_TABLE",
    "ensure_discuss_schema",
    "get_discuss_thread_db",
    "list_discuss_threads_db",
    "persist_discuss_turn_db",
    "resolve_discuss_database_url",
]
