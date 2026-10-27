from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://82.112.234.42:8221/api/v1"
DEFAULT_PROJECT_IDS = [
    "bh-migrated-7-aix",
    "bh-migrated-1-hyeve",
]


@dataclass(slots=True)
class ProjectResult:
    project_id: str
    scanned: int = 0
    candidates: int = 0
    patched: int = 0
    failed: int = 0


def _http_json(
    method: str,
    url: str,
    *,
    token: str,
    body: dict[str, Any] | None = None,
    timeout_sec: int = 20,
) -> dict[str, Any]:
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=True).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, method=method.upper(), headers=headers)
    with urlopen(req, timeout=timeout_sec) as resp:
        payload = resp.read().decode("utf-8")
        return json.loads(payload) if payload else {}


def _list_knowledge_page(
    *,
    base_url: str,
    token: str,
    project_id: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    query = urlencode({"limit": limit, "offset": offset})
    url = f"{base_url.rstrip('/')}/projects/{project_id}/knowledge?{query}"
    return _http_json("GET", url, token=token)


def _patch_knowledge(
    *,
    base_url: str,
    token: str,
    project_id: str,
    knowledge_id: str,
    content: str,
    run_id: str,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/projects/{project_id}/knowledge/{knowledge_id}"
    payload = {
        "content": content,
        "metadata_json": {
            "backfill": {
                "source": "custom_data.content",
                "run_id": run_id,
                "updated_at": datetime.now(UTC).isoformat(),
                "tool": "wh_backfill_knowledge_content.py",
            }
        },
    }
    return _http_json("PATCH", url, token=token, body=payload)


def _iter_project_items(*, base_url: str, token: str, project_id: str, page_size: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        payload = _list_knowledge_page(
            base_url=base_url,
            token=token,
            project_id=project_id,
            limit=page_size,
            offset=offset,
        )
        data = payload.get("data") or {}
        page_items = data.get("items") or []
        if not isinstance(page_items, list):
            break
        items.extend([i for i in page_items if isinstance(i, dict)])
        if not data.get("has_more"):
            break
        offset += page_size
    return items


def _is_empty_text(value: Any) -> bool:
    return not isinstance(value, str) or not value.strip()


def run_backfill(
    *,
    base_url: str,
    token: str,
    project_ids: list[str],
    page_size: int,
    dry_run: bool,
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    started = datetime.now(UTC).isoformat()
    results: list[ProjectResult] = []
    failures: list[dict[str, Any]] = []

    for project_id in project_ids:
        r = ProjectResult(project_id=project_id)
        results.append(r)
        items = _iter_project_items(base_url=base_url, token=token, project_id=project_id, page_size=page_size)
        r.scanned = len(items)

        for item in items:
            content = item.get("content")
            custom_data = item.get("custom_data") or {}
            custom_content = custom_data.get("content") if isinstance(custom_data, dict) else None
            if not _is_empty_text(content):
                continue
            if _is_empty_text(custom_content):
                continue

            r.candidates += 1
            if dry_run:
                continue

            knowledge_id = str(item.get("id") or "")
            if not knowledge_id:
                r.failed += 1
                failures.append(
                    {
                        "project_id": project_id,
                        "knowledge_id": None,
                        "title": item.get("title") or item.get("label"),
                        "error": "missing knowledge id",
                    }
                )
                continue
            try:
                _patch_knowledge(
                    base_url=base_url,
                    token=token,
                    project_id=project_id,
                    knowledge_id=knowledge_id,
                    content=custom_content.strip(),
                    run_id=run_id,
                )
                r.patched += 1
            except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                r.failed += 1
                failures.append(
                    {
                        "project_id": project_id,
                        "knowledge_id": knowledge_id,
                        "title": item.get("title") or item.get("label"),
                        "error": str(exc),
                    }
                )

    totals = {
        "projects": len(results),
        "scanned": sum(x.scanned for x in results),
        "candidates": sum(x.candidates for x in results),
        "patched": sum(x.patched for x in results),
        "failed": sum(x.failed for x in results),
    }
    return {
        "run_id": run_id,
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "dry_run": dry_run,
        "project_results": [asdict(r) for r in results],
        "totals": totals,
        "failures": failures,
    }


def _parse_project_ids(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill WorkHub knowledge content by promoting custom_data.content into canonical content."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="WorkHub API base URL (include /api/v1).")
    parser.add_argument("--token", default=os.getenv("HYEVE_OPERATOR_TOKEN", ""), help="Bearer token.")
    parser.add_argument(
        "--project-ids-csv",
        default=",".join(DEFAULT_PROJECT_IDS),
        help="Comma-separated WorkHub project IDs.",
    )
    parser.add_argument("--page-size", type=int, default=100, help="Pagination size for knowledge listing.")
    parser.add_argument("--dry-run", action="store_true", help="Compute candidates only, do not patch.")
    parser.add_argument("--report", default="", help="Optional report output path.")
    args = parser.parse_args()

    token = str(args.token or "").strip()
    if not token:
        raise SystemExit("Missing token. Set --token or HYEVE_OPERATOR_TOKEN.")
    project_ids = _parse_project_ids(args.project_ids_csv)
    if not project_ids:
        raise SystemExit("No project IDs provided.")

    try:
        report = run_backfill(
            base_url=args.base_url,
            token=token,
            project_ids=project_ids,
            page_size=max(1, int(args.page_size)),
            dry_run=bool(args.dry_run),
        )
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise SystemExit(f"Backfill failed: {exc}") from exc

    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=True)

    print(json.dumps(report, indent=2, ensure_ascii=True))
    if report["totals"]["failed"] > 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
