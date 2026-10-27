from __future__ import annotations

import argparse
import base64
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def _b64url(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _token(*, roles: list[str]) -> str:
    header = {"alg": "none", "typ": "JWT"}
    claims = {"sub": "local-operator", "tenant_id": "default", "roles": roles, "service": False}
    return f"{_b64url(header)}.{_b64url(claims)}."


@dataclass(slots=True)
class CaseResult:
    name: str
    expected_status: int
    expected_code: str
    actual_status: int
    actual_code: str | None
    pass_case: bool
    response_body: dict[str, Any] | str | None


def _request_json(
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 20,
) -> tuple[int, dict[str, Any] | str | None]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url=url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if raw:
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    return resp.status, raw
            return resp.status, None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return int(exc.code), json.loads(raw)
        except json.JSONDecodeError:
            return int(exc.code), raw


def _extract_code(body: dict[str, Any] | str | None) -> str | None:
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    if isinstance(detail, dict):
        code = detail.get("code")
        return str(code) if code is not None else None
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate normalized WorkHub contract errors and operator diagnostics."
    )
    parser.add_argument("--base-url", default="http://localhost:8221", help="WorkHub API base URL.")
    parser.add_argument("--project-id", default="bh-migrated-7-aix", help="Project ID for scoped cases.")
    parser.add_argument(
        "--report-path",
        default="C:/dev/hyeve/_runlogs/aix_mig05_contract_diagnostics.json",
        help="Output report path.",
    )
    args = parser.parse_args()

    manager_token = _token(roles=["manager", "contributor", "verifier"])
    contributor_token = _token(roles=["contributor"])
    fake_task_id = str(uuid4())

    cases: list[tuple[str, int, str, str, str, dict[str, str], dict[str, Any] | None]] = [
        (
            "auth_required_without_bearer",
            401,
            "auth_required",
            "GET",
            f"{args.base_url}/api/v1/projects/{args.project_id}/tasks/by-number/999999",
            {"X-Project-ID": args.project_id},
            None,
        ),
        (
            "project_required_when_context_missing",
            400,
            "project_required",
            "POST",
            f"{args.base_url}/api/v1/tasks/{fake_task_id}/transition",
            {
                "Authorization": f"Bearer {manager_token}",
                "Content-Type": "application/json",
                "Idempotency-Key": f"mig05-{uuid4()}",
            },
            {"status": "running"},
        ),
        (
            "idempotency_required_for_transition",
            400,
            "idempotency_key_required",
            "POST",
            f"{args.base_url}/api/v1/tasks/{fake_task_id}/transition",
            {
                "Authorization": f"Bearer {manager_token}",
                "Content-Type": "application/json",
                "X-Project-ID": args.project_id,
            },
            {"project_id": args.project_id, "status": "running"},
        ),
        (
            "forbidden_for_non_manager_plan_generate",
            403,
            "forbidden",
            "POST",
            f"{args.base_url}/api/v1/plans/generate",
            {
                "Authorization": f"Bearer {contributor_token}",
                "Content-Type": "application/json",
                "X-Project-ID": args.project_id,
                "Idempotency-Key": f"mig05-{uuid4()}",
            },
            {
                "project_id": args.project_id,
                "title": "diag",
                "goal": "validate",
                "verification_method": "manual",
                "tasks": [
                    {"task_key": "k1", "title": "t", "assigned_role": "contributor", "definition_of_done": "done"}
                ],
            },
        ),
    ]

    results: list[CaseResult] = []
    for name, expected_status, expected_code, method, url, headers, payload in cases:
        status, body = _request_json(method=method, url=url, headers=headers, payload=payload)
        code = _extract_code(body)
        results.append(
            CaseResult(
                name=name,
                expected_status=expected_status,
                expected_code=expected_code,
                actual_status=status,
                actual_code=code,
                pass_case=(status == expected_status and code == expected_code),
                response_body=body,
            )
        )

    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "task": "AIX-MIG-05",
        "scope": "Contract error normalization and operator diagnostics",
        "summary": {
            "total_cases": len(results),
            "passed": sum(1 for r in results if r.pass_case),
            "failed": sum(1 for r in results if not r.pass_case),
        },
        "cases": [asdict(r) for r in results],
    }
    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(report_path), "summary": report["summary"]}, indent=2))


if __name__ == "__main__":
    main()
