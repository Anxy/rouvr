"""docker/demo_runner.py — local full-profile worker that finishes *non-coding* plan tasks.

Temporal coding workers need Muse + an exec-spec repo. This process watches
plan.execute SSE events and can drive open/claimed plan-chain tasks through
claim → evidence → done via the ledger API — but only when
ROUVR_DEMO_RUNNER=1 and the task is not a coding delivery candidate.

Disable with ROUVR_DEMO_RUNNER=0 (recommended when proving Muse coder/reviewer).
Not for production.
"""
from __future__ import annotations

import json
import os
import time
import uuid

import httpx

BASE = os.environ.get("WORKHUB_BASE_URL") or os.environ.get("ROUVR_LEDGER_URL") or "http://ledger:8221"
TOKEN = os.environ.get("ROUVR_LEDGER_TOKEN", "")
PRINCIPAL = os.environ.get("ROUVR_EXECUTE_WORKER", "svc-demo-coder")
POLL_S = float(os.environ.get("ROUVR_DEMO_RUNNER_POLL_S", "2"))


def _client() -> httpx.Client:
    headers = {"X-Rouvr-Token": TOKEN} if TOKEN else {}
    return httpx.Client(base_url=BASE, timeout=60.0, headers=headers)


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.split("\n\n"):
        eid, etype, data_lines = None, None, []
        for line in block.splitlines():
            if line.startswith("id:"):
                eid = line[3:].strip()
            elif line.startswith("event:"):
                etype = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif line.startswith(":") or line.startswith("retry:") or not line.strip():
                continue
        if eid is None or etype is None or not data_lines:
            continue
        try:
            data = json.loads("".join(data_lines))
        except ValueError:
            continue
        events.append({"id": int(eid), "type": etype, "data": data})
    return events


def _events_after(c: httpx.Client, cursor: int) -> list[dict]:
    params: dict = {"live": "0", "limit": 200}
    if cursor:
        params["cursor"] = str(cursor)
    r = c.get("/v1/events", params=params)
    r.raise_for_status()
    return _parse_sse(r.text)


def _task(c: httpx.Client, tid: int) -> dict | None:
    r = c.get(f"/v1/tasks/{tid}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _is_coding_task(t: dict) -> bool:
    """Skip tasks that belong on the Muse Temporal path (exec spec / coding)."""
    if t.get("needs_spec") is False and t.get("pipeline"):
        # Bound repo → coding delivery owns it.
        for c in t.get("contracts") or []:
            try:
                body = c.get("body")
                if isinstance(body, str):
                    body = json.loads(body)
                if isinstance(body, dict) and body.get("category") == "feature":
                    # Still allow non-repo plan docs; only skip when a real repo is bound
                    # and start is the intended path. Conservatively skip when not needs_spec.
                    return True
            except ValueError:
                continue
    return False


def _done_key(tid: int, t: dict) -> str:
    """Generation-aware key so reclaim after a prior done is not a stale replay."""
    lease = t.get("lease_epoch")
    nested = t.get("lease")
    if lease is None and isinstance(nested, dict):
        lease = nested.get("lease_epoch")
    # Fall back to updated_at so a new claim generation gets a new key.
    gen = lease if lease is not None else str(t.get("updated_at") or "0")
    return f"demo-done:{tid}:{gen}"


def _complete(c: httpx.Client, tid: int) -> str:
    t = _task(c, tid)
    if t is None:
        return "missing"
    state = t.get("state")
    if state == "done":
        return "already-done"
    if state not in ("open", "claimed", "needs_review"):
        return f"skip:{state}"
    if _is_coding_task(t):
        return "skip:coding-task"

    if state == "open":
        claim = c.post(
            f"/v1/tasks/{tid}/assignments/claim",
            json={
                "principal": PRINCIPAL,
                "attempt_no": 1,
                "ttl_s": 300,
                "key": f"demo-claim:{tid}:{PRINCIPAL}",
            },
        )
        if claim.status_code >= 400:
            t = _task(c, tid) or t
            if t.get("state") not in ("claimed", "needs_review"):
                return f"claim-failed:{claim.status_code}:{claim.text[:120]}"
            # Refresh for generation-aware done key.
            t = _task(c, tid) or t

    fence = f"demo-runner:{tid}:{uuid.uuid4().hex[:8]}"
    ev = c.post(
        f"/v1/tasks/{tid}/evidence",
        json={
            "kind": "step",
            "body": {
                "fence": fence,
                "summary": "demo runner completed plan task (no Muse)",
                "principal": PRINCIPAL,
            },
        },
    )
    if ev.status_code >= 400:
        return f"evidence-failed:{ev.status_code}:{ev.text[:120]}"

    try:
        c.post("/v1/workers/heartbeat", json={"task_id": tid, "holder": PRINCIPAL})
    except httpx.HTTPError:
        pass

    key = _done_key(tid, t)
    tr = c.post(
        f"/v1/tasks/{tid}/transition",
        json={
            "to": "done",
            "idempotency_key": key,
            "owner": PRINCIPAL,
            "note": "demo runner",
        },
    )
    if tr.status_code >= 400:
        return f"transition-failed:{tr.status_code}:{tr.text[:160]}"
    # State-honest: replay of a stale key must not report done.
    after = _task(c, tid)
    if not after or after.get("state") != "done":
        return "transition-replay-stale"
    return "done"


def _task_ids_from_execute(payload: dict | None) -> list[int]:
    if not isinstance(payload, dict):
        return []
    ids = payload.get("task_ids") or payload.get("dispatched") or []
    out: list[int] = []
    for x in ids:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def _sweep_open_claimed(c: httpx.Client, label: str) -> None:
    """Startup/orphan sweep: plan tasks missed by SSE still converge."""
    r = c.get("/v1/tasks")
    if r.status_code >= 400:
        print(f"{label} sweep list failed: {r.status_code}", flush=True)
        return
    for t in r.json().get("items") or []:
        if t.get("state") not in ("open", "claimed"):
            continue
        tid = int(t["id"])
        result = _complete(c, tid)
        print(f"{label} sweep task {tid}: {result}", flush=True)


def _drain(c: httpx.Client, cursor: int, label: str) -> int:
    while True:
        batch = _events_after(c, cursor)
        if not batch:
            return cursor
        for ev in batch:
            cursor = max(cursor, ev["id"])
            if ev["type"] != "plan.execute":
                continue
            payload = (ev.get("data") or {}).get("payload") or {}
            for tid in _task_ids_from_execute(payload):
                result = _complete(c, tid)
                print(f"{label} task {tid}: {result}", flush=True)
        if len(batch) < 200:
            return cursor


def main() -> int:
    if os.environ.get("ROUVR_DEMO_RUNNER", "0").lower() in ("0", "false", "no"):
        print("demo runner disabled (ROUVR_DEMO_RUNNER=0); Muse coding path owns delivery", flush=True)
        return 0

    print(f"demo runner watching {BASE} as {PRINCIPAL}", flush=True)
    with _client() as c:
        for _ in range(60):
            try:
                if c.get("/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            print("ledger not healthy", flush=True)
            return 1

        cursor = _drain(c, 0, "catchup")
        print(f"catchup done cursor={cursor}", flush=True)
        _sweep_open_claimed(c, "startup")

        while True:
            try:
                cursor = _drain(c, cursor, "live")
            except httpx.HTTPError as e:
                print(f"events poll error: {e}", flush=True)
            time.sleep(POLL_S)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("demo runner stopped", flush=True)
        raise SystemExit(0)
