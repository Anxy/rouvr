from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(slots=True)
class Check:
    query: str
    expect_uri: str
    min_hits: int = 1


DEFAULT_BASE_URL = "http://82.112.234.42:8221/api/v1"
DEFAULT_PROJECT_ID = "bh-migrated-7-aix"
DEFAULT_CHECKS = [
    Check(query="vps-first", expect_uri="wh://operator/bootstrap/vps-first"),
    Check(query="idempotency-key", expect_uri="wh://operator/contracts/workhub-headers-idempotency"),
    Check(query="bh-migrated-7-aix", expect_uri="wh://operator/reference/project-id-map"),
    Check(query="SshHost", expect_uri="wh://operator/bootstrap/script-routing"),
    Check(query="q vs query", expect_uri="wh://operator/contracts/knowledge-search-q"),
]


def _http_json(url: str, token: str, timeout_sec: int = 20) -> dict[str, Any]:
    req = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urlopen(req, timeout=timeout_sec) as resp:
        payload = resp.read().decode("utf-8")
        return json.loads(payload) if payload else {}


def run_smoke(*, base_url: str, project_id: str, token: str) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    failed = 0

    for c in DEFAULT_CHECKS:
        params = urlencode({"q": c.query, "limit": 20, "offset": 0})
        url = f"{base_url.rstrip('/')}/projects/{project_id}/knowledge/search?{params}"
        data = _http_json(url, token).get("data") or {}
        items = data.get("items") or []
        total = int(data.get("total") or 0)

        hit_by_uri = False
        for it in items:
            if isinstance(it, dict) and str(it.get("uri") or "").strip() == c.expect_uri:
                hit_by_uri = True
                break

        passed = total >= c.min_hits and hit_by_uri
        if not passed:
            failed += 1
        results.append(
            {
                "query": c.query,
                "expect_uri": c.expect_uri,
                "total": total,
                "returned": len(items),
                "hit_by_uri": hit_by_uri,
                "passed": passed,
                "top_titles": [it.get("title") for it in items[:5] if isinstance(it, dict)],
            }
        )

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "project_id": project_id,
        "checks_total": len(DEFAULT_CHECKS),
        "checks_failed": failed,
        "passed": failed == 0,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="WorkHub knowledge search smoke checks.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--token", default=os.getenv("HYEVE_OPERATOR_TOKEN", ""))
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    token = str(args.token or "").strip()
    if not token:
        raise SystemExit("Missing token. Provide --token or HYEVE_OPERATOR_TOKEN.")

    report = run_smoke(base_url=args.base_url, project_id=args.project_id, token=token)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=True)
    print(json.dumps(report, indent=2, ensure_ascii=True))
    if not report["passed"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
