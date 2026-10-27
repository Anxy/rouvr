from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]

DEFAULT_DSN = "postgresql://postgres:simple@localhost:5432/hyeve_workhub_service"
DEFAULT_TENANT_ID = "default"
DEFAULT_PROJECT_ID = "bh-migrated-7-aix"
DEFAULT_ACTOR = "local-operator"
DEFAULT_REPORT_PATH = Path("C:/dev/hyeve/_runlogs/aix_mig17_knowledge_normalization.json")
PROJECT_SPECIFIC_TOKENS = ("aix", "workhub", "opsbus", "codeintel", "orchestration", "cutover")


@dataclass(slots=True)
class NormalizationResult:
    artefact_id: str
    label: str
    previous_scope: str | None
    new_scope: str
    reason: str
    changed: bool


def _parse_metadata(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _infer_scope(*, label: str | None, summary: str | None) -> tuple[str, str]:
    text = f"{label or ''} {summary or ''}".lower()
    token_hits = [token for token in PROJECT_SPECIFIC_TOKENS if token in text]
    if token_hits:
        return "project_specific", f"token_match:{','.join(token_hits)}"
    return "shared_common", "no_project_specific_tokens"


def _normalized_metadata(
    *,
    metadata: dict[str, Any],
    scope: str,
    reason: str,
    actor: str,
    timestamp: str,
    project_id: str,
) -> dict[str, Any]:
    next_meta = dict(metadata)
    next_meta["normalization"] = {
        "version": "aix-mig17-v1",
        "scope": scope,
        "reason": reason,
        "normalized_at": timestamp,
        "normalized_by": actor,
    }
    next_meta["retrieval"] = {
        "view": "aix_knowledge",
        "scope": scope,
        "project_id": project_id,
        "include": True,
        "rank_weight": 100 if scope == "project_specific" else 50,
    }
    return next_meta


async def run_normalization(
    *,
    dsn: str,
    tenant_id: str,
    project_id: str,
    actor: str,
    apply: bool,
) -> dict[str, Any]:
    conn = await asyncpg.connect(dsn)
    timestamp = datetime.now(UTC).isoformat()
    results: list[NormalizationResult] = []
    try:
        rows = await conn.fetch(
            """
            SELECT id, label, metadata_json
            FROM artefacts
            WHERE tenant_id = $1
              AND project_id = $2
              AND artefact_type = 'note'
              AND deleted_at IS NULL
            ORDER BY created_at ASC
            """,
            tenant_id,
            project_id,
        )

        for row in rows:
            artefact_id = str(row["id"])
            label = str(row["label"] or "")
            metadata = _parse_metadata(row["metadata_json"])
            prev_scope = (
                metadata.get("normalization", {}).get("scope")
                if isinstance(metadata.get("normalization"), dict)
                else None
            )
            summary = None
            knowledge = metadata.get("knowledge")
            if isinstance(knowledge, dict):
                summary_value = knowledge.get("summary")
                summary = str(summary_value) if summary_value is not None else None
            scope, reason = _infer_scope(label=label, summary=summary)

            next_meta = _normalized_metadata(
                metadata=metadata,
                scope=scope,
                reason=reason,
                actor=actor,
                timestamp=timestamp,
                project_id=project_id,
            )
            changed = json.dumps(metadata, sort_keys=True) != json.dumps(next_meta, sort_keys=True)
            if apply and changed:
                await conn.execute(
                    """
                    UPDATE artefacts
                    SET metadata_json = $1::jsonb, updated_at = now(), updated_by = $2
                    WHERE id = $3 AND tenant_id = $4
                    """,
                    json.dumps(next_meta, separators=(",", ":")),
                    actor,
                    artefact_id,
                    tenant_id,
                )

            results.append(
                NormalizationResult(
                    artefact_id=artefact_id,
                    label=label,
                    previous_scope=prev_scope,
                    new_scope=scope,
                    reason=reason,
                    changed=changed,
                )
            )

        project_specific = [r for r in results if r.new_scope == "project_specific"]
        shared_common = [r for r in results if r.new_scope == "shared_common"]
        changed_results = [r for r in results if r.changed]
        return {
            "timestamp": timestamp,
            "mode": "apply" if apply else "dry_run",
            "tenant_id": tenant_id,
            "project_id": project_id,
            "totals": {
                "notes_considered": len(results),
                "project_specific": len(project_specific),
                "shared_common": len(shared_common),
                "metadata_updated": len(changed_results) if apply else 0,
            },
            "project_specific_labels": [r.label for r in project_specific],
            "shared_common_labels": [r.label for r in shared_common],
            "results": [
                {
                    "artefact_id": r.artefact_id,
                    "label": r.label,
                    "previous_scope": r.previous_scope,
                    "new_scope": r.new_scope,
                    "reason": r.reason,
                    "changed": r.changed,
                }
                for r in results
            ],
        }
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize migrated WorkHub note artefacts into shared_common vs project_specific scopes."
    )
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="Postgres DSN for WorkHub DB.")
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID, help="Tenant ID to normalize.")
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID, help="Project ID to normalize.")
    parser.add_argument("--actor", default=DEFAULT_ACTOR, help="Actor recorded in updated_by.")
    parser.add_argument("--report", default=str(DEFAULT_REPORT_PATH), help="Output report JSON path.")
    parser.add_argument("--apply", action="store_true", help="Apply updates. Without this flag runs as dry-run.")
    args = parser.parse_args()

    report = asyncio.run(
        run_normalization(
            dsn=args.dsn,
            tenant_id=args.tenant_id,
            project_id=args.project_id,
            actor=args.actor,
            apply=args.apply,
        )
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), "totals": report["totals"]}, indent=2))


if __name__ == "__main__":
    main()
