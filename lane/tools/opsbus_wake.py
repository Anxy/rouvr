#!/usr/bin/env python3
"""OpsBus wake bridge for the event-driven lane (lane-ops #481).

Tails the OpsBus event stream the WorkHub outbox dispatcher already
feeds and touches the controller wake file (STATE_DIR/wake) for every
example-tenant task event. No hardcoded task ids: any event bound to
the lane project with a task id is a wake hint. The event is only a
hint; WorkHub stays the source of truth for claims and leases, and the
controller still polls every 10s, so a missed or refused stream never
stalls the lane.

Credentials: the controller's existing credential file (default the
lane-attempt-worker path from the installed canary config). No new
principal, no grant minting here: if OpsBus refuses the login (HTTP
403 without an opsbus read grant profile), the bridge exits nonzero
with a one-line reason and the controller keeps its fallback poll.

Stdlib only. Exit 0 = clean iteration under --once; the systemd unit
restarts the loop. Exit 2 = usage/config/auth error.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

DEFAULT_STATE_DIR = os.environ.get(
    "LANE_WAKE_STATE_DIR", "./.rouvr/lane-state")
DEFAULT_CREDS = os.environ.get(
    "LANE_WAKE_CREDS",
    "./.rouvr/creds/lane-worker.json")
DEFAULT_OPSBUS = os.environ.get(
    "LANE_WAKE_OPSBUS", "http://hyeve-opsbus-service:8000")
DEFAULT_AUTH = os.environ.get("LANE_WAKE_AUTH", "http://localhost:8001")
DEFAULT_TENANT = os.environ.get("LANE_WAKE_TENANT", "example-tenant")
DEFAULT_PROJECT = "lane-ops"
SEEN_CAP = 2000


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print("%s opsbus-wake: %s" % (utcnow(), msg), flush=True)


def load_creds(path: str) -> dict:
    if stat.S_IMODE(os.stat(path).st_mode) != 0o600:
        raise SystemExit("refusing %s: not 0600" % path)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def resolve_opsbus_base(configured: str) -> str:
    import socket
    from urllib.parse import urlparse
    try:
        host = urlparse(configured).hostname or ""
        port = urlparse(configured).port or 8000
        socket.create_connection((host, port), timeout=5).close()
        return configured.rstrip("/")
    except OSError:
        pass
    out = subprocess.run(
        ["docker", "inspect", "hyeve-opsbus-service", "--format",
         "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"],
        capture_output=True, timeout=20, text=True, check=False)
    ip = (out.stdout or "").strip()
    if not ip:
        raise SystemExit("OpsBus unreachable: %s and no container IP" %
                         configured)
    return "http://%s:8000" % ip


def opsbus_login(auth_base: str, creds: dict, tenant: str) -> str:
    body = json.dumps({
        "credential_id": creds.get("service_credential_id")
        or creds.get("credential_id"),
        "client_secret": creds.get("client_secret"),
        "audience": "hyeve-opsbus-service",
    }).encode()
    req = urllib.request.Request(
        auth_base.rstrip("/") + "/v1/auth/service-tokens", data=body,
        headers={"Content-Type": "application/json",
                 "X-Hyeve-Tenant-Id": tenant}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())["access_token"]
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise SystemExit(
                "OpsBus login 403: controller credential lacks an "
                "opsbus read grant; issue one via the #474/#475 grant "
                "profiles, then restart this bridge. Lane keeps its "
                "10s fallback poll meanwhile.")
        raise


def inbox_rows(base: str, jwt: str, tenant: str, since: str) -> list:
    url = base.rstrip("/") + "/api/v1/events?since=" + quote(since)
    req = urllib.request.Request(
        url, headers={"Authorization": "Bearer " + jwt,
                      "X-Hyeve-Tenant-Id": tenant})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode() or "{}")
    data = body.get("data", body)
    items = data if isinstance(data, list) else data.get("items", [])
    return items if isinstance(items, list) else []


def event_project(row: dict) -> str:
    """Top-level project first; payload/aggregate copies are fallbacks."""
    payload = row.get("payload") or {}
    aggregate = payload.get("aggregate") or {}
    return str(row.get("project_id") or payload.get("project_id")
               or payload.get("project") or aggregate.get("project_id")
               or "")


def event_task_id(row: dict) -> str:
    payload = row.get("payload") or {}
    aggregate = payload.get("aggregate") or {}
    return str(payload.get("task_id") or aggregate.get("task_id")
               or payload.get("id") or "")


def event_row_id(row: dict) -> str:
    return str(row.get("event_id") or row.get("id") or "")


def lane_event_wakes(row: dict, project: str) -> bool:
    """True for any event bound to this lane project with a task id.

    Deliberately no event-type or task-id allowlist: every status
    change, evidence row or review record on a lane task is a wake
    hint, and spurious wakes only cost one extra bounded poll.
    """
    if not isinstance(row, dict):
        return False
    if not event_row_id(row):
        return False
    if event_project(row) != project:
        return False
    return bool(event_task_id(row))


def touch_wake_file(state_dir: str) -> str:
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "wake")
    if os.path.exists(path):
        os.utime(path, None)
    else:
        with open(path, "wb"):
            pass
    return path


def load_cursor(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cursor(path: str, cursor: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cursor, fh, indent=1)
    os.replace(tmp, path)


def sense_once(base: str, jwt: str, tenant: str, project: str,
               cursor: dict) -> dict:
    """One poll: touch the wake file per new lane task event."""
    since = str(cursor.get("since") or "1970-01-01T00:00:00Z")
    rows = inbox_rows(base, jwt, tenant, since)
    seen = cursor.get("seen") or []
    seen_set = set(seen) if isinstance(seen, list) else set()
    fresh = 0
    for row in rows:
        if not lane_event_wakes(row, project):
            continue
        eid = event_row_id(row)
        if eid in seen_set:
            continue
        seen_set.add(eid)
        fresh += 1
    return {"rows": len(rows), "fresh": fresh,
            "seen": sorted(seen_set)[-SEEN_CAP:]}


def run_loop(args) -> int:
    creds = load_creds(args.creds)
    base = resolve_opsbus_base(args.opsbus_base)
    cursor_path = os.path.join(args.state_dir,
                               "opsbus-wake-cursor.json")
    cursor = load_cursor(cursor_path)
    try:
        jwt = opsbus_login(args.auth_base, creds, args.tenant)
    except SystemExit as exc:
        log(str(exc))
        return 2
    while True:
        try:
            if not jwt:
                jwt = opsbus_login(args.auth_base, creds, args.tenant)
            outcome = sense_once(base, jwt, args.tenant, args.project,
                                 cursor)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                jwt = ""
                log("OpsBus refused (%s); re-login next pass" % exc.code)
            else:
                log("sense failed: HTTP %s" % exc.code)
            outcome = {"rows": 0, "fresh": 0, "seen": cursor.get("seen", [])}
        except Exception as exc:  # noqa: BLE001 - sense failure is a
            # record, not a crash; the loop retries next pass.
            log("sense failed: %s: %s" % (type(exc).__name__, str(exc)[:160]))
            outcome = {"rows": 0, "fresh": 0, "seen": cursor.get("seen", [])}
        if outcome["fresh"]:
            touch_wake_file(args.state_dir)
            log("wake: %d new lane event(s)" % outcome["fresh"])
        cursor = {"since": utcnow(), "seen": outcome["seen"]}
        save_cursor(cursor_path, cursor)
        if args.once:
            return 0
        time.sleep(args.interval)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--creds", default=DEFAULT_CREDS)
    ap.add_argument("--opsbus-base", default=DEFAULT_OPSBUS)
    ap.add_argument("--auth-base", default=DEFAULT_AUTH)
    ap.add_argument("--tenant", default=DEFAULT_TENANT)
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args(argv)
    if not args.interval > 0:
        raise SystemExit("--interval must be positive")
    return run_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
