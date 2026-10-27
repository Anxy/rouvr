from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://82.112.234.42:8221/api/v1"
DEFAULT_PROJECT_IDS = ["bh-migrated-7-aix"]
DEFAULT_REQUIRED_METADATA_KEYS = [
    "knowledge",
    "source",
    "source_task_number",
    "source_docs",
    "last_verified_at",
]


@dataclass(slots=True)
class ProjectSummary:
    project_id: str
    total: int
    issues: int
    missing_uri: int
    missing_kind: int
    missing_tags: int
    missing_content: int
    missing_metadata_keys: int


def _http_json(url: str, token: str, timeout_sec: int = 30) -> dict[str, Any]:
    req = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urlopen(req, timeout=timeout_sec) as resp:
        payload = resp.read().decode("utf-8")
        return json.loads(payload) if payload else {}


def _iter_knowledge(*, base_url: str, token: str, project_id: str, limit: int = 200) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = urlencode({"limit": limit, "offset": offset})
        url = f"{base_url.rstrip('/')}/projects/{project_id}/knowledge?{query}"
        payload = _http_json(url, token)
        data = payload.get("data") or {}
        page = data.get("items") or []
        if not isinstance(page, list):
            break
        items.extend([x for x in page if isinstance(x, dict)])
        if not data.get("has_more"):
            break
        offset += limit
    return items


def _is_blank(v: Any) -> bool:
    return not isinstance(v, str) or not v.strip()


def _lint_item(item: dict[str, Any], required_metadata_keys: list[str]) -> list[str]:
    issues: list[str] = []
    if _is_blank(item.get("uri")):
        issues.append("missing_uri")
    if _is_blank(item.get("kind")):
        issues.append("missing_kind")
    tags = item.get("tags")
    if not isinstance(tags, list) or len(tags) < 3:
        issues.append("missing_tags_min3")
    if _is_blank(item.get("content")):
        issues.append("missing_content")
    meta = item.get("metadata_json")
    if not isinstance(meta, dict):
        issues.append("missing_metadata_json")
        return issues
    for key in required_metadata_keys:
        if key not in meta:
            issues.append(f"missing_metadata:{key}")
            continue
        if key == "source_docs":
            if not isinstance(meta.get(key), list) or not meta.get(key):
                issues.append("invalid_metadata:source_docs")
        elif key == "knowledge":
            if meta.get(key) is not True:
                issues.append("invalid_metadata:knowledge")
        elif key == "last_verified_at":
            if _is_blank(meta.get(key)):
                issues.append("invalid_metadata:last_verified_at")
    return issues


def run_lint(
    *,
    base_url: str,
    token: str,
    project_ids: list[str],
    required_metadata_keys: list[str],
    uri_prefix: str | None = None,
) -> dict[str, Any]:
    summaries: list[ProjectSummary] = []
    findings: list[dict[str, Any]] = []

    for project_id in project_ids:
        items = _iter_knowledge(base_url=base_url, token=token, project_id=project_id)
        if uri_prefix:
            items = [it for it in items if str(it.get("uri") or "").startswith(uri_prefix)]
        missing_uri = 0
        missing_kind = 0
        missing_tags = 0
        missing_content = 0
        missing_metadata_keys = 0
        total_issues = 0
        for item in items:
            issues = _lint_item(item, required_metadata_keys)
            if not issues:
                continue
            total_issues += len(issues)
            missing_uri += int("missing_uri" in issues)
            missing_kind += int("missing_kind" in issues)
            missing_tags += int("missing_tags_min3" in issues)
            missing_content += int("missing_content" in issues)
            missing_metadata_keys += len([x for x in issues if x.startswith("missing_metadata:")])
            findings.append(
                {
                    "project_id": project_id,
                    "id": item.get("id"),
                    "title": item.get("title") or item.get("label"),
                    "uri": item.get("uri"),
                    "issues": issues,
                }
            )
        summaries.append(
            ProjectSummary(
                project_id=project_id,
                total=len(items),
                issues=total_issues,
                missing_uri=missing_uri,
                missing_kind=missing_kind,
                missing_tags=missing_tags,
                missing_content=missing_content,
                missing_metadata_keys=missing_metadata_keys,
            )
        )

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "projects": [asdict(s) for s in summaries],
        "total_findings": len(findings),
        "findings": findings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Lint WH knowledge metadata quality.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token", default=os.getenv("HYEVE_OPERATOR_TOKEN", ""))
    parser.add_argument("--project-ids-csv", default=",".join(DEFAULT_PROJECT_IDS))
    parser.add_argument("--uri-prefix", default="", help="Optional URI prefix scope (example: wh://operator/).")
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    token = str(args.token or "").strip()
    if not token:
        raise SystemExit("Missing token. Provide --token or HYEVE_OPERATOR_TOKEN.")
    project_ids = [x.strip() for x in str(args.project_ids_csv).split(",") if x.strip()]
    if not project_ids:
        raise SystemExit("No project IDs provided.")

    report = run_lint(
        base_url=args.base_url,
        token=token,
        project_ids=project_ids,
        required_metadata_keys=DEFAULT_REQUIRED_METADATA_KEYS,
        uri_prefix=(args.uri_prefix.strip() or None),
    )
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=True)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    if report["total_findings"] > 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
