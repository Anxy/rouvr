from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://82.112.234.42:8221/api/v1"
DEFAULT_PROJECT_ID = "bh-migrated-1-hyeve"


def _http_json(
    method: str, url: str, token: str, body: dict[str, Any] | None = None, timeout_sec: int = 30
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=True).encode("utf-8")
    req = Request(url, method=method.upper(), headers=headers, data=data)
    with urlopen(req, timeout=timeout_sec) as resp:
        payload = resp.read().decode("utf-8")
        return json.loads(payload) if payload else {}


def _all_items(base_url: str, token: str, project_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    limit = 200
    while True:
        q = urlencode({"limit": limit, "offset": offset})
        data = _http_json("GET", f"{base_url.rstrip('/')}/projects/{project_id}/knowledge?{q}", token).get("data") or {}
        page = data.get("items") or []
        items.extend([x for x in page if isinstance(x, dict)])
        if not data.get("has_more"):
            break
        offset += limit
    return items


def run_normalize(*, base_url: str, token: str, project_id: str, dry_run: bool) -> dict[str, Any]:
    all_items = _all_items(base_url, token, project_id)
    run_id = datetime.now(UTC).strftime("k8-%Y%m%dT%H%M%SZ")
    targets: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for item in all_items:
        meta = dict(item.get("metadata_json") or {})
        dedupe = meta.get("dedupe")
        if not isinstance(dedupe, dict) or dedupe.get("status") != "archived":
            continue
        archive_meta = meta.get("archive")
        retrieval = meta.get("retrieval")
        changed = False

        if not isinstance(archive_meta, dict):
            archive_meta = {}
            changed = True
        if archive_meta.get("status") != "archived":
            archive_meta["status"] = "archived"
            changed = True
        if archive_meta.get("reason") is None:
            archive_meta["reason"] = "same_content_duplicate_across_streams"
            changed = True
        if archive_meta.get("source") is None:
            archive_meta["source"] = "dedupe"
            changed = True

        if not isinstance(retrieval, dict):
            retrieval = {}
            changed = True
        if retrieval.get("view") is None:
            retrieval["view"] = "aix_knowledge"
            changed = True
        if retrieval.get("scope") is None:
            retrieval["scope"] = "shared_common"
            changed = True
        if retrieval.get("project_id") is None:
            retrieval["project_id"] = project_id
            changed = True
        if retrieval.get("include") is not False:
            retrieval["include"] = False
            changed = True
        if retrieval.get("rank_weight") != 0:
            retrieval["rank_weight"] = 0
            changed = True

        if not changed:
            continue

        meta["archive"] = archive_meta
        meta["retrieval"] = retrieval
        meta["normalization"] = {
            "source": "wh_knowledge_archived_shape_normalize.py",
            "run_id": run_id,
            "updated_at": datetime.now(UTC).isoformat(),
            "reason": "uniform_archived_metadata_shape",
        }

        record = {"id": item.get("id"), "title": item.get("title") or item.get("label")}
        if dry_run:
            targets.append({**record, "dry_run": True})
            continue

        try:
            patch_url = f"{base_url.rstrip('/')}/projects/{project_id}/knowledge/{item.get('id')}"
            _http_json("PATCH", patch_url, token, body={"project_id": project_id, "metadata_json": meta})
            targets.append({**record, "dry_run": False})
        except Exception as exc:  # pragma: no cover
            failed.append({**record, "error": str(exc)})

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "project_id": project_id,
        "dry_run": dry_run,
        "totals": {
            "normalized_or_planned": len(targets),
            "failed": len(failed),
        },
        "normalized_or_planned": targets,
        "failed": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize metadata shape for archived dedupe knowledge records.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token", default=os.getenv("HYEVE_OPERATOR_TOKEN", ""))
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    token = str(args.token or "").strip()
    if not token:
        raise SystemExit("Missing token. Provide --token or HYEVE_OPERATOR_TOKEN.")

    report = run_normalize(
        base_url=args.base_url,
        token=token,
        project_id=args.project_id,
        dry_run=bool(args.dry_run),
    )
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if report["totals"]["failed"] > 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
