#!/usr/bin/env python3
"""Direct-HTTP lane CLI for hosts without docker (e.g. a bare VM), stdlib only.

Talks to the private WorkHub/ledger route (WH_BASE, e.g.
http://localhost:8221) with a 0600 credential file. JWT lives in memory
only, never in argv/logs. Every mutation requires --key.

Usage:
    python3 tools/wh_direct.py --creds FILE --base http://localhost:8221 \\
        --tenant example-tenant --project example-project read-task 1
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import urllib.error
import urllib.request
from typing import Any, Callable


REFUSAL_EXIT = 2
SENSITIVE_KEYS = frozenset({
    "access_token", "authorization", "client_secret", "credential",
    "credentials", "jwt", "password", "private_key", "refresh_token",
    "secret", "token",
})


def load_creds(path: str) -> dict[str, Any]:
    if stat.S_IMODE(os.stat(path).st_mode) != 0o600:
        raise SystemExit("refusing %s: not 0600" % path)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def login(auth_base: str, creds: dict[str, Any]) -> str:
    """Password login (humans/legacy bots). Kept for lane use."""
    body = json.dumps({"username": creds["username"],
                       "password": creds["password"]}).encode()
    req = urllib.request.Request(
        auth_base.rstrip("/") + "/v1/auth/login", data=body,
        headers={"Content-Type": "application/json",
                 "X-Hyeve-Tenant-Id": creds["tenant"]}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["access_token"]


def service_login(auth_base: str, creds: dict[str, Any]) -> str:
    """Mint a scoped service token from a service credential.

    Creds shape: {service_credential_id, client_secret, audience, tenant}.
    The secret travels only in this POST body, never in argv/logs/output.
    """
    body = json.dumps({"credential_id": creds["service_credential_id"],
                       "client_secret": creds["client_secret"],
                       "audience": creds["audience"]}).encode()
    req = urllib.request.Request(
        auth_base.rstrip("/") + "/v1/auth/service-tokens", data=body,
        headers={"Content-Type": "application/json",
                 "X-Hyeve-Tenant-Id": creds["tenant"]}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["access_token"]


def authenticate(auth_base: str, creds: dict[str, Any]) -> str:
    """Dispatch on creds shape: service credential preferred, password legacy."""
    if "service_credential_id" in creds:
        return service_login(auth_base, creds)
    return login(auth_base, creds)


def wh_call(base: str, jwt: str, method: str, path: str, tenant: str,
            project: str, key: str = "",
            payload: dict[str, Any] | None = None,
            payload_json: str | None = None,
            opener: Callable[..., Any] | None = None) -> dict[str, Any]:
    if method.upper() != "GET" and not key:
        return {"http": 400, "body": {"code": "KEY.REQUIRED",
                                      "detail": "mutations require --key"}}
    if payload is not None and payload_json is not None:
        return {"http": 400, "body": {"code": "PAYLOAD.AMBIGUOUS",
                                      "detail": "use payload or payload_json"}}
    if payload_json is not None:
        # Recovery callers persist this exact JSON string before their first
        # request.  Sending it verbatim makes an idempotent replay byte-for-
        # byte identical after a timeout or process restart.
        try:
            parsed = json.loads(payload_json)
        except (TypeError, ValueError):
            return {"http": 400, "body": {"code": "PAYLOAD.INVALID_JSON",
                                          "detail": "payload_json invalid"}}
        if not isinstance(parsed, dict):
            return {"http": 400, "body": {"code": "PAYLOAD.NOT_OBJECT",
                                          "detail": "payload_json must be object"}}
        data = payload_json.encode("utf-8")
    else:
        data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": "Bearer " + jwt,
               "X-Hyeve-Tenant-Id": tenant, "X-Project-ID": project}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(base.rstrip("/") + path, data=data,
                                 headers=headers, method=method)
    opener = opener or urllib.request.urlopen
    try:
        with opener(req, timeout=30) as resp:
            return {"http": resp.status,
                    "body": json.loads(resp.read().decode() or "{}")}
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode() or "{}")
        except ValueError:
            detail = {}
        return {"http": exc.code, "body": detail}


def read_path(project: str, target: str) -> str:
    if target.isdigit():
        return "/api/v1/projects/%s/tasks/by-number/%s" % (project, target)
    return "/api/v2/workhub/tasks/%s" % target


def response_data(res: dict[str, Any]) -> Any:
    body = res.get("body", {})
    return body.get("data", body) if isinstance(body, dict) else body


def response_items(res: dict[str, Any]) -> list[dict[str, Any]]:
    data = response_data(res)
    if isinstance(data, dict):
        data = data.get("items", data.get("results", []))
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def sanitize(value: Any) -> Any:
    """Recursively redact fields that could expose credentials or tokens."""
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def json_contains(stored: Any, submitted: Any) -> bool:
    """Whether canonical stored JSON preserves every submitted value."""
    if isinstance(submitted, dict):
        return isinstance(stored, dict) and all(
            key in stored and json_contains(stored[key], value)
            for key, value in submitted.items()
        )
    if isinstance(submitted, list):
        return isinstance(stored, list) and len(stored) == len(submitted) and all(
            json_contains(left, right) for left, right in zip(stored, submitted)
        )
    return stored == submitted


def refusal_reason(res: dict[str, Any], *, reject_replay: bool = False) -> str | None:
    """Return a compact reason for HTTP, envelope, replay, or nested failure."""
    http = int(res.get("http", 500))
    body = res.get("body", {})
    if http >= 300:
        detail = body.get("detail", body) if isinstance(body, dict) else {}
        if isinstance(detail, dict):
            return str(detail.get("code") or detail.get("title") or "http_%s" % http)
        return "http_%s" % http
    if isinstance(body, dict) and body.get("success") is False:
        return str(body.get("code") or "response_unsuccessful")
    if reject_replay and isinstance(body, dict) and body.get("idempotent_replay") is True:
        return "idempotent_replay"
    data = response_data(res)
    if isinstance(data, dict):
        if reject_replay and data.get("idempotent_replay") is True:
            return "idempotent_replay"
        failed = data.get("failed")
        if isinstance(failed, int) and failed > 0:
            return "nested_batch_failure"
        for item in data.get("results", []) if isinstance(data.get("results"), list) else []:
            if isinstance(item, dict) and item.get("success") is False:
                return str(item.get("code") or item.get("error_code") or "nested_item_failure")
            if reject_replay and isinstance(item, dict) and item.get("idempotent_replay") is True:
                return "idempotent_replay"
    return None


def load_json_file(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def get_task(base: str, jwt: str, tenant: str, project: str,
             target: str) -> tuple[dict[str, Any], dict[str, Any]]:
    res = wh_call(base, jwt, "GET", read_path(project, target), tenant, project)
    data = response_data(res)
    task = data if isinstance(data, dict) else {}
    # The v2 ID read intentionally projects a compact shape and calls the ID
    # field task_id. Enrich it through the canonical by-number read so the
    # transition can carry the observed record version (CAS) when available.
    if (res.get("http") == 200 and task.get("task_number") is not None
            and (task.get("record_version") is None or not task.get("id"))):
        full_res = wh_call(base, jwt, "GET",
                           read_path(project, str(task["task_number"])),
                           tenant, project)
        full = response_data(full_res)
        if full_res.get("http") == 200 and isinstance(full, dict):
            task = {**task, **full}
            res = full_res
    return res, task


def list_evidence(base: str, jwt: str, tenant: str, project: str,
                  task_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = "/api/v1/tasks/%s/evidence?project_id=%s&limit=500&offset=0" % (task_id, project)
    res = wh_call(base, jwt, "GET", path, tenant, project)
    return res, response_items(res)


def attach_evidence(base: str, jwt: str, tenant: str, project: str,
                    task_id: str, key: str, packet: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "project_id": project,
        "evidence_type": packet.get("evidence_type", "generic"),
        "evidence_ref": packet.get("evidence_ref"),
        "evidence_json": packet.get("evidence_json", {}),
    }
    if packet.get("artefact_id") is not None:
        payload["artefact_id"] = packet["artefact_id"]
    res = wh_call(base, jwt, "POST", "/api/v1/tasks/%s/evidence" % task_id,
                  tenant, project, key=key, payload=payload)
    reason = refusal_reason(res, reject_replay=True)
    if reason:
        raise ValueError(reason)
    created = response_data(res)
    evidence_id = created.get("id") if isinstance(created, dict) else None
    if not evidence_id:
        raise ValueError("evidence_id_missing")
    read_res, rows = list_evidence(base, jwt, tenant, project, task_id)
    if refusal_reason(read_res):
        raise ValueError("evidence_readback_failed")
    matches = [row for row in rows if row.get("id") == evidence_id]
    if len(matches) != 1:
        raise ValueError("evidence_exact_readback_missing")
    row = matches[0]
    expected_ref = payload["evidence_ref"]
    if expected_ref is None and payload.get("artefact_id"):
        expected_ref = "artefact:%s" % payload["artefact_id"]
    if (row.get("task_id") != task_id or row.get("project_id") != project
            or row.get("evidence_type") != payload["evidence_type"]
            or row.get("evidence_ref") != expected_ref
            or not json_contains(row.get("evidence_json", {}),
                                 payload["evidence_json"])):
        raise ValueError("evidence_exact_readback_mismatch")
    return row


def normalize_evaluation(packet: Any, project: str) -> dict[str, Any]:
    if isinstance(packet, list):
        packet = {"decision": "pass", "checks": packet}
    if not isinstance(packet, dict):
        raise ValueError("evaluation_packet_must_be_object_or_check_list")
    result = dict(packet)
    result["project_id"] = project
    result.setdefault("decision", "pass")
    result.setdefault("checks", [])
    return result


def validate_artifact_refs(checks: list[Any], evidence: list[dict[str, Any]]) -> None:
    refs = {str(row.get("id")) for row in evidence if row.get("id")}
    refs.update(str(row.get("evidence_ref")) for row in evidence if row.get("evidence_ref"))
    if not checks:
        raise ValueError("verification_checks_required")
    for check in checks:
        artifact_ref = check.get("artifact_ref") if isinstance(check, dict) else None
        if not artifact_ref or str(artifact_ref) not in refs:
            raise ValueError("artifact_ref_not_on_task:%s" % (artifact_ref or "missing"))


def evaluate_task(base: str, jwt: str, tenant: str, project: str,
                  task_id: str, key: str, packet: Any,
                  *, reject_replay: bool = False) -> dict[str, Any]:
    evidence_res, evidence = list_evidence(base, jwt, tenant, project, task_id)
    reason = refusal_reason(evidence_res)
    if reason:
        raise ValueError(reason)
    payload = normalize_evaluation(packet, project)
    validate_artifact_refs(payload.get("checks", []), evidence)
    res = wh_call(base, jwt, "POST", "/api/v1/tasks/%s/verification/evaluate" % task_id,
                  tenant, project, key=key, payload=payload)
    reason = refusal_reason(res, reject_replay=reject_replay)
    if reason:
        raise ValueError(reason)
    data = response_data(res)
    if not isinstance(data, dict):
        raise ValueError("evaluation_response_invalid")
    return data


def close_task(base: str, jwt: str, tenant: str, project: str, target: str,
               packet: Any, eval_key: str, walk_key: str) -> dict[str, Any]:
    if not eval_key or not walk_key or eval_key == walk_key:
        raise ValueError("distinct_fresh_eval_and_walk_keys_required")
    task_res, task = get_task(base, jwt, tenant, project, target)
    reason = refusal_reason(task_res)
    if reason:
        raise ValueError(reason)
    task_id = str(task.get("id") or task.get("task_id")
                  or (target if not target.isdigit() else ""))
    if not task_id:
        raise ValueError("task_id_missing")
    if task.get("status") != "needs_review":
        raise ValueError("task_not_in_needs_review")
    evaluation = evaluate_task(base, jwt, tenant, project, task_id, eval_key,
                               packet, reject_replay=True)
    if evaluation.get("eligible_for_done") is not True:
        raise ValueError("evaluation_not_eligible_for_done")
    walk_payload = {
        "project_id": project,
        "mode": "partial",
        "operation_id": walk_key,
        "items": [{
            "item_id": task_id,
            "idempotency_key": walk_key,
            "task_id": task_id,
            "status": "done",
            "reason": "verification evaluation eligible_for_done=true",
            "expected_record_version": task.get("record_version"),
        }],
    }
    batch_key = walk_key + ":batch"
    walk_payload["operation_id"] = batch_key
    walk = wh_call(base, jwt, "POST",
                   "/api/v1/projects/%s/tasks/transitions/batch" % project,
                   tenant, project, key=batch_key, payload=walk_payload)
    reason = refusal_reason(walk, reject_replay=True)
    if reason:
        raise ValueError(reason)
    walk_data = response_data(walk)
    if not isinstance(walk_data, dict) or walk_data.get("updated") != 1:
        raise ValueError("transition_not_confirmed")
    read_res, final_task = get_task(base, jwt, tenant, project, task_id)
    if refusal_reason(read_res) or final_task.get("status") != "done":
        raise ValueError("terminal_readback_failed")
    return {"task_id": task_id, "status": "done",
            "evaluation_id": evaluation.get("id"),
            "record_version": final_task.get("record_version")}


SAFE_TASK_FIELDS = ("task_number", "title", "status", "description")


def describe_task(res: dict[str, Any]) -> dict[str, Any]:
    """Project a task body onto the allowlisted usable fields.

    Only these four fields ever print; tokens/credentials cannot pass
    through even if the server ever embeds them elsewhere in the body.
    """
    data = res.get("body", {}).get("data", res.get("body", {}))
    if not isinstance(data, dict):
        return {"http": res.get("http"), "task": {}}
    return {"http": res.get("http"),
            "task": {key: data.get(key) for key in SAFE_TASK_FIELDS}}


def describe_from_list(res: dict[str, Any], target: str) -> dict[str, Any]:
    """Project one task from a LIST response onto the allowlisted fields.

    Read-scoped identities (workhub.tasks.read) can LIST but not read
    by-number, so this is the only individual-description path that works
    without broadening the grant. Matches task_number (digits) or id.
    Exactly one GET (the list itself) is ever issued by the caller.
    """
    body = res.get("body", {})
    data = body.get("data", body) if isinstance(body, dict) else {}
    items = data.get("items", data) if isinstance(data, dict) else []
    if not isinstance(items, list):
        return {"http": res.get("http"), "task": {},
                "detail": "no item list in response"}
    for task in items:
        if not isinstance(task, dict):
            continue
        if str(task.get("task_number")) == target or task.get("id") == target:
            return {"http": res.get("http"), "task": {
                key: task.get(key) for key in SAFE_TASK_FIELDS}}
    return {"http": res.get("http"), "task": {},
            "detail": "task not in list response"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Direct-HTTP lane CLI (no docker).")
    ap.add_argument("--creds", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--key", default="")
    ap.add_argument("--auth-base", default="")
    ap.add_argument("action", choices=("ping", "read-task", "list-tasks",
                                       "knowledge-search", "describe-task",
                                       "describe-list", "list-evidence",
                                       "attach-evidence", "evaluate-task",
                                       "close-task"))
    ap.add_argument("target", nargs="?", default="")
    ap.add_argument("arguments", nargs="*")
    args = ap.parse_args(argv)

    creds = load_creds(args.creds)
    jwt = authenticate(args.auth_base or creds.get("auth_base", ""), creds)
    if args.action == "ping":
        res = wh_call(args.base, jwt, "GET", "/api/v1/capabilities",
                      args.tenant, args.project, key=args.key)
    elif args.action == "read-task":
        res = wh_call(args.base, jwt, "GET", read_path(args.project, args.target),
                      args.tenant, args.project, key=args.key)
    elif args.action == "describe-task":
        res = wh_call(args.base, jwt, "GET", read_path(args.project, args.target),
                      args.tenant, args.project, key=args.key)
        if res.get("http") == 200:
            print(json.dumps(describe_task(res))[:4000])
            return 0
    elif args.action == "list-tasks":
        res = wh_call(args.base, jwt, "GET",
                      "/api/v1/projects/%s/tasks" % args.project,
                      args.tenant, args.project, key=args.key)
    elif args.action == "describe-list":
        res = wh_call(args.base, jwt, "GET",
                      "/api/v1/projects/%s/tasks" % args.project,
                      args.tenant, args.project, key=args.key)
        if res.get("http") == 200:
            print(json.dumps(describe_from_list(res, args.target))[:4000])
            return 0
    elif args.action == "knowledge-search":
        res = wh_call(args.base, jwt, "GET",
                      "/api/v1/projects/%s/knowledge/search?q=%s"
                      % (args.project, args.target),
                      args.tenant, args.project, key=args.key)
    elif args.action == "list-evidence":
        res, rows = list_evidence(args.base, jwt, args.tenant, args.project,
                                  args.target)
        if not refusal_reason(res):
            print(json.dumps({"http": res.get("http"),
                              "evidence": sanitize(rows)})[:200000])
            return 0
    elif args.action == "attach-evidence":
        if len(args.arguments) != 1 or not args.key:
            ap.error("attach-evidence requires TARGET PACKET.json and --key")
        try:
            row = attach_evidence(args.base, jwt, args.tenant, args.project,
                                  args.target, args.key,
                                  load_json_file(args.arguments[0]))
        except ValueError as exc:
            print(json.dumps({"refused": str(exc)}))
            return REFUSAL_EXIT
        print(json.dumps({"http": 201, "evidence_id": row.get("id"),
                          "readback": "exact"}))
        return 0
    elif args.action == "evaluate-task":
        if len(args.arguments) != 1 or not args.key:
            ap.error("evaluate-task requires TARGET CHECKS.json and --key")
        try:
            data = evaluate_task(args.base, jwt, args.tenant, args.project,
                                 args.target, args.key,
                                 load_json_file(args.arguments[0]),
                                 reject_replay=True)
        except ValueError as exc:
            print(json.dumps({"refused": str(exc)}))
            return REFUSAL_EXIT
        print(json.dumps({"http": 200,
                          "eligible_for_done": data.get("eligible_for_done"),
                          "evaluation_id": data.get("id")}))
        return 0
    elif args.action == "close-task":
        if len(args.arguments) != 3:
            ap.error("close-task requires TARGET CHECKS.json EVAL_KEY WALK_KEY")
        try:
            result = close_task(args.base, jwt, args.tenant, args.project,
                                args.target,
                                load_json_file(args.arguments[0]),
                                args.arguments[1], args.arguments[2])
        except ValueError as exc:
            print(json.dumps({"refused": str(exc)}))
            return REFUSAL_EXIT
        print(json.dumps(result))
        return 0
    print(json.dumps({"http": res.get("http"),
                      "keys": sorted(res.get("body", {}))[:8]
                      if isinstance(res.get("body"), dict) else []})[:800])
    return 0 if res.get("http", 500) < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
