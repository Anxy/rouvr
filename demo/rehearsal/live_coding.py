"""Live-model coding-task proof driver: supervisor-created task, sealed hidden
suite (live test_author), live coder + push + PR, clean-room hidden suite,
independent live reviewer, close with signed receipt. Plus the Meta judge
reject-proof and token-usage harvest.

Usage (stack already up with live-model env — see demo/RUNBOOK.md):
    .venv/bin/python demo/rehearsal/live_coding.py create [--title T --desc D --criteria-json C]
    .venv/bin/python demo/rehearsal/live_coding.py start --task-id N
    .venv/bin/python demo/rehearsal/live_coding.py watch --task-id N
    .venv/bin/python demo/rehearsal/live_coding.py judge
    .venv/bin/python demo/rehearsal/live_coding.py usage

The git grant needs an Owner-authorized token: the driver uses `gh auth token`
(the supervisor approval stand-in for demo runs), writes it 0600 beside the
workspace tree (see rouvr/gitops.py::grant_path), and the workflow revokes it
at close. Never prints secrets.
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))
OPS = Path(os.environ.get("ROUVR_OPS_DIR") or str(ROOT / ".data"))


def load_stack_env():
    import shlex
    for f in (OPS / "env.sh", OPS / "meta-provider-env.sh"):
        if not f.exists():
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                for tok in shlex.split(line[len("export "):]):
                    k, _, v = tok.partition("=")
                    # $(...) needs a shell; resolve the two known dynamic forms
                    if "$(" in v:
                        if k == "ROUVR_LEDGER_TOKEN":
                            v = (OPS / "api-token").read_text().strip()
                        else:
                            continue
                    os.environ[k] = v


load_stack_env()
ART = ROOT / "demo" / "rehearsal" / datetime.now(timezone.utc).strftime("%Y-%m-%d") / "liveproof"
ART.mkdir(parents=True, exist_ok=True)
STATE = ART / "state.json"

TENANT, PROJECT = "supervisor", "liveproof"
DEFAULT_TITLE = "liveproof: implement parse_duration"
DEFAULT_DESC = ("Implement parse_duration(s) in durations.py so it parses duration "
                "strings like '1h30m' into integer seconds.")
DEFAULT_CRITERIA = [
    "parse_duration('1h30m') returns 5400 (compound values sum)",
    "units s, m, h, d, w are supported case-insensitively ('2H' == 7200)",
    "unknown units raise ValueError whose message names the offending unit",
    "empty or blank input raises ValueError",
]


def save(**kw):
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    st.update(kw)
    STATE.write_text(json.dumps(st, indent=2))
    return st


def phase_create(title, desc, criteria):
    import httpx
    c = httpx.Client(base_url=os.environ["WORKHUB_BASE_URL"], timeout=15,
                     headers={"X-Rouvr-Token": os.environ.get("ROUVR_LEDGER_TOKEN", "")})
    c.post("/v1/tenants", json={"tenant": TENANT}).raise_for_status()
    c.post("/v1/projects", json={"tenant": TENANT, "project": PROJECT}).raise_for_status()
    from rouvr.mcp_server import workhub_create_task
    t0 = time.time()
    task = workhub_create_task(title, desc, criteria, TENANT, PROJECT)
    dt = time.time() - t0
    print(f"task created: {task['id']} ({dt:.0f}s, incl live test_author+decompose)", flush=True)
    from rouvr.hiddentests import fetch_hidden_suite
    hidden_dir, manifest = fetch_hidden_suite(
        Path(os.environ["HIDDEN_TESTS_ROOT"]), str(task["id"]))
    files = sorted(p.name for p in hidden_dir.iterdir() if p.name != "MANIFEST.json")
    print("hidden suite sealed:", files, "created_by:", manifest.created_by, flush=True)
    save(task_id=task["id"], create_s=round(dt, 1), hidden_files=files)
    from rouvr.gitops import grant_path
    gh = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True,
                        timeout=30).stdout.strip()
    gp = grant_path(Path(os.environ["WORKSPACE_ROOT"]), str(task["id"]))
    gp.parent.mkdir(parents=True, exist_ok=True)
    gp.write_text(json.dumps({"github_token": gh}))
    os.chmod(gp, 0o600)
    print("git grant placed (0600):", gp, flush=True)
    return task["id"]


async def phase_start(task_id):
    from rouvr.starter import run_start
    wid = await run_start(task_number=int(task_id), requested_by="owner")
    print("workflow:", wid, flush=True)
    save(workflow_id=wid)
    return wid


async def phase_watch(task_id, timeout_s=2700):
    import httpx
    from temporalio.client import Client
    c = httpx.Client(base_url=os.environ["WORKHUB_BASE_URL"], timeout=30,
                     headers={"X-Rouvr-Token": os.environ.get("ROUVR_LEDGER_TOKEN", "")})
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    t0 = time.time()
    save(watch_t0=t0)  # per-watch start: never reuse a stale t0 across tasks
    tc = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7233"),
                              namespace=os.environ.get("TEMPORAL_NAMESPACE", "default"))
    h = tc.get_workflow_handle(st["workflow_id"])
    last = ""
    while time.time() - t0 < timeout_s:
        try:
            d = await h.describe()
            wstatus = str(d.status)
        except Exception as e:  # noqa: BLE001
            wstatus = f"describe-err {type(e).__name__}"
        t = c.get(f"/v1/tasks/{task_id}").json()
        line = f"t+{time.time()-t0:.0f}s wf={wstatus} task={t.get('state')}"
        if line != last:
            print(line, flush=True)
            last = line
        if t.get("state") in ("done", "blocked", "failed"):
            return t
        await asyncio.sleep(20)
    raise TimeoutError("watch timed out")


def harvest_usage(workspaces_root):
    """Per-turn model usage from workspace-local muse session logs.

    Method: input = max context-input per turn (context is reused across
    calls, so summing overcounts); output/reasoning = summed per model call
    (each completion is new tokens). Cost n/a: no rate card for the demo key.
    """
    logs = subprocess.run(["find", workspaces_root, "-name", "session.jsonl"],
                          capture_output=True, text=True).stdout.split()
    rows = []
    for log in sorted(logs):
        if "/subagent/" in log:
            continue
        mx, sout, srs, n = 0, 0, 0, 0
        models = set()
        try:
            for line in open(log, errors="replace"):
                if '"input_tokens"' not in line and '"model_id"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                stack = [e]
                while stack:
                    o = stack.pop()
                    if isinstance(o, dict):
                        if isinstance(o.get("input_tokens"), int):
                            mx = max(mx, o["input_tokens"])
                            sout += o.get("output_tokens", 0)
                            srs += o.get("reasoning_tokens", 0)
                            n += 1
                        if isinstance(o.get("model_id"), str):
                            models.add(o["model_id"])
                        stack.extend(o.values())
                    elif isinstance(o, list):
                        stack.extend(o)
        except OSError:
            continue
        rows.append({"workspace": log.split("/.local/")[0].split("/")[-1],
                     "session": log.split("/")[-2][:8], "model_calls": n,
                     "input_max_ctx": mx, "output_sum": sout,
                     "reasoning_sum": srs, "models": sorted(models)})
    return rows


async def phase_judge():
    """Real judge code + live provider: wrong report must fail, right must pass."""
    from backend.rouvr_models.providers import get_provider
    from rouvr.ops import judge_ops_report
    p = get_provider()
    assert p.name == "meta", p.name
    import httpx
    c = httpx.Client(base_url=os.environ["WORKHUB_BASE_URL"], timeout=30,
                     headers={"X-Rouvr-Token": os.environ.get("ROUVR_LEDGER_TOKEN", "")})
    checked_at = datetime.now(timezone.utc).isoformat()
    items = c.get("/v1/tasks").json()["items"]
    figures = {s: sum(1 for t in items if t["state"] == s) for s in ("done", "blocked", "failed")}
    task_states = sorted((t["id"], t["title"], t["state"]) for t in items)
    good = "Lane report\n" + json.dumps(figures, sort_keys=True)
    bad = "Lane report\n" + json.dumps(
        {"done": figures["done"] + 1, "blocked": figures["blocked"], "failed": figures["failed"]},
        sort_keys=True)
    t0 = time.time()
    r_good = judge_ops_report(good, figures, chat=p.chat)
    r_bad = judge_ops_report(bad, figures, chat=p.chat)
    dt = time.time() - t0
    print("judge good:", r_good["verdict"], "| judge wrong:", r_bad["verdict"],
          f"({dt:.0f}s, model {p.model})", flush=True)
    assert r_good["verdict"] == "pass", r_good
    assert r_bad["verdict"] == "fail", r_bad
    (ART / "judge-proof.json").write_text(json.dumps(
        {"checked_at": checked_at, "figures": figures, "task_states": task_states,
         "good": r_good, "bad": r_bad, "model": p.model, "seconds": round(dt, 1)},
        indent=2))
    save(judge="green")


def main():
    ap = argparse.ArgumentParser(prog="live_coding.py")
    ap.add_argument("phase", choices=["create", "start", "watch", "judge", "usage"])
    ap.add_argument("--task-id", default=None)
    ap.add_argument("--title", default=DEFAULT_TITLE)
    ap.add_argument("--desc", default=DEFAULT_DESC)
    ap.add_argument("--criteria-json", default=None)
    a = ap.parse_args()
    tid = a.task_id
    if tid is None and STATE.exists():
        tid = str(json.loads(STATE.read_text()).get("task_id") or "") or None
    if a.phase == "create":
        criteria = json.loads(a.criteria_json) if a.criteria_json else DEFAULT_CRITERIA
        print(phase_create(a.title, a.desc, criteria))
    elif a.phase == "start":
        print(asyncio.run(phase_start(tid)))
    elif a.phase == "watch":
        t = asyncio.run(phase_watch(tid))
        (ART / "final-task.json").write_text(json.dumps(t, indent=2))
        print("terminal:", t.get("state"), flush=True)
    elif a.phase == "judge":
        asyncio.run(phase_judge())
    elif a.phase == "usage":
        rows = harvest_usage(os.environ["WORKSPACE_ROOT"])
        (ART / "token-usage.json").write_text(json.dumps(rows, indent=2))
        for r in rows:
            print(r["workspace"], r["session"], f"calls={r['model_calls']}",
                  "ctx:", r["input_max_ctx"], "out:", r["output_sum"], flush=True)


main()
