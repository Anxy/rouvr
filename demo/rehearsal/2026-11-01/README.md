# Demo rehearsal 2026-11-01 — morning-report ops pack, live stack

Clean checkout: /tmp/rouvr-rehearse at 5748919, `make setup PY=python3.12`,
`./scripts/rouvr --no-chat` (Temporal, ledger :8221, MCP :8231, 4/4 workers).
Standing grant `ops:morning-report`, 2h expiry, owner-approved. Schedule
`ops-morning-report-rehearsal-*` registered via `run_schedule_ops`
(action: OpsTaskWorkflow, SKIP overlap).

## Runs (verifier-produced artifacts per run dir)

| Run | Fault | Result | Receipt | Outbox | History |
|---|---|---|---|---|---|
| r1-clean | none | done / primary | 1 ops_receipt, primary id | 1 file, id matches | history.json |
| r2-kill | orch workers SIGKILL mid-run, restarted | done / primary | 1 ops_receipt | exactly 1 file for the run id (no double-send) | history.json |
| r3-fake200 | ROUVR_INJECT=silent_delivery on workers | done / fallback | 1 ops_receipt, fallback id, lied-about primary id absent and unwritten | fallback file only | history.json |

Each dir holds result.json, task.json (ledger task + evidence),
receipts.json, outbox.txt, history.json (Temporal export).

## Gaps this rehearsal found and fixed

- `run_schedule_ops` and the ops activities omitted the ledger token
  (grant reads + all ops ledger calls 401d): token now attached.
- Workers never learned the live ledger URL (ops leg fell back to a
  local sqlite DB): `ROUVR_LEDGER_URL` now exported by `scripts/rouvr`.

## Not covered (parked)

- Coding-task loop with live coder/reviewer turns (needs Meta creds).
- Cron-tick firing (schedule registered; ticks are cron-driven).
- Muse transcripts: n/a for the ops path (no model turns).
