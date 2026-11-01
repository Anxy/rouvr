# Demo rehearsal 2026-11-01 — morning-report ops pack + live coding, live stack

Supervisor stack on 127.0.0.1 (Temporal :7233, ledger :8221, MCP :8231,
4/4 workers). All runs below are verifier-produced artifacts, never narrative.

## Ops legs (driver: `demo/rehearsal/run_tick.py [t1|t2|t3|all]`)

| Run | Fault | Result | Receipt | Outbox | History |
|---|---|---|---|---|---|
| r1-clean | none | done / primary | 1 ops_receipt, primary id | 1 file, id matches | history.json |
| r2-kill | orch workers SIGKILL mid-run, restarted | done / primary | 1 ops_receipt | exactly 1 file for the run id (no double-send) | history.json |
| r3-fake200 | ROUVR_INJECT=silent_delivery on workers | done / fallback | 1 ops_receipt, fallback id, lied-about primary id absent and unwritten | fallback file only | history.json |
| t1-clean | none (live Meta judge + contract compile) | done / primary, 7.4s | 1 ops_receipt | 1 file | history.json |
| t2-kill | all workers SIGKILL at task state open, restarted | done / primary | 1 ops_receipt | exactly 1 file for the run id | history.json |
| t3-fake200 | ROUVR_INJECT=silent_delivery (live judge) | done / fallback, 13.9s | 1 ops_receipt, fallback id | fallback file only | history.json |

Each dir holds result.json (elapsed_s), task.json (ledger task + evidence),
receipts.json, outbox.txt, judge.json (reviewer evidence with the Meta judge
verdict), history.json (Temporal export).

`t1-blocked-contract/` is kept failure evidence: the live model returned a
verbose non-schema contract and the old merge broke validation (E_CONTRACT);
the merge now keeps safe defaults on mistyped keys (regression-tested).

## Live coding legs (driver: `demo/rehearsal/live_coding.py`)

Supervisor-created tasks with sealed hidden suites (live test_author), live
coder + push + PR, clean-room hidden suite, independent live reviewer, signed
close receipt bound to the reviewed head SHA. See `liveproof/`:
task13/14/15-summary.json, judge-proof.json (wrong report fails), token-usage.json.

## Cron proof (`cron/`)

Scheduled tick A under a live short grant ends done with a read-back receipt;
tick B after grant expiry ends blocked with an escalation record.

## Gaps this rehearsal found and fixed

- `run_schedule_ops` and the ops activities omitted the ledger token
  (grant reads + all ops ledger calls 401d): token now attached.
- Workers never learned the live ledger URL (ops leg fell back to a
  local sqlite DB): `ROUVR_LEDGER_URL` now exported by `scripts/rouvr`.
- Live-model legs: headless exec flags, governed contract merge, gitops env
  merge, test-runner image, grant relocation out of the workspace tree.
