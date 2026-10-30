"""Demo ops task: "Done. No action needed." (first pass, runs locally).

Instruction -> contract -> execute -> independent verify -> receipts, against
the Rouvr ledger. Delivery goes to a local file outbox (demo/.outbox), never a
real mailbox. Failure injection via ROUVR_INJECT:
  kill_midstep     crash after delivery, before close; a rerun must not double-send
  silent_delivery  primary sink reports OK but delivers nothing; reviewer must catch it
  revoked_key      model provider unavailable; task parks as blocked and escalates
Usage: python demo/morning_report.py [--ledger http://localhost:8221]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "core")]
OUTBOX = Path(os.environ.get("ROUVR_DEMO_OUTBOX", ROOT / "demo" / ".outbox"))
INSTRUCTION = ("Every morning, compile yesterday's lane report (tasks closed, parks and "
               "escalations), publish it to the stakeholder inbox, make sure the ledger "
               "is healthy, and escalate if anything fails.")


def ledger_client(url: str | None):
    if url:
        import httpx
        return httpx.Client(base_url=url, timeout=10)
    from fastapi.testclient import TestClient
    from ledger.app import make_app
    return TestClient(make_app(os.environ.get("ROUVR_LEDGER_DATABASE_URL", f"sqlite:///{ROOT}/demo/.rouvr-demo.db")))


def compile_contract(instruction: str) -> dict:
    """Intent -> typed execution contract via the configured model provider."""
    from backend.rouvr_models.providers import get_provider
    p = get_provider()
    if os.environ.get("ROUVR_INJECT") == "revoked_key":
        raise RuntimeError("model credential revoked (injected)")
    raw = p.chat([{"role": "system", "content": "Return a JSON execution contract."},
                  {"role": "user", "content": instruction}])
    contract = {
        "steps": ["health_probe", "compile_report", "deliver", "verify"],
        "success_criteria": ["report delivered and read back", "figures match ledger", "health ok"],
        "side_effects": ["write one message to stakeholder outbox"],
        "escalation": "owner",
        "expires_in_hours": 24,
        "provider": p.name,
    }
    if p.name != "stub":
        try:
            contract.update(json.loads(raw))
        except (ValueError, TypeError):
            contract["model_notes"] = raw[:500]
    return contract


def deliver(report: str, key: str, sink: str) -> dict:
    """Idempotent delivery keyed by run; returns a provider-style message id."""
    msg_id = hashlib.sha256(f"{key}:{sink}".encode()).hexdigest()[:16]
    target = OUTBOX / f"{msg_id}.txt"
    if sink == "primary" and os.environ.get("ROUVR_INJECT") == "silent_delivery":
        return {"ok": True, "message_id": msg_id}  # lies: nothing written
    OUTBOX.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(report)
    return {"ok": True, "message_id": msg_id}


def verify(c, msg_id: str, figures: dict) -> dict:
    """Independent checks: read back the delivered message, recheck figures, probe health."""
    path = OUTBOX / f"{msg_id}.txt"
    readback = path.read_text() if path.exists() else None
    items = c.get("/v1/tasks").json()["items"]
    recount = {s: sum(1 for t in items if t["state"] == s) for s in ("done", "blocked", "failed")}
    health = c.get("/health").status_code == 200
    try:
        figures_match = bool(readback) and json.loads(readback.split("\n", 1)[1])["figures"] == recount
    except (ValueError, IndexError):
        readback, figures_match = None, False  # malformed outbox file: treat as no-readback
    ok = bool(readback) and figures_match and health
    return {"verdict": "pass" if ok else "fail", "readback": bool(readback),
            "figures_match": figures_match,
            "health": health}


def run(ledger_url: str | None = None) -> str:
    c = ledger_client(ledger_url)
    day = (date.today() - timedelta(days=1)).isoformat()
    run_key = f"morning-report:{day}"
    existing = [t for t in c.get("/v1/tasks").json()["items"] if t["title"] == run_key]
    tid = existing[0]["id"] if existing else c.post("/v1/tasks", json={"title": run_key, "intent": INSTRUCTION}).json()["id"]
    task = c.get(f"/v1/tasks/{tid}").json()
    if task["state"] == "done":
        return "Done. No action needed."
    try:
        contract = compile_contract(INSTRUCTION)
    except Exception as exc:  # park before claim, escalate
        c.post(f"/v1/tasks/{tid}/evidence", json={"kind": "escalation", "body": {"reason": str(exc)}})
        c.post(f"/v1/tasks/{tid}/transition", json={"to": "blocked", "idempotency_key": f"{run_key}:park"})
        return f"ESCALATED: {exc}"
    c.post(f"/v1/tasks/{tid}/contracts", json=contract)
    c.post(f"/v1/tasks/{tid}/transition", json={"to": "claimed", "idempotency_key": f"{run_key}:claim", "owner": "ops-worker"})
    items = c.get("/v1/tasks").json()["items"]
    figures = {s: sum(1 for t in items if t["state"] == s) for s in ("done", "blocked", "failed")}
    report = f"Lane report for {day}\n" + json.dumps({"figures": figures}, sort_keys=True)
    result = None
    for sink in ("primary", "fallback"):
        sent = deliver(report, run_key, sink)
        c.post(f"/v1/tasks/{tid}/evidence", json={"kind": "delivery", "body": {"sink": sink, **sent}})
        if os.environ.get("ROUVR_INJECT") == "kill_midstep":
            os.environ.pop("ROUVR_INJECT")
            raise SystemExit("worker killed mid-step (injected); rerun to resume")
        result = verify(c, sent["message_id"], figures)
        c.post(f"/v1/tasks/{tid}/evidence", json={"kind": "reviewer", "body": {"sink": sink, **result}})
        if result["verdict"] == "pass":
            break
    if not result or result["verdict"] != "pass":
        c.post(f"/v1/tasks/{tid}/transition", json={"to": "failed", "idempotency_key": f"{run_key}:fail"})
        return "ESCALATED: verification failed on all sinks"
    c.post(f"/v1/tasks/{tid}/transition", json={"to": "done", "idempotency_key": f"{run_key}:done"})
    return "Done. No action needed."


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default=os.environ.get("ROUVR_LEDGER_URL"))
    print(run(ap.parse_args().ledger))
