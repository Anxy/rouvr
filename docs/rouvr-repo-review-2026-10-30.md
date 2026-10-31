# Rouvr repo review: rouvr-development @ 4e99dea (30 Oct 2026, IST)

> SUPERSEDED by [rouvr-repo-review-2026-10-31.md](rouvr-repo-review-2026-10-31.md)
> (HEAD `3ab4af0`): S001–S019 are PASS there; read the 10-31 review instead.
> This file is kept as history only.

Read-only review. Repo is public and was cloned to /workspace/rouvr-review. I ran no stack, no Temporal and no muse. I ran the unit tests once in an isolated venv with `env -i` (no secrets) and ROUVR_MODEL_PROVIDER=stub: `pytest tests ledger demo` → **133 passed**. This needed `mcp`, which is missing from requirements.txt.
Labels: [V] means verified in the code or by a test. [U] means unverified at runtime, i.e. inferred statically.

## Architecture in brief
- **Launch.** `bin/rouvr` → `scripts/rouvr`. This brings up Temporal in Docker, the ledger on :8221, the MCP front door on :8231 and 4 workers (orch/coder/test/reviewer). It then opens a muse supervisor chat using SUPERVISOR.md and installs the bundled `rouvr-workhub` plugin.
- **WorkHub-lite.** `ledger/app.py` plus `schema.sql` is FastAPI on SQLite by default (`.rouvr/ledger.db`; it also runs on Postgres). It has a token header and a state machine (TRANSITIONS, app.py:19). It refuses `done` without a contract and evidence (app.py:228).
- **TaskWorkflow** (rouvr/workflows.py):
  1. verify_binding (contract_rev + SELF_REVIEW)
  2. assign_task (coder muse turn, lease/fence, PR)
  3. run_tests (fresh clone at head_sha + hidden suite + cheatscan)
  4. verify (deterministic gate + reviewer muse turn)
  5. close + Ed25519 receipt
  - Rework loop: MAX_REWORK=2, resuming the same coder session. Block path: mark_blocked + escalate.
- **ScheduledRunWorkflow** plus `starter.py create_schedule`. Children are deduplicated per (task, rev, tick) with REJECT_DUPLICATE.
- **Model provider.** `core/backend/rouvr_models/providers.py` supports meta | ollama | openrouter | stub.

## Claim check: "core loop is done"
| Claim | Verdict |
|---|---|
| Muse CLI starts as Rouvr supervisor | [V] in code (scripts/rouvr, SUPERVISOR.md). Live launch [U] |
| Voice input | [U] Nothing in rouvr/, scripts or cli/rouvr_cli. If it exists, it is muse-native and not repo code |
| Auto-connects to SQLite WorkHub-lite | [V] ledger default URL plus auto-installed MCP plugin |
| Creates **slices** > tasks with expected output and pass criteria | **Partial.** `workhub_create_task(title, description, acceptance_criteria)` (mcp_server.py:46) makes a task plus contract. There is **no slice entity** in the ledger schema or MCP. "Expected output" is only free-text description |
| Assigns coder; coder picks up, tests, sets needs_review | [V] activities.assign_task/run_tests. needs_review is set when verify starts (activities verify → _close_to) |
| Supervisor verifies, ending done / changes_required | **Not quite.** The *reviewer persona* (a distinct principal) verifies, not the supervisor. Verdicts are approve / request_changes / reject. The terminal states are done or blocked; there is no `changes_required` state (request_changes loops back) |

Overall: the code→review loop is real, well-fenced and unit-tested. Two gaps (G1, G2 below) are likely to break it live on a supervisor-created task [U].

## Done, with refs (mapped to M1–M7)
- **M1, closed loop.** workflows.py TaskWorkflow; activities._close_to does CAS + readback hops. Ledger done-guard is at app.py:228.
- **M2, SHA-bound verification.**
  - [V] run_tests uses a fresh `clone_at(head_sha)`, with the hidden suite copied in only there.
  - Hidden suite tree-hash tamper check: hiddentests.py:57.
  - Cheat scan: cheatscan.py.
  - Deterministic gate checks PR branch, CI green, every criterion claimed, and a 3-round cap: gate.py.
  - Reviewer verdict that fails to parse → reject (fail closed).
- **M3, kill/resume.**
  - Lease/fence with reclaim on retry: lease.py, assign_task attempt>1.
  - tests/test_crash_recovery.py, test_r5_ownership.py.
  - Stuck flag at 30 min: mcp_server._annotate.
- **M4, identities.**
  - Coder and reviewer are distinct principals, with SELF_REVIEW raised at binding and again at the verify claim: personas.py:92, :104.
  - Coder gets `GH_TOKEN=""`; git grant goes via a 0600 file.
- **Receipts.** receipts.py: Ed25519 over the sha256 of 11 links (incl. head_sha, reviewer, evidence ids), key 0600-enforced, key-id rotation pool, tests/test_receipts.py.
- **M5, schedule.** ScheduledRunWorkflow + starter.create_schedule, idempotent per tick.
- **Demo ops task.** demo/morning_report.py runs instruction → contract → health/compile/deliver → readback verify → receipts. Delivery goes to a local outbox. It has ROUVR_INJECT kill_midstep / silent_delivery / revoked_key, tested by demo/test_morning_report.py [V].
- **M7, provider.** Pluggable Meta/Ollama/stub.

## Gaps vs the demo, ranked by demo impact
1. **G1. `escalate` raises NotImplementedError("P2")** (activities.py, escalate). Every block path (rework_exhausted, reject, CONTRACT_MOVED, revoked key) calls it after mark_blocked. NotImplementedError is not a RouvrError, so it is retryable under the workhub retry policy. The workflow will retry, then fail instead of returning `blocked`, and there is no escalation receipt. Tests hide this because they stub `escalate` (tests/test_workflow.py:51). This breaks the "inject failure → park + escalate" beat.
2. **G2. Nothing creates hidden tests for supervisor tasks.** `write_hidden_suite` is only called in scripts/r10_rehearsal.sh and in tests. `workhub_create_task` has no test-author step, even though SUPERVISOR.md claims one. `run_tests` → `fetch_hidden_suite` raises "no hidden suite" → block → G1. [U at runtime; statically certain.]
3. **G3. The demo ops task is not on the governed path.** morning_report.py is a standalone script: it doesn't run as a TaskWorkflow, doesn't use a standing grant and doesn't run on a schedule. The real workflow only knows code tasks (PR/CI/pytest).
4. **G4. Meta model as contract compiler and judge is mostly cosmetic.**
   - compile_contract uses a hard-coded contract; the model output is merged only if it is JSON, with no schema validation.
   - There is no judge in morning_report verify (it is deterministic only).
   - In TaskWorkflow, the "judge" is the reviewer muse turn; the Meta API is used only via META_API_KEY for muse.
   - Under the default stub, no Meta call happens at all.
5. **G5. No standing permission / grant object.** Neither the ledger nor the MCP has an owner-approved bounded policy. Schedules exist, but there's no MCP tool to create one, and no "Day 2: Done. No action needed" digest.
6. **G6. No slice entity or expected-output field** (see the claim check). Rework state is not visible as `changes_required`.
7. **G7. Reviewer reads the coder's workspace.** verify computes the diff from the coder's `ws` and runs the reviewer in the sibling `ws__verify`. Tests are SHA-bound, but the reviewer's diff is not taken from a fresh clone at head_sha.
8. **G8. Runtime packaging.**
   - `mcp` is missing from requirements.txt.
   - Spec points at `ebiz-backend-developer/rouvr-r10-scratch`.
   - Persona creds are hard-coded to `~/Repository/hackathon/.secrets/...`.

## Next tasks, with acceptance tests
- **T1. Implement `escalate`.** Write an `escalation` evidence row plus a waiting_on_supervisor entry, and make it non-retrying on failure.
  - AT: a TaskWorkflow test using the REAL Activities.escalate with rework_exhausted returns `{"state":"blocked"}`. The ledger shows state=blocked, an escalation evidence row, and the task in `workhub_list_tasks().waiting_on_supervisor`.
- **T2. Test-author step on create.** `workhub_create_task` (or a `rouvr_author_tests` tool) runs a distinct test_author persona/model that calls write_hidden_suite. The coder must never see it.
  - AT: create via MCP → `HIDDEN_TESTS_ROOT/<id>/manifest` exists with created_by=test_author.
  - AT: assert_coder_clean passes.
  - AT: an end-to-end stubbed-muse run reaches done.
- **T3. OpsTaskWorkflow for morning_report.** Steps run as activities with idempotency keys. Delivery readback is the verifier step; a fallback sink is used on silent_delivery.
  - AT: killing the worker mid-deliver gives exactly 1 outbox message and state done.
  - AT: silent_delivery leads to a fallback message id in the receipt.
  - AT: revoked_key means the task is blocked before claim, with an escalation row.
- **T4. Meta contract compiler + judge.** JSON-schema-validated contract (steps, criteria, side_effects, stakeholders, escalation, expiry); reject when invalid. The judge is a separate principal and records model + verdict evidence.
  - AT: invalid model JSON → contract refused (not merged).
  - AT: the judge evidence row has a principal ≠ executor.
  - AT: a run against the live Meta API is recorded once, by the owner.
- **T5. Standing grant + schedule tool.** A `grants` table (contract_rev, scope, expiry, approved_by) and an MCP `rouvr_schedule(task, cron)` that refuses to run without an active grant. The tick also produces an exception-only digest.
  - AT: a schedule with no grant → refused.
  - AT: a tick creates a child task; a duplicate tick creates no second child.
  - AT: a clean run → digest reads "Done. No action needed."
- **T6. Reviewer works from a fresh clone at head_sha** (reuse clone_at).
  - AT: mutating the coder ws after submit does not change the reviewer diff.
- **T7. Hygiene.** Add `mcp` to requirements, add a slice entity (or document that tasks are the slices), and make the cred/spec paths configurable.
  - AT: `make test-public` passes in a fresh venv.

## Risks
- **Secrets.**
  - scripts/rouvr greps META_MODEL_API_KEY from `.env` and exports `GH_TOKEN` from `gh auth token` into the whole stack's environment. Workers inherit it (the coder turn blanks GH_TOKEN, but other subprocesses may not).
  - The git grant is written to a plaintext 0600 file under WORKSPACE_ROOT (/tmp), and there is no deletion I could see [U].
  - The ledger token sits in /tmp/rouvr-supervisor/api-token.
  - Good: receipts keys must be 0600 and are never logged.
- **Sandboxing.**
  - The coder muse turn runs on the host in /tmp workspaces; there is no container. Only the test runner has a Dockerfile (testrunner.Dockerfile) [U whether it's used by default].
  - Hidden tests execute repo code.
- **Self-review.**
  - Principals differ, but both personas run on the same host/OS user and share a filesystem. The reviewer reads the coder's workspace (G7).
  - The supervisor creates tasks *and* holds the git grant.
  - Independence is logical, not enforced isolation.
- **Rework limits.**
  - The limits stack: MAX_REWORK=2 (3 attempts) × assign retry max 3 × gate MAX_SUBMIT_ROUNDS=3.
  - Gate rounds are counted across attempts from evidence, so the gate may block before MAX_REWORK.
  - Combined with G1, any exhaustion currently ends in a failed workflow rather than a clean block.
