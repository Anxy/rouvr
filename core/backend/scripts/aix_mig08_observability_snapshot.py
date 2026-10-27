from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]

DEFAULT_DSN = "postgresql://postgres:simple@localhost:5432/hyeve_workhub_service"


async def _build_snapshot(*, dsn: str, tenant_id: str) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        totals = await conn.fetchrow(
            """
            SELECT
              (SELECT COUNT(*) FROM tasks t WHERE t.tenant_id = $1 AND t.deleted_at IS NULL
                AND (t.metadata_json->'migration'->>'source') = 'bivrhub')::int AS migrated_tasks,
              (SELECT COUNT(*) FROM task_comments tc
                JOIN tasks t ON t.id = tc.task_id
                WHERE t.tenant_id = $1 AND t.deleted_at IS NULL AND tc.deleted_at IS NULL
                  AND (t.metadata_json->'migration'->>'source') = 'bivrhub'
                  AND tc.created_by = 'aix-bh-migrator')::int AS migrated_comments,
              (SELECT COUNT(*) FROM artefacts a WHERE a.tenant_id = $1 AND a.deleted_at IS NULL
                AND a.artefact_type = 'note'
                AND (a.metadata_json->'migration'->>'source') = 'bivrhub')::int AS migrated_knowledge
            """,
            tenant_id,
        )
        outbox = await conn.fetch(
            """
            SELECT status, COUNT(*)::int AS count
            FROM outbox
            WHERE tenant_id = $1 AND deleted_at IS NULL
            GROUP BY status
            ORDER BY status
            """,
            tenant_id,
        )
        inbox = await conn.fetch(
            """
            SELECT processing_status, COUNT(*)::int AS count
            FROM inbox
            WHERE tenant_id = $1 AND deleted_at IS NULL
            GROUP BY processing_status
            ORDER BY processing_status
            """,
            tenant_id,
        )
        failed = await conn.fetch(
            """
            SELECT id, project_id, event_type, left(error_message, 220) AS error, created_at
            FROM inbox
            WHERE tenant_id = $1
              AND deleted_at IS NULL
              AND processing_status = 'failed'
            ORDER BY created_at DESC
            LIMIT 20
            """,
            tenant_id,
        )
        audit = await conn.fetch(
            """
            SELECT action, COUNT(*)::int AS count
            FROM admin_audit_log
            WHERE tenant_id = $1 AND deleted_at IS NULL
            GROUP BY action
            ORDER BY count DESC, action
            LIMIT 30
            """,
            tenant_id,
        )
        project_rows = await conn.fetch(
            """
            SELECT t.project_id,
                   COUNT(t.id)::int AS migrated_tasks,
                   COUNT(tc.id) FILTER (WHERE tc.created_by = 'aix-bh-migrator')::int AS migrated_comments
            FROM tasks t
            LEFT JOIN task_comments tc ON tc.task_id = t.id AND tc.deleted_at IS NULL
            WHERE t.tenant_id = $1
              AND t.deleted_at IS NULL
              AND (t.metadata_json->'migration'->>'source') = 'bivrhub'
            GROUP BY t.project_id
            ORDER BY t.project_id
            """,
            tenant_id,
        )
        dead_letter_count = sum(int(row["count"]) for row in inbox if str(row["processing_status"]) == "failed")
        return {
            "timestamp": datetime.now(UTC).isoformat(),
            "task": "AIX-MIG-08",
            "tenant_id": tenant_id,
            "metrics": {
                "migrated_tasks": int(totals["migrated_tasks"] or 0),
                "migrated_comments": int(totals["migrated_comments"] or 0),
                "migrated_knowledge": int(totals["migrated_knowledge"] or 0),
                "dead_letter_count": int(dead_letter_count),
            },
            "outbox_by_status": [dict(r) for r in outbox],
            "inbox_by_processing_status": [dict(r) for r in inbox],
            "dead_letter_recent": [dict(r) for r in failed],
            "admin_audit_top_actions": [dict(r) for r in audit],
            "per_project_migration_activity": [dict(r) for r in project_rows],
        }
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate migration observability snapshot (metrics + audit + dead-letter)."
    )
    parser.add_argument("--workhub-dsn", default=DEFAULT_DSN, help="WorkHub Postgres DSN.")
    parser.add_argument("--tenant-id", default="default", help="Tenant id.")
    parser.add_argument(
        "--report-path",
        default="C:/dev/hyeve/_runlogs/aix_mig08_observability_snapshot.json",
        help="Output report path.",
    )
    args = parser.parse_args()

    snapshot = asyncio.run(_build_snapshot(dsn=args.workhub_dsn, tenant_id=args.tenant_id))
    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "metrics": snapshot["metrics"],
                "dead_letter_rows": len(snapshot["dead_letter_recent"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
