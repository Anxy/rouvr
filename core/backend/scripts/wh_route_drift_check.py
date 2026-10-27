from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REQUIRED_ROUTES: dict[str, set[str]] = {
    "/api/v1/capabilities": {"get"},
    "/api/v1/projects/resolve": {"get"},
    "/api/v1/projects/{project_id}/knowledge": {"get", "post"},
    "/api/v1/projects/{project_id}/knowledge/search": {"get"},
    "/api/v1/projects/{project_id}/knowledge/summary": {"get"},
    "/api/v1/projects/{project_id}/knowledge/{knowledge_id}": {"get", "patch"},
}


def _fetch_openapi(base_url: str) -> dict:
    req = Request(url=f"{base_url.rstrip('/')}/openapi.json", method="GET")
    with urlopen(req, timeout=5.0) as response:  # noqa: S310 - local operator diagnostics
        if int(getattr(response, "status", 0) or 0) != 200:
            raise RuntimeError(f"OpenAPI fetch failed with status={getattr(response, 'status', None)}")
        return json.loads(response.read().decode("utf-8"))


def run_check(base_url: str) -> dict:
    payload = _fetch_openapi(base_url=base_url)
    paths = payload.get("paths", {}) if isinstance(payload, dict) else {}
    missing: list[dict[str, object]] = []
    matched: list[dict[str, object]] = []

    for path, required_methods in REQUIRED_ROUTES.items():
        methods = set()
        if path in paths and isinstance(paths[path], dict):
            methods = {str(k).lower() for k in paths[path].keys()}
        missing_methods = sorted(required_methods - methods)
        if missing_methods:
            missing.append({"path": path, "missing_methods": missing_methods})
        else:
            matched.append({"path": path, "methods": sorted(required_methods)})

    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "ok": len(missing) == 0,
        "openapi_version": payload.get("openapi"),
        "required_count": len(REQUIRED_ROUTES),
        "matched": matched,
        "missing": missing,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect WorkHub route/method drift via live OpenAPI.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8221", help="WorkHub base URL.")
    parser.add_argument(
        "--report-path",
        default="C:/dev/hyeve/_runlogs/wh_route_drift_check.json",
        help="Path to write JSON report.",
    )
    args = parser.parse_args()

    try:
        result = run_check(base_url=args.base_url)
    except HTTPError as exc:
        result = {
            "timestamp": datetime.now(UTC).isoformat(),
            "base_url": args.base_url,
            "ok": False,
            "error": f"HTTP {exc.code}",
        }
    except Exception as exc:  # pragma: no cover - defensive error path
        result = {
            "timestamp": datetime.now(UTC).isoformat(),
            "base_url": args.base_url,
            "ok": False,
            "error": str(exc),
        }

    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
