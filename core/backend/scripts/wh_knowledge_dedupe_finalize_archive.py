from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://82.112.234.42:8221/api/v1"
DEFAULT_APPLY_REPORT = "C:/dev/hyeve/_runlogs/wh_knowledge_dedupe_apply_20260228.json"


def _http_json(
    method: str, url: str, token: str, body: dict[str, Any] | None = None, timeout_sec: int = 30
) -> dict[str, Any]:
    data = None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=True).encode("utf-8")
    req = Request(url, method=method.upper(), headers=headers, data=data)
    with urlopen(req, timeout=timeout_sec) as resp:
        payload = resp.read().decode("utf-8")
        return json.loads(payload) if payload else {}


def run_finalize(*, base_url: str, token: str, apply_report_path: Path, dry_run: bool) -> dict[str, Any]:
    apply_report = json.loads(apply_report_path.read_text(encoding="utf-8"))
    rows = apply_report.get("patched_or_planned") or []
    run_id = datetime.now(UTC).strftime("k7-%Y%m%dT%H%M%SZ")

    finalized: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for row in rows:
        project_id = str(row.get("project_id") or "")
        knowledge_id = str(row.get("knowledge_id") or "")
        if not project_id or not knowledge_id:
            skipped.append({"title": row.get("title"), "reason": "missing_identity"})
            continue

        try:
            get_url = f"{base_url.rstrip('/')}/projects/{project_id}/knowledge/{knowledge_id}"
            current = _http_json("GET", get_url, token).get("data") or {}
            metadata = dict(current.get("metadata_json") or {})
            dedupe = dict(metadata.get("dedupe") or {})
            if dedupe.get("status") not in ("archive_candidate", "archived"):
                skipped.append(
                    {"title": row.get("title"), "knowledge_id": knowledge_id, "reason": "unexpected_dedupe_status"}
                )
                continue

            dedupe["status"] = "archived"
            dedupe["finalized_by"] = "wh_knowledge_dedupe_finalize_archive.py"
            dedupe["finalized_run_id"] = run_id
            dedupe["finalized_at"] = datetime.now(UTC).isoformat()
            metadata["dedupe"] = dedupe

            knowledge_meta = metadata.get("knowledge")
            if isinstance(knowledge_meta, dict):
                knowledge_meta["status"] = "archived"
                metadata["knowledge"] = knowledge_meta

            retrieval = metadata.get("retrieval")
            if isinstance(retrieval, dict):
                retrieval["include"] = False
                metadata["retrieval"] = retrieval

            if not dry_run:
                patch_url = f"{base_url.rstrip('/')}/projects/{project_id}/knowledge/{knowledge_id}"
                patch_body = {"project_id": project_id, "metadata_json": metadata}
                _http_json("PATCH", patch_url, token, body=patch_body)

            finalized.append(
                {
                    "title": row.get("title"),
                    "project_id": project_id,
                    "knowledge_id": knowledge_id,
                    "canonical_project_id": row.get("canonical_project_id"),
                    "canonical_knowledge_id": row.get("canonical_knowledge_id"),
                    "dry_run": dry_run,
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
        "apply_report_path": str(apply_report_path),
        "totals": {
            "finalized_or_planned": len(finalized),
            "skipped": len(skipped),
            "failed": len(failed),
        },
        "finalized_or_planned": finalized,
        "skipped": skipped,
        "failed": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Finalize dedupe archive state in metadata for secondary knowledge notes."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token", default=os.getenv("HYEVE_OPERATOR_TOKEN", ""))
    parser.add_argument("--apply-report", default=DEFAULT_APPLY_REPORT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    token = str(args.token or "").strip()
    if not token:
        raise SystemExit("Missing token. Provide --token or HYEVE_OPERATOR_TOKEN.")
    path = Path(args.apply_report)
    if not path.exists():
        raise SystemExit(f"Apply report not found: {path}")

    report = run_finalize(
        base_url=args.base_url,
        token=token,
        apply_report_path=path,
        dry_run=bool(args.dry_run),
    )
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["totals"]["failed"] > 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
