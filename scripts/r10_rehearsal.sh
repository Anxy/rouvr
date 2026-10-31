#!/usr/bin/env bash
# R10: kill/resume rehearsal. Ledger (local) + live Muse turns + scratch GitHub repo.
# Single-credential rehearsal: coder/reviewer share one model+GitHub credential under
# distinct logical principals; the reviewer credential + stage attempt-scope grant are A2 gates.
# Env in: META_MODEL_API_KEY (model key), GH_TOKEN (or `gh auth token`).
# Out: exit 0 + docs/r10-runs/<ts>/ transcript iff done + 1 PR + activity retry on attempt 1.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
R10_ROOT="${R10_ROOT:-/tmp/rouvr-r10}"
R10_TENANT="r10-$(date +%Y%m%dT%H%M%S)"  # unique tenant per run: workflow ids are REJECT_DUPLICATE
BACKEND="${WORKHUB_BACKEND:-ledger}"
if [ "$BACKEND" != "ledger" ]; then
  # shortcut: stage rehearsal not wired in this script; it only knows the local
  # ledger. Refuse rather than silently run ledger and report it as stage.
  # Upgrade: implement stage operands (scratch task, two credential files,
  # WORKHUB_BASE_URL) once the A2 grant lands — see docs/a2-gates.md.
  echo "FAIL: WORKHUB_BACKEND=$BACKEND unsupported by r10_rehearsal.sh (ledger only); see docs/a2-gates.md" >&2
  exit 2
fi
REPO="${R10_REPO:-ebiz-backend-developer/rouvr-r10-scratch}"
LEDGER_PORT="${R10_LEDGER_PORT:-8221}"
: "${META_MODEL_API_KEY:?set META_MODEL_API_KEY}"
GH_TOKEN="${GH_TOKEN:-$(gh auth token)}"
export GH_TOKEN  # consumed by the fixture writer below; never printed (set -x stays off)

log() { printf '[r10 %s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$R10_ROOT/run.log"; }
fail() { log "FAIL: $*"; exit 1; }

rm -rf "$R10_ROOT"
mkdir -p "$R10_ROOT"/specs/"$R10_TENANT" "$R10_ROOT"/{hidden-tests,workspaces,personas,logs,pids}

# ---- fixtures (no secrets echoed) ----
cat > "$R10_ROOT/personas/tenants.yaml" <<EOF
tenants:
  $R10_TENANT:
    personas:
      coder-1: {role: coder, principal: svc-r10-coder, muse_model: "",
                  credential_file: "$R10_ROOT/personas/coder.json", github_app: r10}
      reviewer-1: {role: reviewer, principal: svc-r10-reviewer, muse_model: "",
                   credential_file: "$R10_ROOT/personas/reviewer.json"}
EOF
for p in coder reviewer; do
  "$ROOT/.venv/bin/python" - "$R10_ROOT/personas/$p.json" <<'EOF'
import json, os, sys
json.dump({"meta_api_key": os.environ["META_MODEL_API_KEY"], "github_token": os.environ["GH_TOKEN"],
           "workhub_token": "ledger"}, open(sys.argv[1], "w"))
EOF
  chmod 600 "$R10_ROOT/personas/$p.json"
done
cat > "$R10_ROOT/specs/$R10_TENANT/scratch.yaml" <<EOF
repo: $REPO
base_branch: main
test_command: ["python", "-m", "pytest", "-q"]
timeout_s: 900
EOF

# ---- ledger ----
export ROUVR_LEDGER_DATABASE_URL="sqlite:///$R10_ROOT/ledger.db"
"$ROOT/.venv/bin/python" -c "
import os, uvicorn
from ledger.app import make_app
uvicorn.run(make_app(os.environ['ROUVR_LEDGER_DATABASE_URL']), host='127.0.0.1', port=$LEDGER_PORT, log_level='warning')
" >>"$R10_ROOT/logs/ledger.log" 2>&1 &
echo $! > "$R10_ROOT/pids/ledger.pid"
for _ in $(seq 1 30); do curl -sf -o /dev/null "http://127.0.0.1:$LEDGER_PORT/health" && break; sleep 1; done
# S022: partitions are mandatory — register tenant/project before any task.
curl -sf -X POST "http://127.0.0.1:$LEDGER_PORT/v1/tenants" -H 'Content-Type: application/json' \
  -d "{\"tenant\":\"$R10_TENANT\"}" > /dev/null
curl -sf -X POST "http://127.0.0.1:$LEDGER_PORT/v1/projects" -H 'Content-Type: application/json' \
  -d "{\"tenant\":\"$R10_TENANT\",\"project\":\"scratch\"}" > /dev/null
TID=$(curl -sf -X POST "http://127.0.0.1:$LEDGER_PORT/v1/tasks" -H 'Content-Type: application/json' \
  -d "{\"title\":\"[R10] punctuated greet\",\"intent\":\"rehearsal\",\"tenant\":\"$R10_TENANT\",\"project\":\"scratch\"}" | "$ROOT/.venv/bin/python" -c "import json,sys; print(json.load(sys.stdin)['id'])")
[ "$TID" = "1" ] || fail "expected fresh ledger task 1, got $TID"
curl -sf -X POST "http://127.0.0.1:$LEDGER_PORT/v1/tasks/$TID/contracts" -H 'Content-Type: application/json' \
  -d '{"project_id":"scratch","description":"Extend greet(name) with an optional punct parameter (default \"\") appended to the greeting.","acceptance_criteria":["greet() appends the punct parameter to the greeting","repo test suite passes"]}' > /dev/null
HIDDEN='def test_c1_greet_punct():\n    from greet import greet\n    assert greet("sam", punct="!") == "hi sam!"\n    assert greet("sam") == "hi sam"\n'
PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -c "
from pathlib import Path
from rouvr.hiddentests import write_hidden_suite
write_hidden_suite(Path('$R10_ROOT/hidden-tests'), task_id='$TID', files={'test_hidden.py': '''$HIDDEN'''}, created_by='test_author')
print('hidden suite locked')
" | tee -a "$R10_ROOT/run.log"

# ---- workers ----
export WORKHUB_BACKEND=ledger WORKHUB_BASE_URL="http://127.0.0.1:$LEDGER_PORT" ROUVR_TENANT_ID="$R10_TENANT" \
  ROUVR_PROJECT_ID=scratch PERSONA_SOURCE=yaml PERSONA_REGISTRY_PATH="$R10_ROOT/personas/tenants.yaml" \
  SPEC_ROOT="$R10_ROOT/specs" WORKSPACE_ROOT="$R10_ROOT/workspaces" HIDDEN_TESTS_ROOT="$R10_ROOT/hidden-tests" \
  TEMPORAL_ADDRESS=127.0.0.1:7233 TEMPORAL_NAMESPACE=default MUSE_BIN=muse ORCHESTRATOR_TOKEN=ledger \
  LEASE_TTL_S=180 HEARTBEAT_S=30 MAX_REWORK=2 PYTHONPATH="$ROOT"
for q in orch build test verify; do
  "$ROOT/.venv/bin/python" -m rouvr.worker "$q" >>"$R10_ROOT/logs/$q.log" 2>&1 &
  echo $! > "$R10_ROOT/pids/$q.pid"
done
sleep 8  # let pollers attach
for q in orch build test verify; do kill -0 "$(cat "$R10_ROOT/pids/$q.pid")" || fail "$q worker died on boot (see logs/$q.log)"; done
log "workers up"

cleanup() { for p in "$R10_ROOT"/pids/*.pid; do kill -9 "$(cat "$p")" 2>/dev/null || true; done; }
trap cleanup EXIT

# ---- start + kill mid-build ----
PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" - <<'EOF' 2>&1 | tail -n 1
import asyncio
from temporalio.client import Client
async def main():
    c = await Client.connect("127.0.0.1:7233", namespace="default")
    n = 0
    async for w in c.list_workflows('ExecutionStatus="Running"'):
        await c.get_workflow_handle(w.id, run_id=w.run_id).terminate(reason="r10 fresh slate")
        n += 1
    print(f"terminated {n} stale workflow(s)")
asyncio.run(main())
EOF
WID=$(PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -m cli.rouvr_cli start --task-number "$TID" --requested-by r10)
STATUS=$(PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" - "$WID" <<'EOF'
import asyncio, sys
from temporalio.client import Client
async def main():
    h = (await Client.connect("127.0.0.1:7233", namespace="default")).get_workflow_handle(sys.argv[1])
    print((await h.describe()).status.name)
asyncio.run(main())
EOF
)
[ "$STATUS" = "RUNNING" ] || fail "workflow $WID not running (status=$STATUS): id reuse?"
log "started $WID ($STATUS)"
# The launcher writes .rouvr-prompt-<sid>.md immediately before spawning the turn;
# that file (not the process name — `muse` is a wrapper that re-execs) is the trigger.
for _ in $(seq 1 600); do ls "$R10_ROOT/workspaces"/.rouvr-prompt-*.md >/dev/null 2>&1 && break; sleep 2; done
ls "$R10_ROOT/workspaces"/.rouvr-prompt-*.md >/dev/null 2>&1 || fail "no muse turn observed in 20m"
log "turn spawned; waiting 45s to land the kill mid-turn"
sleep 45
BPID=$(cat "$R10_ROOT/pids/build.pid")
log "muse turn in flight; kill -9 build worker $BPID"
kill -9 "$BPID"
sleep 5
kill -0 "$BPID" 2>/dev/null && fail "build worker survived kill -9"
"$ROOT/.venv/bin/python" -m rouvr.worker build >>"$R10_ROOT/logs/build.log" 2>&1 &
echo $! > "$R10_ROOT/pids/build.pid"
log "build worker restarted as $(cat "$R10_ROOT/pids/build.pid")"

# ---- wait for close ----
for _ in $(seq 1 900); do
  STATE=$(curl -sf "http://127.0.0.1:$LEDGER_PORT/v1/tasks/$TID" 2>/dev/null \
    | "$ROOT/.venv/bin/python" -c "import json,sys; print(json.load(sys.stdin)['state'])" 2>/dev/null \
    || echo __poll_error__)
  if [ "$STATE" = "__poll_error__" ]; then log "ledger poll failed, retrying"; sleep 10; continue; fi
  [ "$STATE" = "done" ] && break
  [ "$STATE" = "blocked" ] || [ "$STATE" = "failed" ] && fail "task landed in $STATE"
  sleep 10
done
[ "$STATE" = "done" ] || fail "timeout waiting for done (state=$STATE)"

# ---- asserts ----
PR_COUNT=$(gh pr list -R "$REPO" --state open --json headRefName -q '[.[] | select(.headRefName == "rouvr/1/a1")] | length')
HANDOFF=$(curl -sf "http://127.0.0.1:$LEDGER_PORT/v1/tasks/$TID" | "$ROOT/.venv/bin/python" -c "
import json, sys
n = sum(1 for r in json.load(sys.stdin)['evidence'] if r['kind'] == 'temporal_handoff')
print(n)")
ATTEMPTS=$("$ROOT/.venv/bin/python" -c "
import sqlite3
c = sqlite3.connect('$R10_ROOT/ledger.db')
print(c.execute(\"SELECT COUNT(*) FROM attempts WHERE task_id=1 AND attempt_no=1\").fetchone()[0])")
MAX_ATTEMPT=$(PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" - "$WID" <<'EOF'
import asyncio, sys
from temporalio.client import Client
async def main():
    h = (await Client.connect("127.0.0.1:7233", namespace="default")).get_workflow_handle(sys.argv[1])
    hist = await h.fetch_history()
    sched, best = set(), 0
    for ev in hist.events:
        if ev.HasField("activity_task_scheduled_event_attributes"):
            if ev.activity_task_scheduled_event_attributes.activity_type.name == "assign_task":
                sched.add(ev.event_id)
        elif ev.HasField("activity_task_started_event_attributes"):
            s = ev.activity_task_started_event_attributes
            if s.scheduled_event_id in sched:
                best = max(best, s.attempt)
    print(best)
asyncio.run(main())
EOF
)
log "PRs on rouvr/1/a1=$PR_COUNT handoff_rows=$HANDOFF attempt1_rows=$ATTEMPTS max_assign_attempt=$MAX_ATTEMPT state=$STATE"
[ "$PR_COUNT" = "1" ] || fail "expected 1 PR, got $PR_COUNT"
[ "$HANDOFF" = "1" ] || fail "expected 1 handoff row, got $HANDOFF"
[ "$ATTEMPTS" = "1" ] || fail "expected 1 attempt-1 row, got $ATTEMPTS"
[ "$MAX_ATTEMPT" -ge 2 ] || fail "expected assign_task retry (attempt>=2), got $MAX_ATTEMPT"

# ---- record ----
TS=$(date +%Y%m%dT%H%M); DEST="$ROOT/docs/r10-runs/$TS"
mkdir -p "$DEST"
for f in "$R10_ROOT"/logs/*.log "$R10_ROOT/run.log"; do
  sed -E 's#(gh[op]_[A-Za-z0-9_]+|ghu_[A-Za-z0-9_]+|LLM_[A-Za-z0-9_]+|Bearer [A-Za-z0-9._~-]+)#<redacted>#g' "$f" > "$DEST/$(basename "$f")"
done
PR_URL=$(gh pr list -R "$REPO" --head rouvr/1/a1 --state all --json url -q '.[0].url')
cat > "$DEST/README.md" <<EOF
# R10 rehearsal $TS — PASS
- Workflow: $WID (assign_task completed on activity attempt $MAX_ATTEMPT: retry on attempt 1 after kill -9)
- PR: $PR_URL (exactly 1 on rouvr/1/a1)
- Ledger task 1: done; handoff rows 1; attempt-1 rows 1
- Backend: local ledger + live Muse turns + $REPO
- Single-credential rehearsal (see scripts/r10_rehearsal.sh header for the A2 gates).
- Logs: this directory (secrets redacted).
EOF
log "R10 PASS — transcript in docs/r10-runs/$TS"
