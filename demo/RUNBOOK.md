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

## Reset between takes

```bash
./scripts/rouvr down
rm -f /tmp/rouvr-supervisor/ledger.db* demo/.outbox/*
ROUVR_LAUNCH_WINDOWS=0 ./scripts/rouvr --no-chat
```

Fresh ledger + fresh outbox; new run keys keep takes independent anyway
(run_key idempotency returns the old receipt on repeat).

## Slow-model fallback

Coder/reviewer turns dominate wall time. If the model is slow: run the
ops-only path (no coder turns — compile/verify use the stub provider),
show the kill + fake-200 faults there, and present one pre-recorded
coding-task transcript for the review loop. Never fake a live receipt.
