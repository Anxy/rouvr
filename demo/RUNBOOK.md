# Demo runbook — morning-report ops pack on the supervisor stack

Verified 2026-11-01 from a clean checkout (clone → setup → up → smoke →
down). Times below are measured on this host.

## One-command clean bring-up (~4 min first time, ~1 min warm)

```bash
git clone https://github.com/Anxy/rouvr.git rouvr-demo && cd rouvr-demo
git checkout rouvr-development
make setup PY=python3.12      # needs Python 3.12/3.13 (3.14 breaks tiktoken)
ROUVR_LAUNCH_WINDOWS=0 ./scripts/rouvr --no-chat
./scripts/rouvr status        # temporal up, ledger ok, MCP 1 proc, workers 4/4
```

Requires: Docker daemon running, free ports 5433 (Postgres), 6380 (Redis),
7233 (Temporal), 8221 (ledger), 8231 (MCP). (8081 serves nothing unless you
start a Temporal UI yourself.) `muse` CLI on PATH (coder turns), Meta creds
in persona files for live-model legs (stub provider otherwise).

Live-model legs need the provider env on every worker (test_author, decompose,
contract compile, ops judge all call the Meta API through it). Create it from
the Owner key file without ever printing the key:

```bash
umask 077
.venv/bin/python - <<'EOF'
key = open(".secrets/rouvr/meta.env").read().split("=", 1)[1].strip()
body = ("export ROUVR_MODEL_PROVIDER=meta\n"
        "export META_MODEL_API_BASE_URL=https://api.meta.ai/v1\n"
        "export META_MODEL_NAME=muse-spark-1.3\n"
        f"export META_API_KEY={key}\n")
open("/tmp/rouvr-supervisor/meta-provider-env.sh", "w").write(body)
import os; os.chmod("/tmp/rouvr-supervisor/meta-provider-env.sh", 0o600)
EOF
# After changing worker code or env, restart workers so they pick it up:
for q in orch build test verify; do kill $(cat /tmp/rouvr-supervisor/pids/worker-$q.pid); done
for q in orch build test verify; do setsid -f scripts/rouvr-worker-start.sh "$q" </dev/null >>/tmp/rouvr-supervisor/logs/$q.log 2>&1; done
# Also build the clean-room test image once per host:
docker build -f testrunner.Dockerfile -t rouvr-runner:py313-p9 .
```

Headless coder/reviewer turns run `muse exec` with `--disable-approval
--disable-sandbox` (no approver exists on a worker; this host's bwrap cannot
set up loopback). Boundary is workspace cwd/HOME + prompt + step cap.
Per-task git grants live beside the workspace tree (`<ops>/grants/`, 0600,
revoked at close) — never inside a checkout — so a turn cannot self-grant
from a sibling directory.

## Demo order

Every leg below runs from the committed driver (no /tmp scripts):
`.venv/bin/python demo/rehearsal/run_tick.py [t1|t2|t3|all]`.

1. **Instruction** — the morning-report pack text (tasks closed, parks,
   escalations; publish; ledger healthy; escalate on failure).
2. **Contract** — ops contract merged from defaults + instruction; invalid
   JSON refused, revoked keys park pre-claim.
3. **Execute** (`t1`) — create the standing grant (time-boxed,
   owner-approved), fire one tick through OpsTaskWorkflow.
4. **Kill** (`t2`) — the driver SIGKILLs all workers mid-run and restarts
   them ~8s later; Temporal resumes; exactly one outbox file per message id
   (no double-send). Run only with no coding task active.
5. **Recover** — tick ends done; receipt recorded.
6. **Fake-200 caught** (`t3`) — the driver restarts workers with
   `ROUVR_INJECT=silent_delivery`: the "200 but not delivered" lie is caught
   by read-back; delivery falls back and the receipt carries the fallback id.
   Workers are restored clean afterwards.
7. **Receipt** — exactly one `ops_receipt` evidence row per run.
8. **Digest** — "Done. No action needed." (or `blocked` + escalation record
   when the grant lapses).

The `reviewer` evidence on every tick carries the Meta judge step: the model
re-verifies the delivered report against the ledger figures (`judge.verdict`
pass/fail + reason, `model: meta`). A wrong report fails even when the
deterministic read-back passed — see `demo/rehearsal/2026-11-01/liveproof/judge-proof.json`.

9. **Live coding leg** (separate task, same stack) — supervisor creates the
task with sealed hidden tests (live test_author), live coder implements +
pushes + opens a PR, hidden suite runs in the clean-room image, an
independent live reviewer approves with per-criterion notes, close emits a
signed receipt (digest + signature + key id) bound to the reviewed head SHA.
Reproduce from the repo (needs live-model env, a scratch GitHub repo, and a
few minutes per task — coder/reviewer turns dominate):
`.venv/bin/python demo/rehearsal/live_coding.py create|start|watch|judge|usage`.
The git grant uses `gh auth token` as the Owner-approval stand-in for demo
runs (0600, beside the workspace tree, revoked at close).
Proof tasks 13/14/15: `demo/rehearsal/2026-11-01/liveproof/task{13,14,15}-summary.json`,
PRs [#4](https://github.com/Anxy/rouvr-liveproof/pull/4),
[#5](https://github.com/Anxy/rouvr-liveproof/pull/5),
[#6](https://github.com/Anxy/rouvr-liveproof/pull/6), token usage in
`token-usage.json` (model muse-spark-1.3; method noted inside; cost n/a —
no rate card for the demo key).

## Reset between takes

> Never on the shared demo host while the stack runs: this wipes the live
> ledger, outbox, Temporal data, and every proof task above. Reset only a
> private stack you brought up yourself.

```bash
./scripts/rouvr down
rm -f /tmp/rouvr-supervisor/ledger.db* demo/.outbox/*
ROUVR_LAUNCH_WINDOWS=0 ./scripts/rouvr --no-chat
```

Fresh ledger + fresh outbox; new run keys keep takes independent anyway
(run_key idempotency returns the old receipt on repeat).

`./scripts/rouvr status` must run from the checkout that owns the stack:
pidfiles are checkout-scoped, so status from another checkout reports stale
pids as its own.

## Timed run-throughs (2026-11-01, live Meta judge + contract compile)

All on the supervisor stack with `model: meta` (muse-spark-1.3). Artifacts under
`demo/rehearsal/2026-11-01/`.

- **t1 clean** — 7.4s wall. Done, primary sink, one `ops_receipt`, live judge
  pass ("All figures match ledger values."). (`t1-clean/`)
- **t2 kill** — 486s wall (includes ~3 min with all workers dead). Orch workers
  SIGKILLed mid-run at task state `open`; Temporal resumed on restart; done,
  primary sink, exactly one outbox file for the message id, live judge pass.
  (`t2-kill/`)
- **t3 fake-200** — 13.9s wall. `ROUVR_INJECT=silent_delivery`: primary
  reviewer evidence records deterministic fail + judge fail ("Report is empty
  and missing all ledger figures."); delivery falls back; fallback reviewer
  evidence records pass/pass ("All figures match ledger."). (`t3-fake200/`)
- **cron proof** — tick A under a live short grant ends done with read-back
  receipt; tick B after expiry ends blocked with an escalation record.
  (`cron/`)
- **live coding tasks 13/14/15** — 141s, 145s, 181s ledger wall
  (created→done; earlier "~30–40 min" notes measured a stale watch timer —
  corrected 2026-11-01). Sealed hidden suite, PR, hidden-tests pass, live
  reviewer approve with per-criterion notes, signed receipt bound to head
  SHA. (`liveproof/`)

An earlier t1 attempt is kept as failure evidence (`t1-blocked-contract/`):
the live model returned a verbose non-schema contract and the old merge broke
validation (E_CONTRACT). The merge now keeps safe defaults on mistyped keys.

## Slow-model fallback

Coder/reviewer turns dominate wall time. If the model is slow: run the
ops-only path (no coder turns — compile/verify use the stub provider),
show the kill + fake-200 faults there, and present one pre-recorded
coding-task transcript for the review loop. Never fake a live receipt.
