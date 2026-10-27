from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe WorkHub degraded-mode health contract.")
    parser.add_argument("--base-url", default="http://localhost:8221", help="WorkHub base URL.")
    parser.add_argument(
        "--report-path",
        default="C:/dev/hyeve/_runlogs/aix_mig06_health_contract_probe.json",
        help="Output report path.",
    )
    args = parser.parse_args()

    payload = _get_json(f"{args.base_url}/health")
    required = ["status", "service", "degraded_mode", "components"]
    missing = [key for key in required if key not in payload]
    components = payload.get("components", {}) if isinstance(payload.get("components"), dict) else {}
    redis_state = components.get("redis")
    api_state = components.get("api")
    pass_contract = (
        len(missing) == 0
        and payload.get("service") == "hyeve_workhub_service"
        and payload.get("status") in {"healthy", "degraded"}
        and isinstance(payload.get("degraded_mode"), bool)
        and api_state == "up"
        and redis_state in {"up", "down"}
    )
    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "task": "AIX-MIG-06",
        "endpoint": f"{args.base_url}/health",
        "pass_contract": pass_contract,
        "missing_fields": missing,
        "health_payload": payload,
    }
    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), "pass_contract": pass_contract}, indent=2))


if __name__ == "__main__":
    main()
