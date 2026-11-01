# Demo runbook — morning-report ops pack on the supervisor stack

Verified 2026-11-01 from a clean checkout (clone → setup → up → smoke →
down). Times below are measured on this host.

## One-command clean bring-up (~4 min first time, ~1 min warm)

```bash
git clone <repo> rouvr-demo && cd rouvr-demo
git checkout rouvr-development
make setup PY=python3.12      # needs Python 3.12/3.13 (3.14 breaks tiktoken)
ROUVR_LAUNCH_WINDOWS=0 ./scripts/rouvr --no-chat
./scripts/rouvr status        # temporal up, ledger ok, MCP 1 proc, workers 4/4
```

Requires: Docker daemon running, ports 5432/6379/7233/8080/8221/8231 free,
`muse` CLI on PATH (coder turns), Meta creds in persona files for live-model
legs (stub provider otherwise).

Live-model legs need the provider env on every worker (test_author, decompose,
contract compile, ops judge all call the Meta API through it):

```bash
# /tmp/rouvr-supervisor/meta-provider-env.sh is 0600 and holds
# ROUVR_MODEL_PROVIDER=meta, META_MODEL_API_BASE_URL, META_MODEL_NAME,
# META_MODEL_API_KEY (bridged from .secrets/rouvr/meta.env; the preset reads
# META_MODEL_API_KEY while the Owner file carries META_API_KEY).
# After changing worker code or env, restart workers so they pick it up:
for q in orch build test verify; do kill $(cat /tmp/rouvr-supervisor/pids/worker-$q.pid); done
for q in orch build test verify; do setsid -f /tmp/rouvr-worker-start.sh "$q" </dev/null >>/tmp/rouvr-supervisor/logs/$q.log 2>&1; done
# Also build the clean-room test image once per host:
docker build -f testrunner.Dockerfile -t rouvr-runner:py313-p9 .
```

Headless coder/reviewer turns run `muse exec` with `--disable-approval
--disable-sandbox` (no approver exists on a worker; this host's bwrap cannot
set up loopback). Boundary is workspace cwd/HOME + prompt + step cap.

## Demo order

1. **Instruction** — the morning-report pack text (tasks closed, parks,
   escalations; publish; ledger healthy; escalate on failure).
2. **Contract** — ops contract merged from defaults + instruction; invalid
   JSON refused, revoked keys park pre-claim.
3. **Execute** — create the standing grant (time-boxed, owner-approved),
   register the schedule (`rouvr_schedule_ops`), tick fires OpsTaskWorkflow.
4. **Kill** — SIGKILL the orch worker mid-run; restart; workflow resumes,
   exactly one outbox file per message id (no double-send).
5. **Recover** — tick ends done; receipt recorded.
6. **Fake-200 caught** — `ROUVR_INJECT=silent_delivery` on workers: the
   "200 but not delivered" lie is caught by read-back; delivery falls back
   and the receipt carries the fallback id.
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
Proof tasks 13/14/15: `demo/rehearsal/2026-11-01/liveproof/task{13,14,15}-summary.json`,
PRs [#4](https://github.com/Anxy/rouvr-liveproof/pull/4),
[#5](https://github.com/Anxy/rouvr-liveproof/pull/5),
[#6](https://github.com/Anxy/rouvr-liveproof/pull/6), token usage in
`token-usage.json` (model muse-spark-1.3; method noted inside; cost n/a —
no rate card for the demo key).

## Reset between takes

```bash
./scripts/rouvr down
rm -f /tmp/rouvr-supervisor/ledger.db* demo/.outbox/*
ROUVR_LAUNCH_WINDOWS=0 ./scripts/rouvr --no-chat
```

Fresh ledger + fresh outbox; new run keys keep takes independent anyway
(run_key idempotency returns the old receipt on repeat).

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
- **live coding tasks 13/14/15** — ~30–40 min each wall (coder + reviewer
  turns dominate). Sealed hidden suite, PR, hidden-tests pass, live reviewer
  approve with per-criterion notes, signed receipt bound to head SHA.
  (`liveproof/`)

An earlier t1 attempt is kept as failure evidence (`t1-blocked-contract/`):
the live model returned a verbose non-schema contract and the old merge broke
validation (E_CONTRACT). The merge now keeps safe defaults on mistyped keys.

## Slow-model fallback

Coder/reviewer turns dominate wall time. If the model is slow: run the
ops-only path (no coder turns — compile/verify use the stub provider),
show the kill + fake-200 faults there, and present one pre-recorded
coding-task transcript for the review loop. Never fake a live receipt.
