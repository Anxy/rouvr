"""Timed run-throughs of the morning-report ops pack against the supervisor stack.

Fresh run keys per attempt; wall time recorded per leg. Run ONLY when no coding
task is active: the kill leg SIGKILLs all workers, and the fake-200 leg
restarts them with ROUVR_INJECT=silent_delivery (restored clean afterwards).

Usage (from the repo root, stack already up — see demo/RUNBOOK.md):
    .venv/bin/python demo/rehearsal/run_tick.py [t1|t2|t3|all]

Artifacts: demo/rehearsal/<today>/{t1-clean,t2-kill,t3-fake200}/ with
result.json (elapsed_s), task.json, receipts.json, outbox.txt, judge.json,
history.json. Evidence is verifier-produced, never narrative.
"""
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))
OPS = Path(os.environ.get("ROUVR_OPS_DIR") or str(ROOT / ".data"))
OUTBOX = ROOT / "demo" / ".outbox"
ART = ROOT / "demo" / "rehearsal" / datetime.now(timezone.utc).strftime("%Y-%m-%d")

LEDGER = os.environ.get("ROUVR_LEDGER_URL", "http://127.0.0.1:8221")
TEMPORAL = os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233")

import httpx
from temporalio.client import Client

from rouvr.contracts import OpsInput

INSTRUCTION = ("Every morning, compile yesterday's lane report (tasks closed, parks and "
               "escalations), publish it to the stakeholder inbox, make sure the ledger "
               "is healthy, and escalate if anything fails.")


def api():
    return httpx.Client(base_url=LEDGER, timeout=15,
                        headers={"X-Rouvr-Token": (OPS / "api-token").read_text().strip()})


def stack_env():
    # Full sourced env through bash so $(cat ...) expansions in env.sh resolve.
    out = subprocess.run(
        ["bash", "-c", f"source {OPS}/env.sh >/dev/null 2>&1; "
                       f"source {OPS}/meta-provider-env.sh >/dev/null 2>&1; "
                       "python3 -c 'import os,json;print(json.dumps(dict(os.environ)))'"],
        capture_output=True, text=True, timeout=30)
    return json.loads(out.stdout)


def worker_pids():
    out = []
    for pid in subprocess.run(["pgrep", "-f", "rouvr.worker"], capture_output=True,
                              text=True).stdout.split():
        try:
            if os.readlink(f"/proc/{pid}/cwd") == str(ROOT):
                out.append(int(pid))
        except OSError:
            continue
    return out


def start_worker(queue, extra_env=None):
    env = stack_env()
    env["PYTHONPATH"] = str(ROOT) + ":"
    if extra_env:
        env.update(extra_env)
    log = open(OPS / f"run-tick-worker-{queue}.log", "ab")
    p = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "rouvr.worker", queue],
                         cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (OPS / "pids" / f"worker-{queue}.pid").write_text(str(p.pid))
    return p.pid


async def run_pack(client, run_key):
    from rouvr.workflows import OpsTaskWorkflow
    return await client.start_workflow(
        OpsTaskWorkflow.run, OpsInput(run_key, INSTRUCTION, "rehearse", "demo"),
        id=f"rerun-{run_key}-{int(time.time())}", task_queue="rouvr-orchestrator")


def _res(result):
    if isinstance(result, dict):
        return result
    return {"state": result.state, "digest": result.digest, "sink": result.sink,
            "message_id": result.message_id, "task_id": result.task_id}


async def collect(client, handle, run_key, outdir, t0):
    res = _res(await handle.result())
    dt = time.time() - t0
    task_id = res["task_id"]
    c = api()
    task = c.get(f"/v1/tasks/{task_id}").json()
    c.close()
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "result.json").write_text(json.dumps(
        {"run_key": run_key, "workflow_id": handle.id, "elapsed_s": round(dt, 1),
         **res, "task_id": task_id}, indent=2))
    (outdir / "task.json").write_text(json.dumps(task, indent=2)[:20000])
    receipts = [e for e in task.get("evidence", []) if e.get("kind") == "ops_receipt"]
    (outdir / "receipts.json").write_text(json.dumps(receipts, indent=2)[:20000])
    files = sorted(p.name for p in OUTBOX.iterdir()) if OUTBOX.is_dir() else []
    (outdir / "outbox.txt").write_text("\n".join(files) + "\n")
    rev = [e for e in task.get("evidence", []) if e.get("kind") == "reviewer"]
    (outdir / "judge.json").write_text(json.dumps(rev, indent=2)[:4000])
    try:
        hist = await handle.fetch_history()
        (outdir / "history.json").write_text(hist.to_json()[:200000])
    except Exception as e:  # noqa: BLE001
        (outdir / "history.txt").write_text(f"history export failed: {e}\n")
    return res, task


async def leg(which):
    stamp = os.environ.get("RERUN_STAMP", str(int(time.time()))[-6:])
    keys = {k: f"morning-report:{k}-{stamp}" for k in ("t1", "t2", "t3")}
    c = api()
    c.post("/v1/tenants", json={"tenant": "rehearse"})
    c.post("/v1/projects", json={"tenant": "rehearse", "project": "demo"})
    exp = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    c.post("/v1/grants", json={"scope": "ops:morning-report", "contract_rev": "",
                               "expires_at": exp, "approved_by": "owner"}).raise_for_status()
    c.close()
    client = await Client.connect(TEMPORAL, namespace="default")
    if which in ("t1", "all"):
        t0 = time.time()
        h = await run_pack(client, keys["t1"])
        r, task = await collect(client, h, keys["t1"], ART / "t1-clean", t0)
        assert r["state"] == "done" and r["sink"] == "primary", r
        assert len([e for e in task.get("evidence", []) if e.get("kind") == "ops_receipt"]) == 1
        print(f"t1 clean done in {time.time()-t0:.0f}s", flush=True)
    if which in ("t2", "all"):
        t0 = time.time()
        h = await run_pack(client, keys["t2"])
        kill_state = []

        def killer():
            import httpx as hx
            cc = hx.Client(base_url=LEDGER, timeout=10,
                           headers={"X-Rouvr-Token": (OPS / "api-token").read_text().strip()})
            for _ in range(600):
                try:
                    items = cc.get("/v1/tasks").json()["items"]
                    st = next((t["state"] for t in items if t["title"] == keys["t2"]), None)
                except Exception:  # noqa: BLE001
                    st = None
                if st in ("open", "claimed"):
                    break
                time.sleep(0.5)
            try:
                items = cc.get("/v1/tasks").json()["items"]
                st_at_kill = next((t["state"] for t in items if t["title"] == keys["t2"]), None)
            except Exception:  # noqa: BLE001
                st_at_kill = None
            for p in worker_pids():
                try:
                    os.kill(p, 9)
                except ProcessLookupError:
                    pass
            print(f"killed workers at task state={st_at_kill}", flush=True)
            kill_state.append(st_at_kill)
            time.sleep(8)
            for q in ("orch", "build", "verify", "test"):
                start_worker(q)
            print("workers restarted", flush=True)

        kt = threading.Thread(target=killer, daemon=True)
        kt.start()
        r, _ = await collect(client, h, keys["t2"], ART / "t2-kill", t0)
        kt.join(timeout=120)
        assert kill_state and kill_state[0] not in (None, "done", "blocked", "failed"), \
            f"kill landed outside the run (state={kill_state})"
        matches = [f for f in (OUTBOX.iterdir() if OUTBOX.is_dir() else [])
                   if f.name == f"{r['message_id']}.txt"]
        assert r["state"] == "done" and len(matches) == 1
        print(f"t2 kill done in {time.time()-t0:.0f}s", flush=True)
    if which in ("t3", "all"):
        for p in worker_pids():
            try:
                os.kill(p, 9)
            except ProcessLookupError:
                pass
        time.sleep(3)
        for q in ("orch", "build", "verify", "test"):
            start_worker(q, {"ROUVR_INJECT": "silent_delivery"})
        t0 = time.time()
        h = await run_pack(client, keys["t3"])
        r, _ = await collect(client, h, keys["t3"], ART / "t3-fake200", t0)
        primary_id = hashlib.sha256((keys["t3"] + ":primary").encode()).hexdigest()[:16]
        assert r["state"] == "done" and r["sink"] == "fallback" and r["message_id"] != primary_id
        assert not (OUTBOX / f"{primary_id}.txt").exists()
        print(f"t3 fake200 done in {time.time()-t0:.0f}s", flush=True)
        for p in worker_pids():
            try:
                os.kill(p, 9)
            except ProcessLookupError:
                pass
        time.sleep(3)
        for q in ("orch", "build", "verify", "test"):
            start_worker(q)  # restore clean workers (stack env, no inject)
        print("workers restored clean", flush=True)
    print("LEGS GREEN", flush=True)


asyncio.run(leg(sys.argv[1] if len(sys.argv) > 1 else "all"))
