from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://82.112.234.42:8221/api/v1"
DEFAULT_DEDUPE_REPORT = "C:/dev/hyeve/_runlogs/wh_knowledge_dedupe_map_20260228.json"


def _http_json(
    method: str, url: str, token: str, body: dict[str, Any] | None = None, timeout_sec: int = 30
) -> dict[str, Any]:
    data = None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=True).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, method=method.upper(), headers=headers, data=data)
    with urlopen(req, timeout=timeout_sec) as resp:
        payload = resp.read().decode("utf-8")
        return json.loads(payload) if payload else {}


def run_apply(*, base_url: str, token: str, dedupe_report_path: Path, dry_run: bool) -> dict[str, Any]:
    dedupe = json.loads(dedupe_report_path.read_text(encoding="utf-8"))
    overlaps = dedupe.get("overlaps") or []
    run_id = datetime.now(UTC).strftime("k6-%Y%m%dT%H%M%SZ")

    patched: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for row in overlaps:
        if row.get("recommendation") != "archive_candidate_secondary":
            continue
        secondary = row.get("secondary") or {}
        project_id = str(secondary.get("project_id") or "")
        knowledge_id = str(secondary.get("id") or "")
        if not project_id or not knowledge_id:
            skipped.append({"title": row.get("title"), "reason": "missing_secondary_identity"})
            continue

        if dry_run:
            patched.append(
                {
                    "title": row.get("title"),
                    "project_id": project_id,
                    "knowledge_id": knowledge_id,
                    "dry_run": True,
                }
            )
            continue

        try:
            get_url = f"{base_url.rstrip('/')}/projects/{project_id}/knowledge/{knowledge_id}"
            current = _http_json("GET", get_url, token).get("data") or {}
            metadata = dict(current.get("metadata_json") or {})
            metadata["knowledge"] = True
            metadata["dedupe"] = {
                "status": "archive_candidate",
                "reason": "same_content_duplicate_across_streams",
                "canonical_project_id": str((row.get("primary") or {}).get("project_id") or ""),
                "canonical_knowledge_id": str((row.get("primary") or {}).get("id") or ""),
                "run_id": run_id,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            knowledge_meta = metadata.get("knowledge")
            if isinstance(knowledge_meta, dict):
                knowledge_meta["status"] = "archived_candidate"
                metadata["knowledge"] = knowledge_meta

            patch_url = f"{base_url.rstrip('/')}/projects/{project_id}/knowledge/{knowledge_id}"
            patch_body = {"project_id": project_id, "metadata_json": metadata}
            _http_json("PATCH", patch_url, token, body=patch_body)

            patched.append(
                {
                    "title": row.get("title"),
                    "project_id": project_id,
                    "knowledge_id": knowledge_id,
                    "canonical_project_id": str((row.get("primary") or {}).get("project_id") or ""),
                    "canonical_knowledge_id": str((row.get("primary") or {}).get("id") or ""),
                }
            )
        except Exception as exc:  # pragma: no cover
            failed.append(
                {
                    "title": row.get("title"),
                    "project_id": project_id,
                    "knowledge_id": knowledge_id,
                    "error": str(exc),
                }
            )

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "dry_run": dry_run,
        "dedupe_report_path": str(dedupe_report_path),
        "totals": {
            "patched_or_planned": len(patched),
            "skipped": len(skipped),
            "failed": len(failed),
        },
        "patched_or_planned": patched,
        "skipped": skipped,
        "failed": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply dedupe archive-candidate metadata markers for secondary knowledge."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token", default=os.getenv("HYEVE_OPERATOR_TOKEN", ""))
    parser.add_argument("--dedupe-report", default=DEFAULT_DEDUPE_REPORT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    token = str(args.token or "").strip()
    if not token:
        raise SystemExit("Missing token. Provide --token or HYEVE_OPERATOR_TOKEN.")
    path = Path(args.dedupe_report)
    if not path.exists():
        raise SystemExit(f"Dedupe report not found: {path}")

    report = run_apply(
        base_url=args.base_url,
        token=token,
        dedupe_report_path=path,
        dry_run=bool(args.dry_run),
    )
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["totals"]["failed"] > 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
