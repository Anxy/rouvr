from __future__ import annotations

import argparse
import asyncio
import json
import re
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import asyncpg  # type: ignore[import-untyped]

DEFAULT_BH_CONFIG = Path("C:/dev/bivrhub/config/bh-local.json")
DEFAULT_DSN = "postgresql://postgres:simple@localhost:5432/hyeve_workhub_service"
ALLOWED_LIFECYCLE = {"queued", "running", "blocked", "needs_review", "done", "archived"}


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-") or "project"


def _load_bh_config(path: Path) -> dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Invalid BH config at {path}")
    return parsed


def _request_json(
    *, method: str, url: str, headers: dict[str, str], payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url=url, data=data, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _paginate_bh(*, base_url: str, endpoint: str, headers: dict[str, str], limit: int = 200) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({"limit": limit, "offset": offset})
        payload = _request_json(method="GET", url=f"{base_url}{endpoint}?{query}", headers=headers)
        items = payload.get("data", [])
        if not isinstance(items, list) or not items:
            break
        out.extend(items)
        if len(items) < limit or not payload.get("has_more"):
            break
        offset += limit
    return out


async def _wh_project_metrics(
    *, dsn: str, tenant_id: str, bh_project_id: str, workhub_project_id: str, bh_task_ids: list[str]
) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    try:
        tasks_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM tasks
            WHERE tenant_id = $1
              AND project_id = $2
              AND (metadata_json->'migration'->>'source') = 'bivrhub'
              AND (metadata_json->'migration'->>'bh_project_id') = $3
              AND deleted_at IS NULL
            """,
            tenant_id,
            workhub_project_id,
            bh_project_id,
        )
        distinct_bh_task_ids = await conn.fetch(
            """
            SELECT DISTINCT (metadata_json->'migration'->>'bh_task_id') AS bh_task_id
            FROM tasks
            WHERE tenant_id = $1
              AND project_id = $2
              AND (metadata_json->'migration'->>'source') = 'bivrhub'
              AND (metadata_json->'migration'->>'bh_project_id') = $3
              AND deleted_at IS NULL
              AND (metadata_json->'migration'->>'bh_task_id') IS NOT NULL
            """,
            tenant_id,
            workhub_project_id,
            bh_project_id,
        )
        comments_count = await conn.fetchval(
            """
            SELECT COUNT(tc.id)
            FROM task_comments tc
            JOIN tasks t ON t.id = tc.task_id
            WHERE t.tenant_id = $1
              AND t.project_id = $2
              AND (t.metadata_json->'migration'->>'source') = 'bivrhub'
              AND (t.metadata_json->'migration'->>'bh_project_id') = $3
              AND t.deleted_at IS NULL
              AND tc.deleted_at IS NULL
              AND tc.created_by = 'aix-bh-migrator'
            """,
            tenant_id,
            workhub_project_id,
            bh_project_id,
        )
        knowledge_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM artefacts
            WHERE tenant_id = $1
              AND project_id = $2
              AND artefact_type = 'note'
              AND (metadata_json->'migration'->>'source') = 'bivrhub'
              AND deleted_at IS NULL
            """,
            tenant_id,
            workhub_project_id,
        )
        lifecycle_rows = await conn.fetch(
            """
            SELECT status, COUNT(*)::int AS count
            FROM tasks
            WHERE tenant_id = $1
              AND project_id = $2
              AND (metadata_json->'migration'->>'source') = 'bivrhub'
              AND deleted_at IS NULL
            GROUP BY status
            ORDER BY status
            """,
            tenant_id,
            workhub_project_id,
        )
        spot_missing: list[str] = []
        for bh_task_id in bh_task_ids[:5]:
            exists = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM tasks
                    WHERE tenant_id = $1
                      AND project_id = $2
                      AND (metadata_json->'migration'->>'bh_task_id') = $3
                      AND deleted_at IS NULL
                )
                """,
                tenant_id,
                workhub_project_id,
                bh_task_id,
            )
            if not exists:
                spot_missing.append(bh_task_id)
        lifecycle_map = {str(r["status"]): int(r["count"]) for r in lifecycle_rows}
        invalid = [status for status in lifecycle_map if status not in ALLOWED_LIFECYCLE]
        wh_bh_task_ids = {str(r["bh_task_id"]) for r in distinct_bh_task_ids if r["bh_task_id"]}
        source_bh_task_ids = {str(x) for x in bh_task_ids}
        missing_ids = sorted(source_bh_task_ids - wh_bh_task_ids)
        extra_row_count = int(tasks_count or 0) - len(wh_bh_task_ids)
        return {
            "tasks_count": int(tasks_count or 0),
            "tasks_distinct_bh_ids": len(wh_bh_task_ids),
            "comments_count": int(comments_count or 0),
            "knowledge_count": int(knowledge_count or 0),
            "lifecycle_distribution": lifecycle_map,
            "lifecycle_invalid_statuses": invalid,
            "spot_missing_bh_task_ids": spot_missing,
            "missing_bh_task_ids_count": len(missing_ids),
            "missing_bh_task_ids_sample": missing_ids[:10],
            "extra_duplicate_rows_count": max(0, extra_row_count),
        }
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run migration validation suite: counts, spot parity, lifecycle.")
    parser.add_argument("--bh-config", type=Path, default=DEFAULT_BH_CONFIG, help="BH config path containing token.")
    parser.add_argument("--bh-base-url", default="http://localhost:8310", help="BH API base URL.")
    parser.add_argument("--tenant-id", default="default", help="Tenant id.")
    parser.add_argument("--actor-id", default="aix-migrator", help="Actor id for BH headers.")
    parser.add_argument("--workhub-dsn", default=DEFAULT_DSN, help="WorkHub Postgres DSN.")
    parser.add_argument(
        "--report-path",
        default="C:/dev/hyeve/_runlogs/aix_mig07_validation_suite.json",
        help="Output report path.",
    )
    args = parser.parse_args()

    bh_cfg = _load_bh_config(args.bh_config)
    token = str(bh_cfg.get("token") or "").strip()
    if not token:
        raise RuntimeError("Missing BH token in config; run BH login first.")

    rows: list[dict[str, Any]] = []
    for project_number in range(1, 8):
        base_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Tenant-ID": args.tenant_id,
            "X-Actor-ID": args.actor_id,
        }
        project_payload = _request_json(
            method="GET",
            url=f"{args.bh_base_url}/api/projects/by-number/{project_number}",
            headers=base_headers,
        )
        bh_project = project_payload.get("data") or {}
        bh_project_id = str(bh_project.get("id"))
        bh_name = str(bh_project.get("name") or f"project-{project_number}")
        workhub_project_id = f"bh-migrated-{project_number}-{_slug(bh_name)}"

        project_headers = dict(base_headers)
        project_headers["X-Project-ID"] = bh_project_id
        tasks = _paginate_bh(
            base_url=args.bh_base_url,
            endpoint=f"/api/projects/{bh_project_id}/tasks",
            headers=project_headers,
        )
        knowledge = _paginate_bh(
            base_url=args.bh_base_url,
            endpoint=f"/api/projects/{bh_project_id}/knowledge",
            headers=project_headers,
        )
        comments_total = 0
        for task in tasks:
            task_id = str(task.get("id"))
            comments = _paginate_bh(
                base_url=args.bh_base_url, endpoint=f"/api/tasks/{task_id}/comments", headers=base_headers
            )
            comments_total += len(comments)

        bh_task_ids = [str(task.get("id")) for task in tasks if task.get("id")]
        wh_metrics = asyncio.run(
            _wh_project_metrics(
                dsn=args.workhub_dsn,
                tenant_id=args.tenant_id,
                bh_project_id=bh_project_id,
                workhub_project_id=workhub_project_id,
                bh_task_ids=bh_task_ids,
            )
        )

        row: dict[str, Any] = {
            "bh_project_number": project_number,
            "bh_project_id": bh_project_id,
            "bh_project_name": bh_name,
            "workhub_project_id": workhub_project_id,
            "source": {
                "tasks": len(tasks),
                "comments": comments_total,
                "knowledge": len(knowledge),
            },
            "workhub": wh_metrics,
        }
        source_metrics = cast(dict[str, Any], row["source"])
        workhub_metrics = cast(dict[str, Any], row["workhub"])
        row["parity"] = {
            "tasks_ok": workhub_metrics["missing_bh_task_ids_count"] == 0,
            "comments_ok": source_metrics["comments"] == workhub_metrics["comments_count"],
            "knowledge_ok": source_metrics["knowledge"] == workhub_metrics["knowledge_count"],
            "spot_ok": len(cast(list[str], workhub_metrics["spot_missing_bh_task_ids"])) == 0,
            "lifecycle_ok": len(cast(list[str], workhub_metrics["lifecycle_invalid_statuses"])) == 0,
        }
        rows.append(row)

    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "task": "AIX-MIG-07",
        "suite": "counts + spot parity + lifecycle",
        "rows": rows,
        "summary": {
            "projects": len(rows),
            "all_tasks_ok": all(r["parity"]["tasks_ok"] for r in rows),
            "all_comments_ok": all(r["parity"]["comments_ok"] for r in rows),
            "all_knowledge_ok": all(r["parity"]["knowledge_ok"] for r in rows),
            "all_spot_ok": all(r["parity"]["spot_ok"] for r in rows),
            "all_lifecycle_ok": all(r["parity"]["lifecycle_ok"] for r in rows),
        },
    }
    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), "summary": report["summary"]}, indent=2))


if __name__ == "__main__":
    main()
