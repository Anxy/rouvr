from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://82.112.234.42:8221/api/v1"
DEFAULT_PRIMARY_PROJECT = "bh-migrated-7-aix"
DEFAULT_SECONDARY_PROJECT = "bh-migrated-1-hyeve"


def _http_json(url: str, token: str, timeout_sec: int = 30) -> dict[str, Any]:
    req = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urlopen(req, timeout=timeout_sec) as resp:
        payload = resp.read().decode("utf-8")
        return json.loads(payload) if payload else {}


def _all_items(base_url: str, token: str, project_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    limit = 200
    while True:
        q = urlencode({"limit": limit, "offset": offset})
        data = _http_json(f"{base_url.rstrip('/')}/projects/{project_id}/knowledge?{q}", token).get("data") or {}
        page = data.get("items") or []
        items.extend([x for x in page if isinstance(x, dict)])
        if not data.get("has_more"):
            break
        offset += limit
    return items


def _norm_title(v: str) -> str:
    return " ".join((v or "").strip().lower().split())


def _content_fingerprint(item: dict[str, Any]) -> str:
    content = str(item.get("content") or "").strip()
    return content[:2000]


def run_map(base_url: str, token: str, primary_project: str, secondary_project: str) -> dict[str, Any]:
    p_items = _all_items(base_url, token, primary_project)
    s_items = _all_items(base_url, token, secondary_project)
    by_title_primary = {_norm_title(str(i.get("title") or i.get("label") or "")): i for i in p_items}
    by_title_secondary = {_norm_title(str(i.get("title") or i.get("label") or "")): i for i in s_items}

    overlaps: list[dict[str, Any]] = []
    for t in sorted(set(by_title_primary.keys()).intersection(set(by_title_secondary.keys()))):
        p = by_title_primary[t]
        s = by_title_secondary[t]
        same_content = _content_fingerprint(p) == _content_fingerprint(s)
        recommendation = "archive_candidate_secondary" if same_content else "manual_merge_required"
        overlaps.append(
            {
                "normalized_title": t,
                "title": p.get("title") or p.get("label"),
                "primary": {"project_id": primary_project, "id": p.get("id"), "uri": p.get("uri")},
                "secondary": {"project_id": secondary_project, "id": s.get("id"), "uri": s.get("uri")},
                "same_content": same_content,
                "recommendation": recommendation,
            }
        )

    primary_only = [
        {"title": i.get("title") or i.get("label"), "id": i.get("id"), "uri": i.get("uri")}
        for t, i in by_title_primary.items()
        if t not in by_title_secondary
    ]
    secondary_only = [
        {"title": i.get("title") or i.get("label"), "id": i.get("id"), "uri": i.get("uri")}
        for t, i in by_title_secondary.items()
        if t not in by_title_primary
    ]

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "primary_project": primary_project,
        "secondary_project": secondary_project,
        "totals": {
            "primary_total": len(p_items),
            "secondary_total": len(s_items),
            "title_overlap": len(overlaps),
            "same_content_overlap": len([x for x in overlaps if x["same_content"]]),
            "manual_merge_required": len([x for x in overlaps if not x["same_content"]]),
            "primary_only": len(primary_only),
            "secondary_only": len(secondary_only),
        },
        "overlaps": overlaps,
        "primary_only": primary_only,
        "secondary_only": secondary_only,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build WH knowledge dedupe map across two projects.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token", default=os.getenv("HYEVE_OPERATOR_TOKEN", ""))
    parser.add_argument("--primary-project", default=DEFAULT_PRIMARY_PROJECT)
    parser.add_argument("--secondary-project", default=DEFAULT_SECONDARY_PROJECT)
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    token = str(args.token or "").strip()
    if not token:
        raise SystemExit("Missing token. Provide --token or HYEVE_OPERATOR_TOKEN.")

    report = run_map(
        base_url=args.base_url,
        token=token,
        primary_project=args.primary_project,
        secondary_project=args.secondary_project,
    )
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=True)
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
