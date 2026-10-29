# Rouvr Roadmap — roadmap, system design (HLD + LLD) and implementation guide
Consolidated 29 Oct 2026 (IST). One document replacing `rouvr-system-design-complete.md` (v2 design + v3 additions + v4 code guide) and the "Rouvr port map and unified roadmap" draft (29 Oct 2026, for Ankur's review).

**How to read this**
- **[V] / [I] / [U]** (from the port map): verified in code this session / inference / unverified live.
- **UNVERIFIED**: not proven live. **Phase 2 / P2**: designed, not built for the hackathon.
- **(ADDED — proposal)**: gap-fill by the assistant, not a verified fact. Everything else is the user's text, kept intact.
- Where sources disagree, the **29 Oct port map wins on facts**; each case is listed in §Conflict register.

**Parts**
1. Part I — Roadmap: owner decisions, timeline to deadline, work packages R1–R10 / A1–A2 / P2, dependency graph, port map, inventory.
2. Part II — System design: product, requirements, HLD, LLD (data, APIs, sequences, modules, errors, deployment), testing, ops, capacity, risks, open decisions.
3. Part III — LLD implementation guide: full typed code (`rouvr-scaffold.zip`), build order keyed to roadmap items.

---

## Conflict register (ADDED — consolidation)
| # | Topic | Older design doc said | 29 Oct port map says (wins) | Resolution in this doc |
|---|---|---|---|---|
| C1 | Muse resume | `muse exec --resume <session>` UNVERIFIED; #1 risk; respawn fallback | Resume was **removed on purpose** in hyeve-lane ("out of scope by owner order"); **owner reversed this on 29 Oct**: resume back in scope, R2 makes it work, respawn only if the CLI cannot | R2 is the spike. Code keeps `RESUME_SUPPORTED=false` until R2 reports RESUME. |
| C2 | Muse argv | Scaffold guessed `--model --output-format=stream-json --cwd --resume` | [V] `muse exec --json --provider meta --session-id <sid> --prompt-file … --workspace … --max-model-steps 40` (`_task_loop_child_argv`, workflows.py:2033) | `rouvr/muse.py` updated to the verified argv. `--model` is still UNVERIFIED. |
| C3 | Session per attempt | Current state C: "launches Muse with a fresh UUID each time… passes no session" | Session **is recorded**: uuid4 per leg, published as `muse_session_binding.v1` WorkHub row (task, role, workflow_run_id, activity_attempt). Gap is only that a retry mints a new uuid and never resumes | Session-binding rows reused (port as-is). The design's `muse_session` evidence type is mapped onto, or written alongside, the binding row (A1 decides). |
| C4 | Where the launcher lives | AIX canary / Muse worker | `TaskLoopWorkflow` + Muse launch are in **hyeve-lane** `lanes/temporal/workflows.py`, not AIX | Port map §3 is the source for porting. |
| C5 | WorkHub R10 paths | Placeholder paths (`/tasks/{id}/assignments/claim`, …) | [V] router routes: `/assignments/claim`, `/attempts`, `/results`, `/assignments/reclaim`, `/leases/claim`, `/leases/renew`, `/reclaim-dead-assignments`, `/assignment-liveness/renew` (prefix not confirmed) | `routes.py` uses the verified names; the prefix stays UNVERIFIED until A1. |
| C6 | WorkHub client | HTTP client | Lane currently shells out (`_dispatch_wh` → `workhub_exec.py`, creds JSON 0600) | Rewrite thin as the HTTP `WorkHubPort` (R3); creds come from a host ref, never argv. |
| C7 | Backends | WorkHub only ("no new WorkHub") | Two backends via `WorkHubPort`: real WorkHub (demo + every R4–R10 acceptance check) and `ledger/` (public/offline fallback, through R3) | Added `rouvr/workhub/port.py`; `ledger/` parity is R3. |
| C8 | Build plan | 9-step task order, "no dates" | R1, R2, A1, R3–R10, R8a, A2, P2 (ordered, no dates) | Port map order supersedes the 9 steps (mapping in §Roadmap ↔ old build plan). A proposed calendar to 5 Nov is ADDED. |
| C9 | Old page "G1 to G5" owners, "7 to 10 engineering days vs ~7 calendar days" | Earlier page text | Not used; owners per task are still open except R8a (test-author persona) | G1–G5 retired in favour of R/A IDs; owner column left open. |
| C10 | Evidence for tests | Test runner as evidence (step 6) | Plus **hidden acceptance tests** by a separate `test_author` persona (R8a, owner decision) and a **cheat-scan** (R8); today evidence is self-reported by the model | Added to requirements, personas and the build order; gate/cheat-scan code not yet in the scaffold. |
| C11 | Finish path | Activity returns → verify → close | **`submit_result(pr_url, claims)` MCP tool + deterministic gate** is the only exit, capped at 3 feedback rounds (R7); headless MCP in Muse is UNVERIFIED | R7 added; `MAX_REWORK=2` (verifier rework) and the 3-round submit gate cap are separate limits. |
| C12 | Verdict labels | approve / request_changes / reject | APPROVE / CHANGES_REQUIRED | Parser accepts both; internal value stays `request_changes`. |
| C13 | Heartbeat | Proposal: renew every 30 s, heartbeat timeout 90 s, lease TTL 120 s | Existing TaskLoop: heartbeat ~60 s, 3 min timeout | Proposal kept (worker-death recovery < 2 min); confirm in R5. |
| C14 | Workflow ID | Requirement: ID = task ID; reference code `rouvr-task-<tenant>-<task_id>-<sha16>` | R4: workflow ID = task ID (today `lane-task-<n>`) | Both satisfy "one task, one workflow". The deterministic id embeds the task ID; drop the tenant/hash suffix if WorkHub task IDs are globally unique (decide in R4). |
| C15 | Builder ≠ verifier | Enforced via persona pair + WorkHub check (fix list) | Today only session-based in `route_task`; must be enforced **at claim** (A1, R9) | Kept as an A1 item + R9 acceptance. |
| C17 | Persona registry source | OD3 recommended tenant-keyed YAML | Port map §3: "Rouvr gets a persona registry with `cred_ref`"; AIX has only static role→principal mapping [V] | **Owner decision 29 Oct: AIX mapping.** Decision wins; format ASSUMED in `personas.py`, confirm in R6. Public-repo users without AIX use the YAML fallback. |
| C18 | Start path | CLI or endpoint; R4 said both | — | **Owner decision 29 Oct: thin endpoint** `POST /workflows/start`. CLI is operator-only. Outbox starter is later/optional. |
| C19 | Recurring work (req 9, H-03, `ScheduledRunWorkflow`, P2 "wake") | Phase 2 goal | P2 lists wake | **Owner decision 29 Oct: coding only.** Recurring work is out of scope for now; the code is kept but unused. |
| C16 | Naming/hygiene | Risk 5: Llama wording differs from README | Owner decision: eBizneeds/Hyeve stays; strip VMG, betademo, hawks, host paths, IPs, secrets (R1) | Risk 5 is handled by R1. |

---

# Part I — Roadmap

## Owner decisions (29 Oct 2026)
1. **The demo runs on real WorkHub.** The `ledger/` backend still ships as the public/offline fallback through R3, but every acceptance check for R4 to R10 runs against real WorkHub.
2. **Resume is back in scope.** The goal of R2 is to make resume work, with respawn only as a fallback if `muse exec` truly can't do it. The "resume removed by owner order" stance in hyeve-lane is reversed.
3. **eBizneeds/Hyeve naming stays in the public repo.** Only VMG, betademo, hawks, host paths, IPs and secrets get stripped.
4. **Hidden acceptance tests get their own task (R8a)**, written by a separate test-author persona that is never the builder.
5. **(29 Oct, resolves OD1) Coding tasks only.** Recurring operational tasks are out of scope for now.
6. **(29 Oct, resolves OD2) Rouvr starts workflows through a thin endpoint** (`POST /workflows/start`). The outbox starter is later/optional.
7. **(29 Oct, resolves OD3) The AIX mapping is the persona registry source.**

## Timeline to deadline (ADDED — proposal)
**Deadline:** Wed 5 Nov 2026, 07:30 IST (Meta Global AI Developer Hackathon, Startup track). The port map orders work without dates; the calendar below is a proposal that respects its dependencies. About 6.5 working days remain from Thu 29 Oct, so R1/R2/A1 run in parallel and R8a test writing for the demo task starts as soon as R6 lands.

| Date (IST) | Milestone | Work packages | Exit gate |
|---|---|---|---|
| Thu 29 Oct | M0 Kick-off | R1, R2, A1 start in parallel | port map reviewed by Ankur |
| Fri 30 Oct | M1 Facts settled | R2 done (RESUME/RESPAWN), A1 done (`docs/workhub-fixes.md`) | staging happy path claim→attempt→renew→result 2xx |
| Sat 31 Oct | M2 One interface | R1 done, R3 done, R4 (thin endpoint `POST /workflows/start`, OD2) | contract suite green on ledger + staging; duplicate start rejected |
| Sun 1 Nov | M3 Ownership | R5 | kill worker → lease stops → retry reclaims same attempt |
| Mon 2 Nov | M4 One launcher | R6 (personas from AIX mapping, OD3; confirm format), R8a starts | one PR, binding row complete, rerun opens 0 PRs |
| Tue 3 Nov | M5 Evidence | R7, R8a done, R8 | stub PR rejected, real PR passes; submit gate caps at 3 |
| Wed 4 Nov | M6 Close loop | R9, R10 rehearsal + recording | end-to-end script exits 0, recorded |
| Wed 5 Nov 07:30 | **Submission** | buffer 00:00–07:30 for re-record only | — |
| After 5 Nov | M7 Back-port, M8 Phase 2 | A2, P2 (without recurring wake, OD1; outbox starter optional, OD2) | R10 green on AIX staging; P2 items each get their own check |

**Cut-line if late (proposal):** keep R4–R6, R8, R9, R10 on the critical path. If headless MCP fails in R2, R7 uses the fallback (a small tool loop around Muse). If R2 says RESPAWN, R10 shows respawn on the same attempt. P2 never enters the demo.

## Dependency graph
```
R1 ─────────────────────────────────────────────┐
R2 ──────────────────────┐                      │
A1 ──► R3 ──► R4 ──► R5 ──► R6 ──► R7 ──┐       │
                  └──────► R6 ──► R8a ──► R8 ──► R9 ──► R10 ──► A2
A1 ─────────────────────────────────────────────► R9          P2 (after)
```

## Roadmap ↔ old 9-step build plan
| Old step | Roadmap item |
|---|---|
| 1 Muse resume spike | R2 |
| 2 WorkHub small fixes | A1 (+ R3 port/ledger parity) |
| 3 Admission | R4 |
| 4 Ownership | R5 |
| 5 assignTask | R6 |
| 6 Test runner as evidence | R8 (+ R8a hidden tests) |
| 7 Verifier, result, close | R7 (submit gate) + R9 |
| 8 Kill and recover rehearsal | R10 |
| 9 Phase 2 | P2 (+ A2 back-port) |
| — (new) | R1 hygiene |

## Port map and unified roadmap (29 Oct 2026, verbatim)
29 Oct 2026, IST. Everything here was read-only: nothing was pushed, and no repo or VPS was changed.

**Sources** (GitHub HEAD unless noted):
- hyeve-lane `d2e39d8` (29 Oct 01:40 IST)
- hyeve-workhub-service `9798926` (29 Oct 02:24 IST)
- hyeve-agent-sdk main `e03f371` (4 Oct)
- aix-lane local clone `189f567` (25 Oct)
- Anxy/rouvr `ccc53b9` (27 Oct)

**Labels:**
- **[V]** I verified it in code this session.
- **[I]** It's an inference.
- **[U]** It's unverified live. I did not re-probe running services this session, so live health comes from the earlier recon.

## 0. Corrections to the earlier recon
1. **The launcher's location was wrong.** `TaskLoopWorkflow` and the Muse launch live in **hyeve-lane** `lanes/temporal/workflows.py` (2,849 lines), not in AIX. [V]
2. **Muse does not get a random, untracked session.** `_task_loop_child_argv` (line 2033) runs `muse exec --json --provider meta --session-id <sid> --prompt-file … --workspace … --max-model-steps 40`. The sid is `uuid4()` per leg (build at line 2464, review at line 2596). It is published as a `muse_session_binding.v1` WorkHub row with task, role, workflow_run_id and activity_attempt (`session_binding.py`, `session-binding-protocol.md`, lane-ops #661). So session-per-attempt is already **recorded**. What's missing is that a retry mints a new uuid and never resumes. [V]
3. **Resume was removed on purpose.** In `WorkerExecutionWorkflow`, `test_worker_execution.py:391 test_resume_machinery_removed` and `workflows.py:1634/1792` say "same-attempt resume … out of scope by owner order." Codex resume (`codex exec resume`) exists in `tools/codex_launch.py` (docstring lines 17–104, argv at line 1239). [V]
4. **The WorkHub line numbers have moved.** The functions all exist [V]:
   - service: `claim_assignment` 17875, `submit_result` 20104, `reclaim_assignment` 23720, `renew_workspace_lease` 29738, `reclaim_dead_assignments` 30047, `renew_assignment_liveness` 31464
   - router (`workhub_contract_router.py`): `/assignments/claim` 5297, `/attempts` 5341, `/results` 5376, `/assignments/reclaim` 5993, `/leases/claim` 6910, `/leases/renew` 6941, `/reclaim-dead-assignments` 7090, `/assignment-liveness/renew` 7351
5. **The SDK is further along than "fake providers".** Its README says it has a v2alpha1 config compiler, a tool router, a *local repository verifier that checks real bytes*, and an MCP capability bridge. It also "does **not** yet run Codex" and has no model-backed run qualified. It doesn't mention Muse at all. The work is on `codex/sdk*` branches, and main was last touched 4 Oct. [V]
6. **The b5be5c0 flag was wrong.** b5be5c0 only changes `.env.example` paths and has no VMG in it. The VMG text in Rouvr history is:
   - "VMG Hawks PR #163 …" added in `1d8e8e4` and removed in `1702c6a`
   - `tenant_id="vmg"` / "VMG Inc" still at HEAD in `core/backend/tests/runtime/objective_cycle/test_p27_p28_p29.py:17,19,38,42`

   [V]

## 1. Inventory: what exists in AIX today
| Component | Repo/path | What it does | State |
|---|---|---|---|
| TaskLoopWorkflow | hyeve-lane `lanes/temporal/workflows.py:2741` | claim → build → review → route. ID `lane-task-<n>`, REJECT_DUPLICATE, approval expiry checked before each leg, heartbeat ~60 s with a 3 m timeout, bounded retry, semantic refusals park | Code complete [V]. Live: worker polling, 0 runs, schedules paused [U] |
| claim/build/review/route activities | same file, lines 2365/2412/2536/2680 | build: idempotent on an existing `loopd-build-` row, checks binding and status, launches Muse, **reads a JSON report the model writes** (keys per command plus head_sha) and attaches it as `worker_result`. review: a second Muse session writes the verdict JSON. route: needs APPROVE, both sessions present and different, then moves the task to `needs_review` | Partial [V]. **Evidence is self-reported by the model, and the reviewer reads the builder's report. Route stops at needs_review: there's no result submit or close.** |
| WorkerExecutionWorkflow + governed attempt | workflows.py 1425–1615, `attempt.py`, `lease.py`, `wiring.py` | start/poll/cancel a governed attempt, attempt lease with epoch/owner, predecessor gate, hold records | Code [V], live unknown [U]. Resume deliberately removed |
| Supervisor/LeadTurn workflows | workflows.py 161/266 | board reconcile, lead turn | Code [V]. Schedules paused [U] |
| Session binding | `lanes/temporal/session_binding.py`, protocol md | binding rows in WorkHub comms `muse-delivery:<ulid>:<role>`, idempotent POST plus exact GET readback, sanitised | Code + tests [V] |
| Muse launcher (AIX packet path) | hyeve-lane `tools/aix_muse_worker.py` (1,424 lines) | `--packet` runner, governed worktree, own process group, structured `session.run.linked` / cleanup events, uses the account's subscription login | Code + tests [V]. Second launcher, alongside the TaskLoop direct argv |
| Codex launcher | hyeve-lane `tools/codex_launch.py` (2,768 lines) | session log, `codex exec resume`, deadlines, no bypass flags | Code [V]. Not used by Temporal |
| WorkHub contract | hyeve-workhub-service `backend/services/workhub_contract_service.py` (41k lines) and routers | assignment claim/reclaim, leases, liveness, attempts, results, dead-assignment reclaim, `/tasks/{id}/reassign`, lifecycle submit-result | Live [U]. **No caller uses the formal claim → attempt → result path end to end** (earlier recon) [U] |
| WorkHub client in lane | `_dispatch_wh` (workflows.py 340) calls `workhub_exec.py` from PATH with `LANE_WORKHUB_CREDS` (0600 JSON) | CLI shell-out client | Code [V] |
| Canary controller | aix-lane `tools/canary_lane.py` (9,937 lines) | admission, builder/reviewer/closer roles, `rework_after_changes_requested` 1584, rework cap 1398/1460, `requeue_after_worker_loss` 966, `requeue_after_stall` 4571, `closeout_as_closer` 7701, `closeout_approved_task` 7951 | Code [V]. Live failing `preclaim-task-read-refused` (raised at line 8358) [U] |
| Canary helpers | aix-lane `canary_recovery.py`, `canary_continuation.py`, `canary_model_adapter.py` | recovery and continuation packets, model adapter | Already copied, scrubbed, into Rouvr `lane/tools/` [V] |
| Test runner | VPS2 testrun-runner | runs tests as evidence | Healthy per earlier recon [U]. Not wired to TaskLoop |
| Verifier/review | review_task (model), canary reviewer role, WorkHub `independent_review` rows | self-review refused by session ID only | Partial [V]. No system re-run of tests |
| Persona/credentials | `bridge_creds.py`, `LANE_WORKHUB_CREDS`, `TASK_LOOP_MUSE_BIN_DEFAULT="/home/anxy/.local/bin/muse"` (workflows.py:1885), workspace default `/home/anxy/work` (line 2044), PATH in aix_muse_worker.py:299 | static role → principal, hardcoded host paths | Partial [V]. No persona registry |
| Auth integration | hyeve-auth-service plus JWT in the canary (`cfg, jwt, principal`) | principals for builder/reviewer/closer | Exists [V by signature]. Grant refusals are the live blocker [U] |
| SDK | hyeve-agent-sdk (TS, `@hyeve/agent-sdk`) | config compiler, tool router, local repo verifier, MCP bridge | Candidate. No model-backed run, no Muse [V] |
| Conventions | hyeve-lane `AGENTS.md`, `MUSE.md`, `WORKER_CONTRACT.md`, `MANDATE.schema.json`, `HANDOVER.schema.json` | agent instructions and contracts | Exist [V] |
| AIX product backend | aix `hyeve-aix-service/backend` (FastAPI, orchestrator v2, objective-cycle Temporal workflow) | product core | Copied into Rouvr `core/` [V]. Its own suite shows 472 failures pre-existing (27 Oct) |

## 2. What the Rouvr repo has now (`ccc53b9`, 1,908 files, 4 commits) [V]
- `core/`: the AIX backend, libs and src, including `workflows/temporal/{workflows.py (only a 110-line state machine), activities, objective_cycle_*}`. None of the hyeve-lane TaskLoop is there.
- `lane/tools/`: canary_continuation, canary_model_adapter, canary_recovery, opsbus_wake, waker_creds, waker_packet, wh_direct. canary_lane.py is not there.
- `ledger/`: WorkHub-lite. This is a FastAPI app with `/v1/tasks`, contracts, evidence and transition, plus `stub_workhub` (generic `/v1/{kind}`). **It has no assignment, lease, attempt or result endpoints.**
- `workers/`: `meta_worker` stub and `sdk-worker` (worker.mjs). There's no Muse launcher.
- `demo/`: morning_report, compose, 3 failure injections. `cli/`: rouvr_cli plus vscode-extension. `docs/`: CI and public tests (65 passed on 27 Oct).
- **Hygiene hits:**
  - The `vmg` tenant fixture listed in §0.6.
  - "Llama" wording in `docs/models.md:5-6`, `docs/demo.md:11`, `workers/README.md:7` and `.env.example:6,8` (base URL `api.llama.com`). The README itself no longer says Llama.
  - eBizneeds/Hyeve naming in README:110, CHANGELOG:7 and docs/ecosystem.md. That's probably intended, so it's a decision for you.
  - 300+ `hyeve_*` identifiers in core.
  - No `/home/anxy`, betademo, hawks or IPs found.

## 3. Port map
| AIX piece | Decision | Notes / stripping needed |
|---|---|---|
| TaskLoopWorkflow + claim/build/review/route | **Port, sanitise + extend** → `rouvr/orchestrator/` | Strip `/home/anxy` defaults (1885, 2044) and replace with config. Change the ID from `lane-task-<n>` to the task ID. Replace the self-reported build report with a test-runner activity. Add result + close. |
| session_binding.py + protocol | **Port as-is** (rename the thread prefix to config) | Already sanitised by design |
| lease.py, attempt.py, wiring.py (governed attempt, epoch fencing) | **Port, sanitise** | This is basically phase-2 fencing, already written [I] |
| aix_muse_worker.py | **Port, sanitise**. It becomes the single launcher, absorbing `_task_loop_child_argv` | PATH default at line 299 |
| codex_launch.py resume design | **Rewrite thin** as the Muse resume/respawn adapter | Copy the pattern (session log, pgid tracking, deadline refusal), not the Codex code |
| canary_lane.py rework/requeue/closeout | **Rewrite thin** as Temporal activities (rework cap, requeue on loss, closer closeout) | 9.9k lines, entangled with cfg/jwt. Lift the logic, not the file. Then retire it in AIX. |
| WorkHub service | **Keep AIX-only** (private). Rouvr talks to it through a `WorkHubPort` interface | Public Rouvr can't ship WorkHub. `ledger/` must grow the same port shape (assignment, lease, attempt, result) as a local backend [I: needed for anyone running Rouvr public] |
| `_dispatch_wh` / workhub_exec shell-out | **Rewrite thin** as an HTTP `WorkHubPort` client | Creds come from a host ref, never argv |
| Auth principals | **Keep AIX-only.** Rouvr gets a persona registry with `cred_ref` | |
| Persona registry | **Gap (build new)**: tenant-keyed YAML, refs only | |
| Independent test runner activity | **Gap**: fresh checkout at PR head, runs the hidden acceptance tests plus the repo suite, and the system writes the evidence | Reuse the testrun-runner in AIX deploy only |
| Diff cheat-scan | **Gap** | skipped/deleted tests, new mocks, hardcoded fixtures, `pass`/TODO bodies, bare excepts |
| `submit_result` MCP tool + deterministic gate | **Gap** | the only exit; capped feedback rounds |
| SDK | **Optional slot**, not ported | Only once it has a model-backed run |
| AIX product core already in Rouvr | Keep. Scrub per Task R1 | 472 failing tests aren't Rouvr's demo path. Exclude them from CI. |
| Canary helpers already in Rouvr lane/ | Keep for now. Retire when Temporal covers them | |

*(Owner decisions moved to the top of Part I — deduplicated.)*

## 4. Unified roadmap (ordered; no dates)
**Repo key:**
- **R** = Anxy/rouvr (public)
- **A** = AIX side (hyeve-lane / workhub, private)

**Rules for every task:**
- Builder ≠ verifier.
- Evidence is produced by the system.
- No self-merge.
- Each task opens one PR and the PR body links acceptance evidence.
- A task starts only once its dependencies are accepted.

### R1. Sanitise Rouvr to tenant-neutral (R)
- **Goal:** the public repo carries no private tenant, host or vendor-specific wording beyond the decisions you approve.
- **Steps:**
  1. Replace `tenant_id="vmg"` / "VMG Inc" in `test_p27_p28_p29.py` with `tenant-a` / "Tenant A".
  2. Change Llama to Muse / Meta Model API in `docs/models.md`, `docs/demo.md`, `workers/README.md` and `.env.example` (the base URL comes from env with no vendor default, or with whatever value you confirm).
  3. Add a CI hygiene check: `rg -i 'vmg|betademo|hawks|/home/anxy|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.|115\.246'` must return 0, with an allowlist file for approved mentions.
  4. Run gitleaks in CI.
  5. Write a history note in `docs/release-notes` that lists the history hits (`1d8e8e4` VMG Hawks line). **Do not rewrite history.**
- **Acceptance:** the CI job `hygiene` is green, gitleaks has 0 new findings, public tests are green, and `git grep -i vmg` at HEAD returns nothing.
- **Back to AIX:** the hygiene script. **Depends on:** none.

### R2. Muse resume spike (R, run on the AIX host)
- **Goal:** get resume working in headless `muse exec` (owner wants it). Fall back to respawn on the same attempt only if the CLI cannot resume, and document why.
- **Steps:**
  1. Run `muse exec --json --session-id S` on a toy repo task.
  2. Kill the process group mid-run.
  3. Try the resume forms the CLI offers (check `muse exec --help`; the same `--session-id S` may continue).
  4. Record whether prior context or tool history is present.
  5. Write `docs/spikes/muse-resume.md` with the exact commands and transcript hashes.
- **Acceptance:** a script `scripts/spike_resume.sh` reproduces it, and the doc states RESUME or RESPAWN with evidence.
- **Back:** re-enable resume in hyeve-lane (owner has reversed the "resume removed by owner order" stance in hyeve-lane. **Depends on:** none (it can run in parallel with R1).

### A1. WorkHub small-fix list (A, read + small PRs)
- **Goal:** confirm against the live OpenAPI and fix only what blocks the flow.
- **Steps:**
  1. Fetch the live `/openapi.json`.
  2. Confirm the routes in §0.4.
  3. Check for atomic create+bind (`/assignments/bind-route` 5568 may already cover it [I]).
  4. Check whether attempt metadata can hold session_id, workflow_run_id and pr_url (no model field was found by grep [V]), or use the session-binding rows instead.
  5. Check builder ≠ verifier enforcement at the claim (only session-based in route_task [V]).
  6. Diagnose `preclaim-task-read-refused` (canary_lane.py:8358) as a grant issue, because it will hit Temporal too.
  7. Map error codes to retryable/non-retryable.
- **Acceptance:** `docs/workhub-fixes.md` lists each item as CONFIRMED / FIX (with PR) / NOT NEEDED, with a curl readback per endpoint, and a scripted happy path claim → attempt → lease renew → result returns 2xx against staging.
- **Depends on:** none.

### R3. WorkHubPort + ledger parity (R)
- **Goal:** Rouvr code calls one interface with two backends: real WorkHub over HTTP, and local `ledger/`.
- **Steps:**
  1. Define the port (create_task, claim_assignment, open_attempt, renew_lease, renew_liveness, attach_evidence, submit_result, close, reclaim).
  2. Add the missing endpoints to `ledger/`.
  3. Write contract tests that run the same suite against both backends.
- **Acceptance:** the contract suite is green on the ledger, and green on WorkHub staging when `WORKHUB_URL` is set.
- **Back:** AIX uses the same HTTP client instead of `workhub_exec.py`. **Depends on:** A1.

### R4. Admission (R)
- **Goal:** one task, one workflow.
- **Steps:**
  1. Port TaskLoopWorkflow as `TaskWorkflow`, with workflow ID = task ID and REJECT_DUPLICATE.
  2. Add `rouvr start <task_id>` and `POST /workflows/start`.
- **Acceptance:** a test starts twice, and the second call returns `started:false` with the same run_id. The Temporal list shows 1 run.
- **Depends on:** R3.

> **29 Oct owner decision (OD2):** admission is through the thin endpoint `POST /workflows/start` (scaffold `rouvr/api.py`). `rouvr start` is the operator CLI only.

### R5. Ownership via the existing endpoints (R)
- **Goal:** the workflow holds assignment, lease and attempt, and the activity heartbeat renews the lease.
- **Steps:**
  1. Port lease.py/attempt.py.
  2. Renew in the heartbeat loop.
  3. Use the `reclaim-dead-assignments` path on retry.
- **Acceptance:** an integration test kills the worker. Lease renewals stop (visible in WorkHub or the ledger), and after the TTL the retry reclaims on the same attempt.
- **Depends on:** R4.

### R6. assignTask (R)
- **Goal:** a single launcher.
- **Steps:**
  1. Write the persona registry (YAML, refs only, tenant-keyed).
  2. Port `aix_muse_worker` with host paths moved to config.
  3. Prep the workspace with `AGENTS.md`/`MUSE.md`.
  4. Run the existing-PR check via the GitHub API.
  5. Persist the session per attempt through a session-binding row.
  6. Resume or respawn per R2.
- **Acceptance:** the coder persona opens exactly one PR. The binding row has session_id + run_id + pr_url. A forced rerun opens 0 new PRs.
- **Back:** replaces the direct argv in hyeve-lane. **Depends on:** R2, R5.

> **29 Oct owner decision (OD3):** the persona registry reads the existing AIX mapping (`rouvr/personas.py::load_aix_mapping`). The format is an assumption to confirm in R6.

### R7. submit_result tool + deterministic gate (R)
- **Goal:** the only way to finish.
- **Steps:**
  1. Add an MCP tool `submit_result(pr_url, claims)`. The process exiting without calling it counts as failure.
  2. Write a gate that checks only system-observable facts: the PR exists on the task branch, CI status, and every acceptance criterion maps to an evidence item from R8 or R9.
  3. Return one-line feedback to the same session.
  4. Cap the loop at 3 rounds, then mark the task blocked.
- **Acceptance:** tests show that an exit without submit becomes `failed`, a bad submit gets feedback, and 3 rejects become `blocked`. The gate fails closed on any read error.
- **Depends on:** R6, plus confirming that Muse can call MCP tools headless (part of R2).

### R8a. Acceptance-test author persona + hidden tests (R)
- **Goal:** every task gets acceptance tests written before the build, by someone other than the builder, and kept out of the builder's sight.
- **Steps:**
  1. Add a `test_author` persona to the registry (a fresh Muse session or the Codex supervisor, never the builder principal).
  2. When a task is created, the test author turns its acceptance criteria into executable tests (behaviour checks against the real service or data where possible, not file-exists checks).
  3. Store them in a private location the builder workspace can't read (a WorkHub evidence blob or a separate repo path), hashed and locked at creation.
  4. Write the full hidden suite for the demo task(s) now, as the first real use.
  5. The R8 runner pulls them at verify time only.
- **Acceptance:** the builder workspace has no copy of the tests (checked by the runner). The test hash recorded at creation matches the hash at verify. A seeded stub PR fails the hidden tests, and a correct PR passes.
- **Back:** AIX adopts the same test-author step for every lane task. **Depends on:** R6. **Blocks:** R8.

### R8. Independent test runner + hidden acceptance tests + cheat-scan (R)
- **Goal:** evidence the builder never writes.
- **Steps:**
  1. Acceptance tests are written at task creation by a separate persona and stored outside the builder workspace.
  2. The runner does a fresh clone at the PR head SHA in a clean container, runs the hidden tests plus the suite, and the system writes the evidence with SHA, command, exit code and log hash.
  3. The cheat-scan flags skipped/xfail/deleted tests, new mocks or monkeypatches of external calls, literals that equal fixture values, empty or TODO bodies, and broad excepts.
- **Acceptance:** a seeded "fake" PR (stub plus skipped test) is rejected, and a real PR passes. The evidence row author is the runner principal.
- **Back:** this replaces the self-reported `worker_result` in hyeve-lane build_task. **Depends on:** R6, R8a.

### R9. Verifier + result + close (R; the close logic is lifted from canary_lane)
- **Goal:** different principal, rework loop, system-only close.
- **Steps:**
  1. The verifier persona gets the acceptance criteria, diff and R8 evidence (never the builder's report) and returns APPROVE or CHANGES_REQUIRED.
  2. Rework goes back into R6 on a new attempt.
  3. Approval leads to `submit_result` in WorkHub and then a closer-principal close (logic from `closeout_as_closer` 7701).
  4. Readback confirms the status.
- **Acceptance:** a test loops rework at least once. The verifier principal ≠ builder principal is enforced at the claim (not only by session). The closed task's result links task, attempt, run, session, PR and test run.
- **Depends on:** R7, R8, A1.

### R10. Kill/resume rehearsal + recording (R)
- **Steps:** a scripted end-to-end run: assign → build → `kill -9` the worker → recover → verify → close, with no human input.
- **Acceptance:** the script exits 0. Temporal history shows a retry on the same attempt, there's 1 PR, and the WorkHub task is closed. Record it for the demo.
- **Depends on:** R9.

### A2. Back-port to AIX (A)
- **Steps:**
  1. Bump hyeve-lane to consume the Rouvr orchestrator modules (or vendor them at a tagged SHA).
  2. Retire the TaskLoop direct launcher.
  3. Leave the canary untouched until R10 passes on AIX staging.
- **Acceptance:** the same R10 script is green against real WorkHub. **Depends on:** R10.

### P2. Phase 2 (both)
> **29 Oct owner decisions:** recurring wake is out of scope (OD1, coding only). The outbox starter is optional (OD2).
Each item gets its own acceptance check when it's picked up:
- unattended wake (Temporal schedule)
- one-shot escalation with a dedupe key
- signed receipts
- outbox
- OTel
- contract-revision approval
- canary retirement
- an SDK exec-spec slot (only after the SDK shows a model-backed run)

## 5. Risks and open questions for Ankur
1. **[DECIDED: real WorkHub, ledger as fallback]** Public Rouvr can't depend on private WorkHub.** Is the hackathon demo run against real WorkHub (with the `ledger/` parity backend shipped for the public), or against the ledger only? I recommend both, via R3.
2. **[DECIDED: resume wanted; R2 makes it work]** Resume was removed "by owner order"** in hyeve-lane. Do you still stand by that, or does R2 decide?
3. **Grant refusals.** `preclaim-task-read-refused` is an Auth/WorkHub grant problem. If it isn't fixed in A1, Temporal will park on the same refusal.
4. **Headless MCP in Muse** is unverified. R7 depends on it, and the fallback is our own small tool loop around Muse.
5. **[DECIDED: keep eBizneeds/Hyeve]** Naming in the public repo. Should eBizneeds/Hyeve stay (README:110, CHANGELOG, ecosystem.md)? The 300+ `hyeve_*` identifiers remain, and a rename is post-hackathon.
6. **Core carries 472 failing tests.** CI must scope to the demo path, or judges will see red if they run everything.
7. **[DECIDED: R8a test-author persona]** Owners per task, and who writes the hidden acceptance tests** (it must not be the builder bot).
8. **History.** The VMG Hawks line exists in `1d8e8e4` (removed in `1702c6a`). Do you want a force-push or a squash, or to leave it? I'm flagging it only.


---

# Part II — System design (HLD + LLD)
*(From `rouvr-system-design-complete.md` v2/v3, preserved; 29 Oct annotations inline.)*

## Product summary
Rouvr is a reliable autonomous colleague. You delegate work, and it records intent, agrees an execution contract, runs agents with defined authority, gets independent review, recovers from failure and closes with evidence. Proven recurring work runs unprompted, so the daily update becomes exception-only: "Done. No action needed."
- **Context:** Meta Global AI Developer Hackathon (Cerebral Valley), Startup track
- **Deadline:** Wed 5 Nov 2026, 07:30 IST
- **Models:** Meta Model API. `muse-wonder-1.0` runs the main loop and `muse-spark` runs subagents.
- **Interface:** Muse Code. Rouvr is the interactive Muse session.
- **First use case:** software development (Rouvr builds Rouvr). **Scope = coding tasks only** (owner decision OD1, 29 Oct 2026). Recurring/operational tasks are out of scope for now.
- **State on 28 Oct:** the parts exist but aren't wired together. There is no unattended loop yet.

**Design principle:** WorkHub owns the facts. Temporal owns orchestration and retry. The SDK (optional) resolves non-secret policy, never credentials or scheduling. The persona registry resolves secrets on the host. One Muse launcher runs every worker.

## Scope, goals, non-goals and glossary  *(ADDED — proposal; derived from the sections below)*
**Goals (phase 1):** one instruction → one governed WorkHub task → one Temporal workflow → one PR → independently verified → closed with evidence, surviving a worker kill on the same attempt.
**Goals (phase 2):** one-shot escalation, signed receipts, OpenTelemetry, multi-tenant SDK spec. *Later/optional (29 Oct owner decisions):* unattended recurring work (out of scope per OD1) and outbox-driven start (optional per OD2; the thin start endpoint is the start path).
**Non-goals (both phases, proposal):** replacing WorkHub; a new UI beyond Muse Code + Temporal UI; running untrusted third-party code outside persona workspaces; human-in-the-loop approval of every step (only blocked/escalated tasks reach a human).

| Term | Meaning |
|---|---|
| Rouvr | The interactive Muse Code session a person launches manually; intake, kickoff, liaison, closeout readback |
| Worker session | A headless `muse exec` session created programmatically by `assign_task`, one per attempt |
| Task / assignment / attempt / lease | WorkHub facts: the work item; who holds it; one execution try; the time-bounded hold renewed by heartbeat |
| Persona | Claim-time binding of login, role, cred refs and allowed repos |
| Evidence | Typed WorkHub row (`evidence_type`, `evidence_ref`, `evidence_json`), exact-readback verified |
| Rework | Verifier verdict that opens a new attempt on the same branch, bounded by `MAX_REWORK` |
| Blocked | Terminal-for-phase-1 state owned by an operator; phase 2 escalates once |

## Current state (28 Oct 2026, from read-only recon)
> Updated by the 29 Oct port map: see §Inventory, §Corrections and conflicts C3–C6.
| Link | Status | Evidence |
|---|---|---|
| A. Create governed task | PARTIAL | Task create, assignment claim, lease, attempts and results are in the OpenAPI. Persona, repo and branch are not set in one atomic create. |
| B. WorkHub → Temporal | EXISTS, NOT WIRED | Temporal server and lane worker are healthy, and schedules are paused. Nothing starts a workflow from WorkHub. The loop workflow uses `lane-task-<n>`, not the task ID. |
| C. Temporal → persona/worker | PARTIAL | *(corrected 29 Oct, port map [V])* The task loop launches Muse directly (`muse exec --json --provider meta --session-id <uuid4> …`) with a new uuid4 per leg (build, review), recorded as a `muse_session_binding.v1` WorkHub row (task, role, workflow_run_id, activity_attempt). A retry mints a new uuid and never resumes (resume was removed on purpose). `aix_muse_worker.py` prepares a governed worktree. |
| D. Progress/results/comms | PARTIAL | AIX has formal result and review paths. F1 recipient-bound messaging exists but isn't wired into the build loop. |
| E. Kill/resume same attempt | MISSING | Muse resume has never been verified live. Codex resume is a proven pattern to copy. |
| F. Verify/close | PARTIAL | The task loop routes approval but doesn't close. AIX has builder, reviewer and closer identities. There is no current full-cycle proof. |
| G. Autonomous wake | PARTIAL | The AIX canary is live on VPS1 but fails at `preclaim-task-read-refused`. Temporal schedules are paused. |
| H. Next task / rework | PARTIAL | Implemented in AIX, which is blocked by the preclaim failure. |
| I. Dead worker reclaim | PARTIAL | WorkHub reclaim exists in code (see the API mapping). There is no live proof. |
| J. One-shot escalation | MISSING | |
| K. Persona registry | PARTIAL | AIX has static role mappings. The Muse login is hardcoded. There is no canonical registry. **OD3 (29 Oct): the AIX mapping becomes the registry source**; Rouvr reads it through `rouvr/personas.py`. |

The VPS2 supervisor service is idle and is being disabled. The VPS2 testrun-runner is healthy and will be reused.

## Requirements

### Functional
| # | Requirement | Phase |
|---|---|---|
| 1 | Accept an instruction in Muse Code and create a WorkHub task with persona, repo, branch and acceptance | 1 (needs the atomic-create fix) |
| 2 | One WorkHub assignment starts exactly one workflow (workflow ID = task ID) | 1 |
| 3 | The worker runs under a persona binding (login, role, allowed repos) resolved at claim time, with an authenticated identity | 1 |
| 4 | The workflow owns the assignment, lease and attempt. The lease is renewed by the activity heartbeat. | 1 |
| 5 | A killed worker recovers on the same attempt: resume the same Muse session, or respawn on the same task, branch and attempt, with no duplicate PRs | 1 (the path depends on the spike) |
| 6 | Tests run as evidence. A separate verifier persona approves or sends the work back for rework. | 1 |
| 7 | The close records the task, attempt, workflow run, persona session, PR and test run in WorkHub | 1 (plain record). Signed receipt in phase 2. |
| 8 | One deduplicated owner escalation when blocked | 2 (phase 1 marks the task blocked) |
| 9 | ~~Authorised recurring work runs on schedule without a live session~~ **Out of scope for now (OD1, 29 Oct 2026: coding tasks only)** | later |

**Assumptions (phase 2):** the model compiles intent into a typed contract that is granted once and bound to an exact revision. Failure injection (silent delivery, revoked key) goes beyond the kill test.

### Non-functional
| Quality | Target | Basis | Phase |
|---|---|---|---|
| Exactly-once effect | No duplicate PRs or results per attempt | Workflow-side idempotency keys plus the existing-PR check before launch | 1 |
| Durability | No task state lost on a worker or host crash | Temporal history plus WorkHub facts | 1 |
| Recovery time | Recover within 2 min of worker death | Assumption, UNVERIFIED | 1 |
| Auditability | Every close has its evidence recorded in WorkHub (a signed receipt later) | WorkHub result | 1 / 2 |
| Isolation | Keyed by tenant from day one | Persona registry | 1 |
| Secret hygiene | Secrets never leave the host or enter the SDK spec, prompts or logs | Registry holds refs only | 1 |
| Availability | 99.5% control plane | Assumption | 2 |
| Cost | Cheap model for subagents | `muse-spark` | 1 |

### Non-functional: SLOs, limits and retention  *(ADDED — proposal; all targets are assumptions until measured)*
| SLI | Target (proposal) | Measured from |
|---|---|---|
| Admission latency (`rouvr start` → run_id) | p95 < 2 s | Starter log / Temporal start event |
| Claim → first Muse turn | p95 < 60 s | Temporal history timestamps |
| Worker-death recovery | < 2 min (existing assumption) | heartbeat timeout 90 s + reschedule |
| Close latency (approve → `done` readback) | p95 < 30 s | `close_task` activity duration |
| Duplicate PRs per attempt chain | 0 | GitHub PR list per branch |
| Tasks closed without eligible evidence | 0 | WorkHub evaluation ids on every `done` |

| Limit (proposal) | Value | Enforced by |
|---|---|---|
| Builder slots per host | `BUILDER_CONCURRENCY` (2) | Temporal worker `max_concurrent_activities` |
| Tokens per step | step budget (`MUSE_BUDGET_EXCEEDED`) | `enforce_post_turn` |
| Workspace size | ≤10k files / 256 MB scope (from on-host layout) | Muse launcher |
| Rework | `MAX_REWORK` = 2 | TaskWorkflow |

**Retention (proposal):** Temporal history 30 days (namespace retention); WorkHub evidence kept for the life of the task (WorkHub owns it); workspaces 7 days after close (stated); evidence dirs on host 30 days then archive.

## HLD: architecture

### Phase 1 (hackathon)
```mermaid
flowchart LR
  U[User] -->|asks| R[Rouvr<br/>interactive Muse session]
  R -->|create + assign task| WH[(WorkHub - existing)]
  R -->|start workflow id = task id| T[Temporal TaskWorkflow]
  T -->|claim assignment, attempt| WH
  T --> A[assignTask activity<br/>heartbeat renews lease]
  A --> S[Exec spec<br/>local default / SDK optional]
  A --> P[Persona registry<br/>from AIX mapping - OD3]
  A -->|existing-PR check| GH[GitHub]
  A --> L[muse_launcher<br/>session per attempt]
  L -->|PR| GH
  L <-->|task, progress, PR url| WH
  T --> TR[testrun-runner]
  T --> V[Verifier persona]
  V -->|approve| C[Result + close in WorkHub]
  V -->|rework| T
  C --> R
```

### Phase 2 target (unattended)
```mermaid
flowchart LR
  U[User] --> R[Rouvr]
  R -->|create task| WH[(WorkHub)]
  WH --> OB[Outbox relay - later/optional]
  OB -.-> ST[Thin start endpoint<br/>POST /workflows/start]
  ST -->|id = task_id| T[TaskWorkflow]
  T --> A[assignTask]
  A --> M[Muse worker<br/>session = attempt]
  M --> API[Meta Model API]
  T --> TR[Test runner]
  T --> V[Verifier]
  V -->|approve| RC[Result + signed receipt]
  V -->|rework| T
  T -->|blocked| E[One-shot escalation]
```

### HLD: end-to-end data flow and planes  *(ADDED — restates the stated design as a flow; no new components)*
| # | Step | From → To | Data | Durable where |
|---|---|---|---|---|
| 1 | Instruction + acceptance | User → Rouvr | free text | — |
| 2 | Create task (+ bind persona/repo/branch) | Rouvr → WorkHub | task, acceptance, `execution_spec` evidence | WorkHub |
| 3 | Start workflow | Rouvr → Temporal | `TaskWorkflowInput` (frozen, secret-free) | Temporal history |
| 4 | Claim assignment + lease, open attempt | Workflow → WorkHub | `temporal_handoff` evidence | WorkHub |
| 5 | Resolve persona + spec, preflight, PR check | assignTask → registry/WorkHub/GitHub | refs only; live binding | — (never cached) |
| 6 | Turn | assignTask → Muse → Meta Model API | prompt, diff | workspace + supervisor evidence dir |
| 7 | Session, progress, PR | assignTask/Muse → WorkHub | `muse_session`, `muse_progress`, `pr_record` | WorkHub |
| 8 | Tests | Workflow → testrun-runner | `test_evidence` | WorkHub |
| 9 | Verdict | Verifier (distinct principal) → WorkHub | `review_verdict` | WorkHub |
| 10 | Result, evaluate, transition, readback | Workflow → WorkHub | formal result, evaluation id, `done` | WorkHub |
| 11 | Closeout | Rouvr ← WorkHub | result + evidence | — |

**Control plane:** Rouvr, starter, Temporal, WorkHub API. **Execution plane:** builder/test/verifier workers, Muse CLI, workspaces, Meta Model API, GitHub. Secrets cross only into the execution plane, on the host, at spawn.

## HLD: components
| Component | Owns | Status 28 Oct | Phase 1 work | Phase 2 |
|---|---|---|---|---|
| Rouvr | Intake, liaison, monitoring, closer readback | Works | Start-workflow call, closeout readback | Event-driven start |
| WorkHub | Source of truth: task, assignment, attempt, lease, result | Live | Small fixes only (see API mapping) | Fencing tokens, outbox |
| Temporal | Orchestration, retry, history | Healthy, idle | `TaskWorkflow` plus admission | ~~Schedules for unattended wake~~ (out of scope, OD1) |
| Thin start endpoint *(OD2, 29 Oct)* | `POST /workflows/start` → idempotent Temporal start; nothing else | New | `rouvr/api.py` (ASGI) + `rouvr start` operator CLI | Outbox-driven start becomes optional |
| assignTask() | Resolve, prepare, PR check, launch, heartbeat | Missing | New activity | — |
| Exec spec provider | Non-secret policy, instructions, capabilities | Optional | Local default (task plus `AGENTS.md`/`MUSE.md`); SDK slot if the teammate's branch is proven | Multi-tenant SDK spec |
| Persona registry | Persona → login, cred refs, role, repos | Partial | **AIX mapping as source (OD3)**, read via `load_aix_mapping`; YAML loader kept as fallback | — |
| Muse launcher | Headless `muse exec` | Two launchers, random sessions | Single launcher only, session per attempt, resume or respawn | — |
| Test runner | System tests as evidence | Unconnected | Activity calling testrun-runner | — |
| Verifier | Independent review | Partial | Separate principal, approve/rework | — |
| Escalation | One-shot owner alert | Missing | Mark blocked | Dedupe key plus F1 messaging |
| Legacy (AIX canary, VPS2 watcher, host loop driver, direct TaskLoop launcher) | — | Mixed | Leave untouched | Retire after acceptance |

## HLD: tech stack
| Layer | Choice | Rationale |
|---|---|---|
| Orchestration | Temporal | Durable history, retries and heartbeats. The ID reuse policy gives exactly one workflow per task. |
| System of record | Existing WorkHub (Postgres-backed) | Already live with assignment, lease, attempt, result and reclaim. Reuse it. |
| Models | `muse-wonder-1.0`, `muse-spark` via the Meta Model API | Strong main loop, cheap subagents and checks |
| Agent runtime | Muse Code CLI, headless | Fixed or resumable session IDs are **UNVERIFIED** (spike, task 1) |
| Workers | Python Temporal SDK | Matches the single Muse launcher |
| Secrets | Host-local vault or per-persona env file, referenced by ID | Secrets stay on the host |
| Code host | GitHub PRs | Evidence and review surface, and the source for the existing-PR check |
| Observability | Phase 1: Temporal UI plus WorkHub evidence. Phase 2: OpenTelemetry, Prometheus, Grafana. | Keeps phase 1 small |

## HLD: scale, availability, security, observability
- **Scale:** horizontal workers on per-role task queues (builder, verifier, test), scaling separately. Concurrency is capped per tenant and per model with rate limiters. Phase 1 runs one queue at low concurrency.
- **Availability:** workers are stateless, and durable state lives in Temporal and WorkHub. Phase 1 runs a single region and a single Temporal server. A WorkHub replica and the 99.5% target come in phase 2.
- **Security:** personas get least privilege, with their own credentials and allowed repos. The verifier principal must differ from the builder for the same task (no self-review), checked at the verifier claim. Secrets are resolved only on the host, never in specs, prompts or logs. Approval bound to a contract revision comes in phase 2.
- **Observability:** the trace ID equals the task ID. Phase 1 shows lease renewals, recovery count (resume or respawn) and rework rate through Temporal history and WorkHub. Phase 2 adds spans across Rouvr, WorkHub, the workflow, the activity and the Muse session, plus metrics on escalations and token spend. Only live evidence counts.

## HLD: multi-tenancy, availability/DR, security and scaling (detail)  *(ADDED — proposal unless the doc states it above)*
### Multi-tenancy
- Tenant is carried everywhere already stated: `X-Hyeve-Tenant-Id`, `tenant_id` in `TaskWorkflowInput`, `workflow_id = rouvr-task-<tenant>-<task_id>-<sha16>`, tenant-keyed persona registry.
- Proposal: Temporal search attribute `TenantId` (stated) + per-tenant rate limiter keys `(tenant, model)`; phase 2 option of one Temporal namespace per tenant for hard isolation and per-tenant retention.
- Proposal: workspaces and evidence paths gain a tenant prefix in phase 2 (`/srv/rouvr/<tenant>/workspaces/...`); phase 1 is single-tenant `default`.
- No cross-tenant reads: the WorkHub client is constructed per tenant (`workhub_client_for(tenant_id)`, stated).

### Availability and disaster recovery
| Component | Phase 1 (stated) | Failure impact | RPO / RTO (proposal) | Phase 2 (proposal) |
|---|---|---|---|---|
| Temporal server | Single server on lane host | No new starts/progress; running activities fail heartbeats | RPO = last persisted event (its DB); RTO < 30 min manual restart | Temporal DB backups nightly + PITR; or Temporal Cloud |
| WorkHub | As deployed (Postgres) | All effects refuse (fail closed); activities retry `E_DOWNSTREAM` | Inherits WorkHub's backup policy (UNVERIFIED) | Read replica (stated) + PITR |
| Workers | Stateless | Heartbeat timeout → same-attempt recovery | 0 / < 2 min | Workers on ≥2 hosts |
| Workspaces | Host disk | Respawn re-clones branch; PR + progress in WorkHub | Branch on GitHub is the RPO | — |
| Persona registry / creds | Host file | No spawns (fail closed) | Backed up with host config (proposal) | Vault |
DR drill (proposal): quarterly restore of Temporal DB into a scratch host and replay one closed workflow with the replayer.

### Security (threat model, STRIDE)
| Threat | Example | Control (stated → proposed) |
|---|---|---|
| Spoofing | Worker acts as another persona | Persona resolved at claim on host; JWT per persona via service tokens; verifier ≠ builder |
| Tampering | Agent edits files outside scope, rewrites evidence | `MUSE_PATH_*` checks; evidence dir outside workspace, immutable publish; exact readback |
| Repudiation | "Who closed this?" | Evidence chain + formal result; phase 2 signed receipt |
| Information disclosure | Secrets in prompts/logs | Refs only; `sanitize_for_log`; JWT in memory; 0600 creds |
| Denial of service | Runaway tokens / loops | Step budget, `start_to_close`, `MAX_REWORK`, per-model limiters |
| Elevation of privilege | Agent pushes to a disallowed repo | `allowed_repos` + `repo_not_allowed` 403; `denyUnmatched` capability posture |
| Prompt injection (proposal) | Repo content instructs the agent to exfiltrate | Read-only posture by default, no secrets in env beyond persona's own, network egress allowlist (proposal: Meta Model API, GitHub, WorkHub only) |

### Scaling
- Stated: per-role queues scale independently; limiters per tenant and model.
- Proposal: autoscale builder workers on Temporal `ApproximateBacklogCount` of `rouvr-builder`; cap at model RPM/TPM budget ÷ tokens per turn. WorkHub load is ~tens of writes per task (see capacity math), so WorkHub is not the bottleneck; model rate limits are.

## Persona model
> **29 Oct addition (owner decision 4):** a third role `test_author` writes hidden acceptance tests at task creation and is never the builder principal (R8a). Closer principal performs close (logic from canary `closeout_as_closer`).
A persona is **the login, role and allowed repos that a run uses**, resolved at claim time. Bots stay stateless and role-agnostic. Any worker process can run any persona it is handed.

```yaml
# host-local, tenant-keyed; refs only, never secrets (path is an example)
tenants:
  default:
    personas:
      coder:    {role: builder,  muse_login_ref: ref:muse/coder,    workhub_cred_ref: ref:workhub/coder,    allowed_repos: [Anxy/rouvr], model: muse-wonder-1.0}
      verifier: {role: verifier, muse_login_ref: ref:muse/verifier, workhub_cred_ref: ref:workhub/verifier, allowed_repos: [Anxy/rouvr], model: muse-spark}
      closer:   {role: closer,   workhub_cred_ref: ref:workhub/closer, allowed_repos: [Anxy/rouvr]}
```
**Rule:** for a given task, the verifier's principal is never the builder's principal.

## LLD: data view (fields Rouvr relies on in WorkHub)
These are not new tables. Exact field names are **UNVERIFIED** against the live OpenAPI (task 2); the names below are the design contract that task 2 confirms or corrects.

### Entities and fields
| Entity | Fields Rouvr reads/writes | Notes |
|---|---|---|
| Task | `id`, `task_number`, `project_id`, `tenant_id`, `title`, `description`/intent, `status`, `record_version` (CAS), `active_execution_contract_ref`, `acceptance` (contract ref or criteria) | `record_version` is the compare-and-swap token for close; `active_execution_contract_ref` pins the live contract |
| Task (phase-1 fix) | `persona`, `repo`, `branch` bound atomically at create | Task 2 small fix; until then the assignment claim carries persona/repository binding/authority/acceptance refs |
| Assignment binding (live read) | `assignment_id`, `assignment_status` (`active`), `assignment_record_version`, `executor_sub`, `holding_principal`, `lease_epoch`, `authority_ref`, `plan_digest`, `route_digest`?, `lease_expires_at`? | `route_digest` compared only when the owner read carries it; `lease_expires_at` **must** be surfaced by task 2 or preflight refuses `BINDING_LEASE_EXPIRY_UNPROVEN` |
| Attempt (WorkHub row) | `workhub_attempt_id`, `attempt_version` | No owner API exposes the attempt row directly; identity/version are proven by the `temporal_handoff` evidence receipt filed at claim time |
| Attempt (Rouvr-owned metadata/evidence) | `muse_session_id`, `workflow_run_id`, `pr_url`, `seq`, `state` | Stored as task metadata or evidence rows; exact placement is a task 2 item. **UNVERIFIED** |
| Evidence row | `id`, `task_id`, `project_id`, `evidence_type`, `evidence_ref`, `evidence_json` | Every write is followed by an exact-readback check before the writer proceeds |
| Verification evaluation | `checks[]` (each with `artifact_ref` pinned to an evidence `id`/`evidence_ref`), `decision`, `eligible_for_done`, `id` | Evaluation refuses unless every `artifact_ref` is already evidence on the task |
| Result (formal) | attempt id, verdict (`approved`/`rework`/`failed`), evidence refs, `formal_result_id` | **UNVERIFIED** end to end from a workflow |

### Canonical evidence types (phase 1)
| `evidence_type` | `evidence_ref` | Written by | Purpose |
|---|---|---|---|
| `temporal_handoff` | `temporal-handoff-<controller_attempt>-e<epoch>` (exact fence key) | Claim path | Proves attempt identity/version + fence (`assignment_revision`, `lease_epoch`) |
| `execution_spec` | `contract:<contract_ref>` | Intake | Pins the complete accepted selection block to the live contract ref (exactly one row per live ref) |
| `muse_session` | `session:<muse_session_id>` | assignTask | Records session id per attempt before launch |
| `muse_progress` | `progress:<attempt>:<seq>` | Muse worker | Resumable progress used by the respawn fallback prompt |
| `pr_record` | `pr:<attempt>` (or PR URL) | assignTask | PR URL recorded immediately at pre-check and post-exit |
| `test_evidence` | `testrun:<run_id>` | Test activity | Test run evidence the verifier must cite |
| `review_verdict` | `verdict:<attempt>:<n>` | Verify activity | Approve/rework + reasons; cited at close |
| `controller_terminal_recovery_v1` | `completed-halt-recovery:<sha>` | Recovery path | Operator recovery audit record |

### State machines
**WorkHub task `status`:** `needs_review` gates close; `done` is terminal readback. Full enum mapping is a task 2 item. Rouvr's expected flow maps onto it:

```
created → assigned → running → verifying → done
                 ↘ rework → running (bounded by MAX_REWORK)
any → blocked → (phase 2: resolution → running; phase 1: terminal, operator-owned)
```

Close precondition: task is `needs_review` → evaluation returns `eligible_for_done=true` → batch transition to `done` with `expected_record_version` (CAS) → terminal readback shows `done`.

**Workflow phases:** `STARTING → RUNNING_STEP → VERIFYING_STEP → BUILDING_REVIEW → {COMPLETED_FOR_REVIEW | GAPS_FOUND}` with `WAITING_APPROVAL`, `WAITING_BLOCKER`, `SUSPENDED`, `REPAIRING`, `CERTIFYING`, `CERTIFIED`, `FAILED`, `CANCELLED`, `BLOCKED` and the retry chain `BLOCKED → RESOLUTION_PENDING → READY_TO_RETRY → RETRYING → RUNNING_STEP`. Transitions are enforced by the transition table in `TaskWorkflow` (see §LLD core modules); `FAILED`/`CANCELLED` are reachable from every non-terminal phase.

**Attempt `state` (Rouvr-owned):** `open → claimed → running → {session_recorded → pr_recorded} → tested → {approved | rework} → closed | blocked`. Recovery never creates a second attempt for the same failure: resume and respawn both continue the same `attempt_id` (see N-03/N-04).

### Lease and assignment state machines  *(ADDED — proposal; WorkHub's real enums confirmed by task 2)*
```
Assignment:  unassigned → active(holder=P, epoch=e) → released | reclaimed(epoch=e+1) | moved(holder≠P)
Lease:       none → held(expires_at) → renewed(expires_at') … → expired → reclaimable → held(epoch+1)
                                     ↘ released (close / cancel)
```
| Event | Assignment | Lease | Rouvr action |
|---|---|---|---|
| claim ok | active, epoch e | held | file `temporal_handoff` (`-e<e>`) |
| heartbeat | — | renewed | `renew_lease` inside `assign_task` |
| worker dies | active (stale) | expires after `LEASE_TTL_S` 120 s | Temporal retries same attempt; re-claim or `reclaim_dead_assignment` (N-16) |
| another holder | moved | held by other | `E_MOVED`, no spawn (N-05) |
| close / cancel | released | released | cancel clears claim (N-15/N-16) |

### Evidence JSON schemas  *(ADDED — proposal; field names follow §Canonical evidence types)*
```json
{"muse_session":  {"attempt_id":"…","muse_session_id":"…","supersedes":null,"recovery_mode":"fresh|resume|respawn","worktree":"/srv/rouvr/workspaces/<task>/<attempt>"},
 "muse_progress": {"attempt_id":"…","seq":3,"summary":"redacted text","files_changed":["…"],"head_sha":"…"},
 "pr_record":     {"attempt_id":"…","branch":"…","pr_url":"…","pr_number":12,"observed_at":"pre_check|post_exit"},
 "test_evidence": {"attempt_id":"…","run_id":"…","passed":41,"failed":0,"skipped":2,"report_ref":"…","candidate_sha":"…"},
 "review_verdict":{"attempt_id":"…","n":1,"decision":"approve|rework","reasons":["…"],"verifier_principal":"…","cited":["testrun:<id>","pr:<attempt>"]}}
```

### Phase 2 Rouvr-owned tables (DDL)  *(ADDED — proposal; the doc lists these as "phase 2, kept in the design but not built")*
```sql
-- Lives in WorkHub's Postgres (or a Rouvr schema beside it) so the outbox is in the same transaction.
CREATE TABLE step_log (
  id             BIGSERIAL PRIMARY KEY,
  tenant_id      TEXT NOT NULL,
  task_id        TEXT NOT NULL,
  attempt_id     TEXT NOT NULL,
  activity_id    TEXT NOT NULL,            -- rouvr-activity-<name>-<sha20>
  activity_name  TEXT NOT NULL,
  outcome        TEXT NOT NULL CHECK (outcome IN ('ok','failed','replayed','cancelled')),
  reason_code    TEXT,
  evidence_refs  TEXT[] NOT NULL DEFAULT '{}',
  correlation_id TEXT NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, activity_id)
);
CREATE INDEX step_log_task_idx ON step_log (tenant_id, task_id, created_at);

CREATE TABLE outbox (
  id            BIGSERIAL PRIMARY KEY,
  tenant_id     TEXT NOT NULL,
  event_id      UUID NOT NULL UNIQUE,      -- consumers dedupe on this
  aggregate     TEXT NOT NULL,             -- 'task'
  aggregate_id  TEXT NOT NULL,
  event_type    TEXT NOT NULL,             -- task.assigned | task.blocked | task.done | schedule.fired
  payload       JSONB NOT NULL,            -- secret-free
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at  TIMESTAMPTZ,
  attempts      INT NOT NULL DEFAULT 0
);
CREATE INDEX outbox_unpublished_idx ON outbox (created_at) WHERE published_at IS NULL;

CREATE TABLE escalations (
  id           BIGSERIAL PRIMARY KEY,
  tenant_id    TEXT NOT NULL,
  task_id      TEXT NOT NULL,
  dedupe_key   TEXT NOT NULL,              -- sha256(tenant|task|reason|contract_rev)
  reason       TEXT NOT NULL,              -- rework_limit | grant_refused | contract_moved | ...
  recipient    TEXT NOT NULL,              -- F1 recipient-bound owner
  status       TEXT NOT NULL CHECK (status IN ('pending','sent','acknowledged','resolved')),
  sent_at      TIMESTAMPTZ,
  resolved_at  TIMESTAMPTZ,
  UNIQUE (tenant_id, dedupe_key)
);
CREATE INDEX escalations_open_idx ON escalations (tenant_id, status) WHERE status <> 'resolved';

CREATE TABLE receipts (
  id               BIGSERIAL PRIMARY KEY,
  tenant_id        TEXT NOT NULL,
  task_id          TEXT NOT NULL,
  contract_rev     TEXT NOT NULL,
  attempt_id       TEXT NOT NULL,
  workflow_run_id  TEXT NOT NULL,
  muse_session_id  TEXT NOT NULL,
  pr_url           TEXT NOT NULL,
  test_run_id      TEXT NOT NULL,
  digest_sha256    CHAR(64) NOT NULL,      -- hash of the fields above
  signature        BYTEA NOT NULL,         -- service key (KMS/host key), key id below
  key_id           TEXT NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, task_id, attempt_id)
);

CREATE TABLE recurring_schedules (
  id               TEXT PRIMARY KEY,       -- also the Temporal schedule id
  tenant_id        TEXT NOT NULL,
  template_task_id TEXT NOT NULL,          -- proven task whose contract is reused
  approved_revision TEXT NOT NULL,         -- contract revision granted once
  cron             TEXT NOT NULL,
  timezone         TEXT NOT NULL DEFAULT 'Asia/Kolkata',
  enabled          BOOLEAN NOT NULL DEFAULT false,
  last_fired_at    TIMESTAMPTZ,
  created_by       TEXT NOT NULL
);
CREATE INDEX recurring_enabled_idx ON recurring_schedules (tenant_id) WHERE enabled;
-- Fencing (phase 2): WorkHub lease writes carry lease_epoch; UPDATE ... WHERE lease_epoch = $fence, 0 rows → 409.
```

### Keys and identity
| Key | Format | Uniqueness scope | Purpose |
|---|---|---|---|
| `workflow_id` | `rouvr-task-<tenant>-<task_id>-<sha16>` via `workflow_id_for_task(tenant_id, task_id)` (see §LLD core modules) | Tenant+task | Exactly one workflow per task; `REJECT_DUPLICATE` on start |
| `activity_id` / idempotency key | `rouvr-activity-<name>-<sha20>` via `activity_id_for(task_id, activity_name, step_id, attempt_id)` | Task+activity+step+attempt | Backend dedupe; in-worker idempotency store replays the stored envelope with `idempotent_replay=true` |
| WorkHub mutation key | Caller-supplied `Idempotency-Key` header; required on every non-GET | Per endpoint row | The WorkHub client refuses keyless mutations with `KEY.REQUIRED` before sending |
| Handoff fence key | `temporal-handoff-<controller_attempt>-e<epoch>` | Attempt+epoch | Exact-equality fence; no substring inference |
| Close keys | Distinct fresh `EVAL_KEY`, `WALK_KEY`, batch key `WALK_KEY:batch` | Per close | Close refuses unless eval and walk keys differ; every key is single-use |

**Phase 2, kept in the design but not built:** fencing tokens on lease writes (stale writes get 409), step log table, signed receipts (a hash of task, contract revision, attempt, run, session, PR and test run, signed with a service key), escalations with a unique dedupe key, an outbox written in the same transaction as the state change, and approved_revision / recurring_schedule on tasks.

## LLD: API mapping (WorkHub, plus small fixes)
### Needs → capability → fix
| Need | WorkHub capability | Fix needed |
|---|---|---|
| Create a task with persona, repo, branch, acceptance | Task create is in the OpenAPI. The assignment claim carries persona, repository binding, authority and acceptance refs. | **Small fix:** create and bind atomically in one call, or a single idempotent create-and-assign |
| Assignment claim | In the OpenAPI; live binding read `GET /api/v1/projects/{p}/tasks/by-number/{n}/assignment-binding` | Check builder ≠ verifier on the verifier claim (UNVERIFIED whether it exists) |
| Workspace lease claim/renew | Lease endpoints in the OpenAPI | None. Called from the activity heartbeat. |
| Attempt create | In the OpenAPI | Store session ID, run ID and PR URL (UNVERIFIED) |
| Liveness renewal | Exists | None |
| Result submit | In the OpenAPI | None (UNVERIFIED end to end from a workflow) |
| Dead-assignment reclaim | Reclaim endpoint in the OpenAPI | Confirm it works with Temporal retry (UNVERIFIED) |
| Close | `evaluate → batch-transition → readback` order (see M-09) | Callable as an activity (UNVERIFIED) |
| Escalation | Durable blockers exist | Phase 2: one-shot dedupe |
| Read task / list tasks | `GET` by-number / by-id / list (see routes below) | None |

### WorkHub routes Rouvr calls
All calls carry `Authorization: Bearer <jwt>`, `X-Hyeve-Tenant-Id`, `X-Project-ID`. Every mutation carries a fresh `Idempotency-Key`; keyless mutations are refused client-side with `KEY.REQUIRED` before any request is sent. Auth: `POST /v1/auth/login` (password, legacy) or `POST /v1/auth/service-tokens` (`service_credential_id` + `client_secret` + `audience`, preferred); JWT lives in memory only.

| # | Method + path | Caller | Request (key fields) | Success | Failure signals |
|---|---|---|---|---|---|
| R1 | `GET /api/v1/projects/{p}/tasks/by-number/{n}` | Intake, claim, close | — | 200 task (`id`, `task_number`, `status`, `record_version`, `active_execution_contract_ref`) | 404 unknown task; 403 `preclaim-task-read-refused` (N-02) |
| R2 | `GET /api/v2/workhub/tasks/{id}` | Claim, close | — | 200 compact shape (`task_id` + `task_number`); enriched via R1 for `record_version` | Same as R1 |
| R3 | `GET /api/v1/projects/{p}/tasks` | Rouvr readback, list | — | 200 items | 403 read-refused |
| R4 | `GET /api/v1/projects/{p}/tasks/by-number/{n}/assignment-binding` | Preflight (every effect), claim | — | 200 live binding tuple | 404 `assignment_binding_not_found` = no active assignment (recovery gate); field mismatch = moved (N-05/N-06) |
| R5 | `POST /api/v1/tasks/{id}/evidence` | assignTask, test, verify | `project_id`, `evidence_type`, `evidence_ref`, `evidence_json`, `artefact_id?` | 201 row; caller re-lists and demands exact readback (same `id`, same type/ref, stored JSON ⊇ submitted JSON) | `evidence_exact_readback_missing/mismatch` (N-13); replay flagged `idempotent_replay` |
| R6 | `GET /api/v1/tasks/{id}/evidence?project_id={p}&limit=500&offset=0` | Preflight, verify, close | — | 200 rows (handoff receipt, execution_spec, test evidence) | Read refusal fails the effect closed |
| R7 | `POST /api/v1/tasks/{id}/verification/evaluate` | Verify/close | `project_id`, `decision`, `checks[]` (each `artifact_ref` must already be an evidence `id`/`evidence_ref` on the task) | 200 `{eligible_for_done, id}` | `verification_checks_required`, `artifact_ref_not_on_task:*`, `evaluation_not_eligible_for_done` (N-09) |
| R8 | `POST /api/v1/projects/{p}/tasks/transitions/batch` | Close | `project_id`, `mode:"partial"`, `operation_id:<walk>:batch`, `items:[{item_id, idempotency_key:<walk>, task_id, status:"done", reason, expected_record_version}]` | `{updated:1}` then terminal readback `status=done` | `transition_not_confirmed`, `terminal_readback_failed`, CAS conflict on `record_version` (N-14) |
| R9 | `GET /api/v1/capabilities` | Health probe | — | 200 | Non-2xx = control-plane degraded |
| R10 | Task create + assignment claim + lease claim/renew + attempt create + result submit | Intake / claim / heartbeat / close | Per OpenAPI + task-2 fields | Per OpenAPI | See error taxonomy; exact shapes **UNVERIFIED** (task 2) |

### Workflow start (new, thin) — chosen start path (OD2, 29 Oct 2026)
Rouvr calls this thin endpoint to start every workflow. The outbox-driven starter is later/optional and Rouvr does not start workflows through the SDK directly. The scaffold implements a minimal subset contract in `rouvr/api.py`: `POST /workflows/start`, `Authorization: Bearer`, body `{task_id, contract_rev, requested_by}` → `200 {workflow_id, started}`, `400 E_BAD_REQUEST`, `401 E_AUTHZ`. The full contract below is the target. *(Proposal: the request fields beyond task_id may be resolved server-side from WorkHub.)*

`rouvr start <task_id>` (CLI) or `POST /workflows/start {task_id}` → `{workflow_id, run_id, started: true|false}`. Both call Temporal start with `id = workflow_id_for_task(tenant_id, task_id)` and `id_reuse_policy = REJECT_DUPLICATE`. `AlreadyStarted` returns the existing run (`started:false`); it is not an error (N-01). Input is the frozen `TaskWorkflowInput` contract (`contract_version`, `workflow_version ≥ 1`, `tenant_id`, `task_id`, `candidate_sha ≥ 7 chars`, `plan_sha256` 64-hex, `start_request_id`, `correlation_id`, plus owner refs `assignment_id`, `workhub_attempt_id`, `accepted_model_id`, `worker_id` when bound). See M-01 for the starter implementation.

### Error taxonomy (WorkHub → Rouvr mapping; task 2 confirms exact strings)
v1's error contract is the reference for this mapping, not a new API. Full retry/non-retry table is in §LLD caching, queues, errors; the mapping rules:

| WorkHub signal | Rouvr code | Class | Handling |
|---|---|---|---|
| `400 invalid_contract` / `BINDING_*` / `SELECTION_BINDING_MISMATCH` | `E_CONTRACT` | Non-retryable | Fail activity → `mark_blocked(task, "contract")` (H-01 aborts before any spawn) |
| `403 repo_not_allowed` / grant refusal / `preclaim-task-read-refused` | `E_AUTHZ` | Non-retryable | Fail closed, no spawn, `mark_blocked(task, "grant_refused")` (N-02) |
| `403 builder==verifier` on verifier claim | `E_SELF_REVIEW` | Non-retryable | Refuse verify dispatch; re-claim with a different principal (N-08) |
| `404 assignment_binding_not_found` | `E_NO_ASSIGNMENT` | Situational | Expected on the recovery gate (proves no active assignment); elsewhere → re-claim (N-05) |
| `409` assignment/lease moved, `record_version` CAS conflict | `E_MOVED` | Non-retryable (re-read, don't retry blind) | Re-read live state; re-claim or re-evaluate once, then block (N-05/N-06/N-14) |
| `422 unapproved_revision` / contract moved | `E_CONTRACT_MOVED` | Non-retryable | Block; phase 2 re-approves the revision |
| `429` / model 429/5xx | `E_THROTTLE` | Retryable | Exponential backoff + jitter within activity policy |
| `503` / WorkHub 5xx / network errors | `E_DOWNSTREAM` | Retryable | Exponential backoff + jitter; heartbeat timeout triggers same-attempt recovery |
| Malformed payload / `KEY.REQUIRED` / `PAYLOAD.*` | `E_BAD_REQUEST` | Non-retryable (caller bug) | Fix caller; never retry the same bytes |

### Rouvr control API (full contract)  *(ADDED — proposal; extends "Workflow start (new, thin)". CLI and HTTP are equivalent)*
All endpoints: `Authorization: Bearer <service jwt>`, `X-Hyeve-Tenant-Id`; mutations require `Idempotency-Key`. Errors use `{"code":"E_*","message":"…","retryable":bool,"correlation_id":"…"}`.

| Endpoint / CLI | Request | 200/201 | Errors |
|---|---|---|---|
| `POST /workflows/start` · `rouvr start <task_id>` | `{task_id, candidate_sha, plan_sha256, start_request_id}` | `{workflow_id, run_id, started}` (`started:false` on duplicate, N-01) | 400 `E_BAD_REQUEST` (validation), 403 `E_AUTHZ`, 404 task unknown, 409 `E_CONTRACT` (no pinned `execution_spec`), 503 `E_DOWNSTREAM` (Temporal down) |
| `GET /workflows/{task_id}` · `rouvr status` | — | `{phase, attempt_id, rework, recovery_count, pr_url, last_reason_code}` (from `query_status`) | 404 not started |
| `POST /workflows/{task_id}/cancel` · `rouvr cancel` | `{reason}` | `{phase:"CANCELLED" \| "cancel_requested_during_activity"}` (N-15) | 409 already terminal |
| `POST /workflows/{task_id}/resolve` (phase 2) | `{resolution, approved_revision?}` | `{phase:"READY_TO_RETRY"}` | 409 not `BLOCKED`, 422 `E_CONTRACT_MOVED` |
| `POST /schedules` (out of scope, OD1) | `{template_task_id, approved_revision, cron, timezone}` | `{schedule_id, enabled:false}` | 403 template not proven/closed `done`, 422 revision mismatch |
| `POST /schedules/{id}:enable` / `:pause` (phase 2) | — | `{enabled}` | 404 |
| `POST /escalations/{id}:ack` (phase 2) | — | `{status:"acknowledged"}` | 404, 409 already resolved |
| `GET /healthz` | — | `{temporal:ok, workhub:ok(R9), pollers:{queue:n}}` | 503 degraded |

Signals/queries on `TaskWorkflow` (stated in core modules): `signal_cancel(reason)`, `query_status`; proposal adds `signal_resolve(resolution)` for phase 2.

## LLD: sequences
Path IDs: H-01/H-02 are happy paths; N-01…N-16 are non-happy paths. Each names its preconditions, steps, postconditions and owning module.

### H-01 Happy path: intake → build → test → verify → close
Pre: user instruction + acceptance criteria exist. Post: task `done`, result + all evidence in WorkHub, Rouvr readback shown to the user.
```mermaid
sequenceDiagram
  participant U as User
  participant R as Rouvr
  participant WH as WorkHub
  participant T as Temporal
  participant A as assignTask
  participant M as Muse (coder)
  participant TR as Test runner
  participant V as Verifier
  U->>R: instruction + acceptance
  R->>WH: create task (persona, repo, branch, acceptance) [R10, idempotent]
  WH-->>R: task_id + task_number + record_version
  R->>WH: attach execution_spec (selection block pinned to contract_ref) [R5]
  R->>T: StartWorkflow(id=wf(task_id), REJECT_DUPLICATE)
  T-->>R: run_id
  T->>WH: claim assignment + lease, open attempt, file temporal_handoff receipt
  T->>A: assignTask(task, attempt, heartbeat 90s, max 3 attempts)
  A->>A: resolve persona (M-04) + exec spec (M-05) + workspace (M-06)
  A->>WH: preflight verify_binding(live reads) [R4+R6] — refuse on any mismatch
  A->>GH: open_pr_for_branch? none → continue
  A->>WH: record muse_session_id [R5 muse_session]
  A->>M: spawn turn (bounded, denyUnmatched + :read-only posture)
  loop each heartbeat (~30s)
    A->>WH: renew lease
    A->>T: heartbeat(session, lease_epoch)
  end
  M->>WH: progress evidence (muse_progress)
  M-->>A: turn completed, exit 0, observed diff
  A->>GH: open_pr_for_branch? new → record pr_url immediately [R5 pr_record]
  T->>TR: run_tests(task, attempt) → test evidence [R5 test_evidence]
  T->>V: verify(task, attempt, evidence) under a DIFFERENT principal
  V->>WH: read diff + test evidence [R6]
  V-->>T: approve + verdict evidence [R5 review_verdict]
  T->>WH: result submit (approved) + evaluate eligible_for_done [R7]
  T->>WH: batch transition → done with expected_record_version [R8]
  T->>WH: terminal readback status=done
  R->>WH: read result + evidence
  R-->>U: "Done. No action needed."
```

### H-02 Happy path: verifier sends back once, second pass approves
Pre: H-01 through first verify. Post: rework bounded (`rework ≤ MAX_REWORK`), second attempt approved and closed.
```mermaid
sequenceDiagram
  participant T as Temporal
  participant A as assignTask
  participant TR as Test runner
  participant V as Verifier
  participant WH as WorkHub
  T->>V: verify(attempt 1)
  V-->>T: rework + reasons (verdict evidence)
  T->>T: rework=1 ≤ MAX → open attempt 2 (new session id, same branch)
  T->>A: assignTask(attempt 2, prompt = verdict reasons + prior progress)
  A-->>T: exit 0 (existing-PR check finds PR from attempt 1, no duplicate)
  T->>TR: run_tests(attempt 2)
  T->>V: verify(attempt 2)
  V-->>T: approve
  T->>WH: result + close
```

### N-01 Duplicate workflow start (idempotent admission)
Pre: workflow for task T already running. Trigger: operator double-starts, CLI retry. Steps: `StartWorkflow(id=wf(T), REJECT_DUPLICATE)` raises `AlreadyStarted` → starter returns `{workflow_id, existing run_id, started:false}`. Post: exactly one run in the Temporal UI; no second claim, no second attempt. Owner: M-01 admission.

### N-02 Grant/claim refusal (canary's `preclaim-task-read-refused`)
Pre: principal lacks read/claim grant. Trigger: R1/R4 returns 403. Steps: (1) activity fails with `E_AUTHZ` (non-retryable); (2) no workspace staging, no spawn — refusal precedes every effect; (3) workflow calls `mark_blocked(task, "grant_refused")` with the refusal code; (4) phase 1 stops (no retry); phase 2 would `escalate_once`. Post: task `blocked` with the refusal as evidence; zero model turns ran. Owner: M-02 ownership + M-03 pre-effect gates.

### N-03 Worker killed mid-run, resume path (spike proves resume)
Pre: `assign_task` running session S, heartbeats flowing. Trigger: worker process/host dies.
```mermaid
sequenceDiagram
  participant T as Temporal
  participant A as assignTask (new worker)
  participant WH as WorkHub
  participant M as Muse
  Note over T,M: worker killed; heartbeats + lease renewal stop
  T->>T: heartbeat timeout (90s) → retry activity, SAME attempt_id
  T->>A: assignTask(same attempt)
  A->>WH: read S, pr_url, progress; preflight live binding [R4+R6]
  alt lease expired/lost
    A->>WH: re-claim (or dead-assignment reclaim path)
  end
  A->>M: muse exec --resume S (same worktree dir)
  M-->>A: exit 0, continues with prior context
```
Post: attempt completes once; no duplicate PR (pre-check runs again before any push); `recovery_count+1` in history. Owner: M-07 recovery.

### N-04 Worker killed mid-run, respawn fallback (spike fails resume)
Same trigger as N-03, but `muse exec --resume S` is unavailable/unsound. Steps: (1) same heartbeat-timeout retry on the same attempt; (2) same preflight + re-claim; (3) new session id `S'` recorded on the SAME attempt (supersedes S, S retained for audit); (4) prompt = WorkHub progress evidence + verdict context + `"PR #N exists, continue on branch B, do not open a second PR"`; (5) same persistent worktree dir, so files survive. Post: as N-03. The demo names the path honestly. Owner: M-07 recovery.

### N-05 Lease lost or assignment moved mid-run
Pre: `assign_task` looping. Trigger: renew returns 409/lease-moved, or preflight `BINDING_LEASE_MOVED`/`BINDING_ASSIGNMENT_MOVED`. Steps: (1) stop the turn promptly (cancel owns the worker: shielded await, stop signal asserted, process group reaped bounded, unproven shutdown refuses); (2) re-read live binding [R4]; (3a) if our claim is restorable → re-claim, resume loop; (3b) if another holder owns it → fail activity `E_MOVED`, do not spawn; (4) workflow either retries the activity once after re-claim or marks blocked. Post: at most one holder writes; stale writes never silently win (phase 2 makes this a fencing-token 409). Owner: M-02/M-03.

### N-06 Contract / authority moved mid-run
Trigger: preflight `BINDING_CONTRACT_MOVED` / `BINDING_ROUTE_FIELD_MISMATCH`, or 422 `unapproved_revision`. Steps: fail closed before spawn (or cancel the turn if mid-run, as N-05); `mark_blocked(task, "contract_moved")`. Post: no work under a stale contract; phase 2 re-approves the revision and resumes. Owner: M-03 gates.

### N-07 Muse turn failures (tool, budget, scope, transport)
Trigger: any post-turn explicit failure from the adapter: `MUSE_TOOL_FAILED` (a turn item reached a non-completed terminal status), `MUSE_BUDGET_EXCEEDED` (validated token usage overruns the step budget), `MUSE_PATH_ESCAPES_WORKSPACE` / `MUSE_PATH_OUT_OF_SCOPE` (worker output outside workspace/accepted scope), `EXECUTOR_MODEL_MISMATCH` (provider-echoed model ≠ bound route), `MUSE_WORKSPACE_TAINTED` (foreign symlink), transport `MuseSessionError` (gap, late activity, contradictory terminal, malformed frame, unclean exit). Steps: (1) `ok=False` with full evidence intact — never success from agent text alone; (2) evidence published to the supervisor-owned dir (never the worker tree); (3) activity fails retryable-or-not per code (`MUSE_TOOL_FAILED`/transport → retryable within policy; scope/budget/model-mismatch → non-retryable → rework-with-tightened-scope or blocked). Post: failure is evidence-backed and classified; no synthetic success. Owner: M-06 launcher.

### N-08 Verifier principal equals builder (self-review refused)
Pre: verify dispatch requested. Trigger: verifier claim principal == builder principal for the task. Steps: (1) refuse dispatch before any verify effect (`E_SELF_REVIEW`); (2) resolve a different verifier principal from the registry and re-claim once; (3) if no distinct principal is available → `mark_blocked(task, "no_independent_verifier")`. Post: no self-review ever runs. Owner: M-08 verify.

### N-09 Tests fail or evaluation ineligible
Trigger: test runner reports failures, or `evaluate_task` returns `eligible_for_done=false`, or `artifact_ref_not_on_task`. Steps: (1) test evidence still attached (failures are evidence); (2) verify returns rework with reasons, or close refuses `evaluation_not_eligible_for_done`; (3) workflow reworks (H-02) while `rework ≤ MAX_REWORK`, else `mark_blocked(task, "rework_limit")`. Post: nothing closes without eligible evidence. Owner: M-08/M-09.

### N-10 Rework budget exhausted
Pre: `rework > MAX_REWORK` (default 2, per-task overridable). Steps: `mark_blocked(task, "rework_limit")` with all verdicts attached; phase 2 sends `escalate_once` + owner signal. Post: task `blocked`, fully auditable. Owner: TaskWorkflow loop.

### N-11 Duplicate-PR race (two paths, one branch)
Pre: branch B has no PR, or PR appears between pre-check and push. Defences in order: (1) pre-launch `open_pr_for_branch` + immediate record; (2) one branch per task (collisions are same-branch, hence visible); (3) post-exit `open_pr_for_branch` + immediate record; (4) respawn prompt names the existing PR; (5) idempotency: a rerun that finds the recorded PR skips creation. Trigger outcome: at most one PR per attempt chain; a rerun never opens a second PR (acceptance: kill → recover → still one PR). Owner: M-03/M-07.

### N-12 Model / downstream 429-5xx and network errors
Trigger: Meta Model API 429/5xx, WorkHub 5xx, network errors. Steps: exponential backoff + jitter inside the activity's retry policy (see `retry_policy_for`); heartbeat timeout on `assign_task` triggers same-attempt recovery (N-03/N-04); long model-bearing calls other than `assign_task` are bounded by `start_to_close` only (no heartbeat timeout that turns slow models into false timeouts). Post: transient faults recover without operator input; persistent faults exhaust policy → blocked with the last error as evidence. Owner: §LLD caching, queues, errors.

### N-13 Evidence write/readback failure
Trigger: `attach_evidence` 201 but re-list misses the row, or stored JSON ⊉ submitted JSON, or `evidence_id_missing`. Steps: fail the writing activity (retryable once with the same idempotency key — byte-identical replay); if readback still fails → block with `evidence_unproven`. Post: no verdict, result or close ever cites unproven evidence. Owner: R5 caller + M-09.

### N-14 Close CAS conflict / terminal readback failure
Trigger: batch transition returns `updated≠1`, or final read shows `status≠done`, or `record_version` moved under us. Steps: (1) re-read task [R1]; (2) if `done` with our evaluation id → success (idempotent close); (3) else re-evaluate with fresh keys once; (4) still failing → `mark_blocked(task, "close_conflict")` — never force-write. Post: close is exactly-once per evaluation. Owner: M-09 close.

### N-15 Operator cancel
Trigger: `signal_cancel(reason)` at any non-terminal phase. Steps: waiting phases cancel immediately; `RUNNING_STEP`/`VERIFYING_STEP` record `cancel_requested_during_activity` and cancel after the activity boundary; `may_dispatch_activity` refuses all further dispatch once cancelled. Post: phase `CANCELLED`, lease released, no orphan spawn (cancel owns the worker per N-05). Owner: phase engine + M-06.

### N-16 Stuck / zombie claim and dead-worker reclaim
Trigger: worker dies without Temporal retry covering the window (host loss), or a claim outlives its lease. Steps: (1) lease expires (renewal died with the worker); (2) the dead-assignment reclaim endpoint re-claims (see `reclaim_dead_assignment`, **UNVERIFIED** with Temporal retry — task 4 proves it); (3) recovery continues on the same attempt (N-03/N-04); (4) cancel-clears-claim semantics: a cancelled run never leaves a claim behind. Post: no task is stuck `running` with no worker; no two workers hold the task. Owner: M-02/M-07.

### H-03 Scheduled recurring work (OUT OF SCOPE for now — OD1, 29 Oct 2026; kept for later)  *(ADDED — proposal for functional req 9)*
Pre: a template task closed `done`; owner granted its contract revision once; schedule enabled. Post: a new governed task closes with no live Rouvr session; the daily update is exception-only.
```mermaid
sequenceDiagram
  participant S as Temporal Schedule
  participant SW as ScheduledRunWorkflow
  participant WH as WorkHub
  participant T as TaskWorkflow
  participant O as Owner (F1)
  S->>SW: fire(schedule_id, fire_time)
  SW->>WH: create task from template (Idempotency-Key = schedule_id:fire_time)
  SW->>WH: check approved_revision == active_execution_contract_ref
  alt revision moved
    SW->>O: escalate_once(contract_moved)
  else ok
    SW->>T: start child id = wf(task_id), REJECT_DUPLICATE
    T-->>SW: done | blocked
    SW->>O: daily digest: "Done. No action needed." or exceptions only
  end
```

### H-04 One-shot escalation (phase 2)  *(ADDED — proposal for functional req 8)*
Trigger: any `mark_blocked(reason)`. Steps: (1) compute `dedupe_key = sha256(tenant|task|reason|contract_rev)`; (2) `INSERT … ON CONFLICT (tenant_id, dedupe_key) DO NOTHING` in the same transaction as the blocked transition + outbox row; (3) outbox relay publishes `task.blocked`; (4) F1 recipient-bound message sent once; (5) owner `resolve` → `signal_resolve` → `RESOLUTION_PENDING → READY_TO_RETRY → RETRYING → RUNNING_STEP` (stated chain). Post: one message per (task, reason, revision), no matter how many retries hit the block.
```mermaid
sequenceDiagram
  participant T as TaskWorkflow
  participant WH as WorkHub (+outbox, escalations)
  participant RL as Outbox relay
  participant F1 as F1 messaging
  participant O as Owner
  T->>WH: mark_blocked(reason) + escalation(dedupe) + outbox (one tx)
  RL->>WH: poll unpublished
  RL->>F1: send(event_id)
  F1->>O: one message
  RL->>WH: published_at = now()
  O->>T: resolve → signal_resolve
  T->>T: READY_TO_RETRY → RETRYING → RUNNING_STEP
```

### N-17 Outbox relay crash / redelivery (phase 2)  *(ADDED)*
Relay publishes then dies before `published_at`. Next poll republishes the same `event_id`; consumers (starter, F1) dedupe on `event_id`; the starter is idempotent anyway (`REJECT_DUPLICATE`). Post: at-least-once delivery, exactly-once effect.

### N-18 Temporal server unavailable  *(ADDED)*
`rouvr start` returns 503 `E_DOWNSTREAM`; Rouvr tells the user the task exists in WorkHub and retries start with the same `start_request_id`. Running activities cannot heartbeat → after restart Temporal times them out and recovers on the same attempt (N-03/N-04). Leases may expire meanwhile → re-claim path (N-16).

### N-19 WorkHub unavailable mid-run  *(ADDED)*
Lease renewal fails with 5xx → `E_DOWNSTREAM`, retried inside the heartbeat loop with backoff. If renewal cannot land before `lease_expires_at`, the activity stops the turn (owns the worker, as N-05) rather than run unleased; Temporal retries the same attempt when WorkHub is back. Every effect fails closed — no work without a live owner read.

### N-20 Credential revoked or persona removed mid-run  *(ADDED)*
Next WorkHub/GitHub call returns 401/403 → `E_AUTHZ` non-retryable; turn is stopped; `mark_blocked(task,"grant_refused")`; evidence keeps the refusal code. No fallback to another persona for the builder role (proposal: only the verifier re-resolves, N-08).

### N-21 Workflow code deploy with runs in flight  *(ADDED)*
Workflow changes are gated with `workflow.patched()` / `workflow_version` (stated in `TaskWorkflowInput.validate`); the replay test suite runs on recorded histories in CI before deploy; incompatible changes go to a new workflow type and old runs drain.

## LLD: core modules
Reference implementation. Class, function and method names below are normative for phase 1; path IDs in comments map each branch to its sequence in §LLD sequences. There is no `renew_lease_loop` child workflow: lease renewal lives inside `assign_task` (M-03), so the lease dies with the worker.

### C-00 Shared contracts (errors, inputs, identity)
```python
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

CONTRACT_VERSION = "rouvr.task-contract.v1"
WORKFLOW_TYPE = "TaskWorkflow"

MAX_REWORK = 2
LEASE_TTL_S = 120
HEARTBEAT_INTERVAL_S = 30
HEARTBEAT_TIMEOUT_S = 90


# --- errors: every refusal names its code; `retryable` decides Temporal retry ---
class RouvrError(Exception):
    code: str = "E_UNKNOWN"
    retryable: bool = False

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(self.code if not detail else f"{self.code}:{detail}")


class ContractError(RouvrError):
    code = "E_CONTRACT"              # 400, binding/selection mismatch (H-01 gate, N-06)


class ContractMovedError(RouvrError):
    code = "E_CONTRACT_MOVED"        # 422, live contract/authority moved (N-06)


class AuthzError(RouvrError):
    code = "E_AUTHZ"                 # 403, grant refusal, revoked key (N-02)


class SelfReviewError(RouvrError):
    code = "E_SELF_REVIEW"           # verifier == builder (N-08)


class MovedError(RouvrError):
    code = "E_MOVED"                 # 409 lease/assignment/CAS moved (N-05, N-14)


class NoAssignmentError(RouvrError):
    code = "E_NO_ASSIGNMENT"         # no active assignment; situational (N-05, N-16)


class EvidenceError(RouvrError):
    code = "E_EVIDENCE"              # readback failure; retried once, same key (N-13)
    retryable = True


class IneligibleError(RouvrError):
    code = "E_INELIGIBLE"            # tests fail / not eligible (N-09, N-10)


class ScopeError(RouvrError):
    code = "E_SCOPE"                 # path escape / budget / model mismatch (N-07)


class ThrottleError(RouvrError):
    code = "E_THROTTLE"              # 429, model 429/5xx (N-12)
    retryable = True


class DownstreamError(RouvrError):
    code = "E_DOWNSTREAM"            # WorkHub 5xx, network, worker death (N-03, N-04, N-12)
    retryable = True


class TransportError(RouvrError):
    code = "E_TRANSPORT"             # turn/transport failure (N-07)
    retryable = True


class BadRequestError(RouvrError):
    code = "E_BAD_REQUEST"           # caller bug; never retry the same bytes


class CancelledError(RouvrError):
    code = "E_CANCELLED"             # operator cancel (N-15)


class WorkHubHttpError(Exception):
    """Raw owner error, translated by `classify_http_error` before it crosses
    a module boundary. Never raised past the calling activity."""

    def __init__(self, status: int, code: str = "", detail: str = "") -> None:
        self.status = status
        self.code = code
        super().__init__(f"http_{status}:{code}:{detail}")


def classify_http_error(exc: WorkHubHttpError) -> RouvrError:
    """Map owner HTTP signals onto the Rouvr taxonomy (see §LLD API mapping)."""
    if exc.status == 400:
        return ContractError(exc.code)
    if exc.status == 403:
        return AuthzError(exc.code or "grant_refused")
    if exc.status == 404 and exc.code == "assignment_binding_not_found":
        return NoAssignmentError(exc.code)
    if exc.status == 404:
        return ContractError(exc.code or "not_found")
    if exc.status == 409:
        return MovedError(exc.code or "moved")
    if exc.status == 422:
        return ContractMovedError(exc.code or "unapproved_revision")
    if exc.status == 429:
        return ThrottleError(exc.code or "rate_limited")
    if exc.status >= 500:
        return DownstreamError(exc.code or f"http_{exc.status}")
    return BadRequestError(exc.code or f"http_{exc.status}")


# --- inputs: frozen, validated, secret-free ---
FORBIDDEN_PAYLOAD_KEYS = frozenset({
    "access_token", "api_key", "authorization", "caller_authorization",
    "client_secret", "credential", "credentials", "jwt",
    "password", "private_key", "provider_credential", "refresh_token",
    "secret", "token",
    "lifecycle_state", "workhub_status", "closed_at",  # WorkHub-owned facts
})


@dataclass(frozen=True, slots=True)
class TaskWorkflowInput:
    contract_version: str = CONTRACT_VERSION
    workflow_version: int = 1
    tenant_id: str = ""
    task_id: str = ""
    candidate_sha: str = ""
    plan_sha256: str = ""
    start_request_id: str = ""
    correlation_id: str = ""
    assignment_id: str = ""        # owner refs; empty until bound at claim
    workhub_attempt_id: str = ""
    accepted_model_id: str = ""
    worker_id: str = ""

    def validate(self) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ContractError(f"contract_version={self.contract_version!r}")
        if self.workflow_version < 1:
            raise ContractError("workflow_version<1")
        for name in ("tenant_id", "task_id", "start_request_id", "correlation_id"):
            if not getattr(self, name).strip():
                raise ContractError(f"{name} is empty")
        if len(self.candidate_sha) < 7:
            raise ContractError("candidate_sha too short")
        if not re.fullmatch(r"[0-9a-f]{64}", self.plan_sha256):
            raise ContractError("plan_sha256 must be 64 hex chars")


@dataclass(frozen=True, slots=True)
class ActivityInput:
    contract_version: str
    activity_name: str
    activity_id: str
    tenant_id: str
    task_id: str
    step_id: str | None
    attempt_id: str | None
    idempotency_key: str
    correlation_id: str
    causation_id: str
    payload_refs: dict[str, str]    # refs only; validated below

    def validate(self, known_activities: set[str]) -> None:
        if self.contract_version != CONTRACT_VERSION:
            raise ContractError("activity contract_version mismatch")
        if self.activity_name not in known_activities:
            raise BadRequestError(f"unknown activity {self.activity_name}")
        forbidden = set(self.payload_refs) & FORBIDDEN_PAYLOAD_KEYS
        if forbidden:
            raise BadRequestError(f"forbidden payload keys: {sorted(forbidden)}")
        if any(not v.strip() for v in self.payload_refs.values()):
            raise BadRequestError("empty payload ref value")


class ActivityOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ActivityResult:
    activity_id: str
    outcome: ActivityOutcome
    reason_code: str
    evidence_refs: tuple[str, ...] = ()
    idempotent_replay: bool = False


@dataclass(frozen=True, slots=True)
class ActivityContract:
    name: str
    start_to_close: timedelta
    heartbeat_timeout: timedelta | None
    maximum_attempts: int


ACTIVITY_CONTRACTS: dict[str, ActivityContract] = {
    "claim_assignment_and_lease": ActivityContract("claim_assignment_and_lease", timedelta(seconds=30), None, 3),
    "open_or_reuse_attempt":      ActivityContract("open_or_reuse_attempt", timedelta(seconds=30), None, 3),
    "assign_task":                 ActivityContract("assign_task", timedelta(seconds=1800), timedelta(seconds=90), 3),
    "run_tests":                   ActivityContract("run_tests", timedelta(seconds=900), None, 2),
    "verify":                      ActivityContract("verify", timedelta(seconds=900), None, 1),
    "record_result":               ActivityContract("record_result", timedelta(seconds=120), None, 2),
    "close_task":                  ActivityContract("close_task", timedelta(seconds=120), None, 2),
    "mark_blocked":                ActivityContract("mark_blocked", timedelta(seconds=30), None, 3),
}


def workflow_id_for_task(*, tenant_id: str, task_id: str) -> str:
    """One task maps to exactly one workflow id (N-01)."""
    raw = f"{tenant_id}|{task_id}"
    readable = re.sub(r"[^a-zA-Z0-9_-]+", "-", f"rouvr-task-{tenant_id}-{task_id}").strip("-")
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{readable[:180]}-{digest}"


def activity_id_for(*, task_id: str, activity_name: str,
                    step_id: str | None, attempt_id: str | None) -> str:
    """Stable idempotency key per (task, activity, step, attempt)."""
    identity = "|".join((task_id, activity_name, step_id or "-", attempt_id or "-"))
    digest = hashlib.sha256(identity.encode()).hexdigest()[:20]
    return f"rouvr-activity-{activity_name}-{digest}"
```

### TaskWorkflow (phases, run loop, signals, queries)
```python
from temporalio import workflow
from temporalio.common import RetryPolicy

Phase = str

TRANSITIONS: dict[str, frozenset[str]] = {
    "STARTING":           frozenset({"RUNNING_STEP", "CANCELLED", "FAILED"}),
    "RUNNING_STEP":       frozenset({"VERIFYING_STEP", "WAITING_BLOCKER", "SUSPENDED",
                                     "BUILDING_REVIEW", "GAPS_FOUND", "COMPLETED_FOR_REVIEW",
                                     "CANCELLED", "FAILED"}),
    "VERIFYING_STEP":     frozenset({"BUILDING_REVIEW", "RUNNING_STEP",  # rework re-enters
                                     "CANCELLED", "FAILED"}),
    "BUILDING_REVIEW":    frozenset({"COMPLETED_FOR_REVIEW", "GAPS_FOUND", "CANCELLED", "FAILED"}),
    "WAITING_APPROVAL":   frozenset({"RUNNING_STEP", "WAITING_BLOCKER", "BLOCKED", "CANCELLED", "FAILED"}),
    "WAITING_BLOCKER":    frozenset({"RUNNING_STEP", "BLOCKED", "CANCELLED", "FAILED"}),
    "SUSPENDED":          frozenset({"RUNNING_STEP", "READY_TO_RETRY", "CANCELLED", "FAILED"}),
    "REPAIRING":          frozenset({"RUNNING_STEP", "VERIFYING_STEP", "GAPS_FOUND", "CANCELLED", "FAILED"}),
    "CERTIFYING":         frozenset({"CERTIFIED", "GAPS_FOUND", "COMPLETED_FOR_REVIEW", "CANCELLED", "FAILED"}),
    "BLOCKED":            frozenset({"RESOLUTION_PENDING", "RUNNING_STEP", "WAITING_APPROVAL"}),
    "RESOLUTION_PENDING": frozenset({"READY_TO_RETRY", "CANCELLED", "FAILED"}),
    "READY_TO_RETRY":     frozenset({"RETRYING", "CANCELLED", "FAILED"}),
    "RETRYING":           frozenset({"RUNNING_STEP", "CANCELLED", "FAILED"}),
}
TERMINAL = frozenset({"COMPLETED_FOR_REVIEW", "CERTIFIED", "GAPS_FOUND",
                      "FAILED", "CANCELLED", "BLOCKED"})


@workflow.defn(name="TaskWorkflow")
class TaskWorkflow:
    """One workflow per task. H-01/H-02 run in `run`; every N-path either
    raises into this loop or is handled by the activity it names."""

    def __init__(self) -> None:
        self._phase: Phase = "STARTING"
        self._cancel_requested = False
        self._approval_ref: str | None = None
        self._blocker_ref: str | None = None
        self._rework = 0
        self._recovery_count = 0

    @workflow.run
    async def run(self, inp: TaskWorkflowInput) -> dict:
        """H-01 happy path; H-02 rework; N-10 budget. N-02 fails at the
        claim with zero turns spawned."""
        inp.validate()
        await self._act("claim_assignment_and_lease", inp)       # M-02
        while True:
            attempt = await self._act("open_or_reuse_attempt", inp)  # M-02; N-03/N-04 reuse
            self._transition("RUNNING_STEP", "assign")
            await self._act("assign_task", inp, attempt)          # M-03
            self._transition("VERIFYING_STEP", "test")
            test_evidence = await self._act("run_tests", inp, attempt)      # M-08; N-09
            verdict = await self._act("verify", inp, attempt, test_evidence)  # M-08; N-08
            if verdict.approved:
                self._transition("BUILDING_REVIEW", "close")
                await self._act("record_result", inp, attempt, verdict)  # M-09
                await self._act("close_task", inp)                # M-09; N-14 CAS + readback
                self._transition("COMPLETED_FOR_REVIEW", "done")
                return {"task_id": inp.task_id, "status": "done",
                        "rework": self._rework, "recovery_count": self._recovery_count}
            self._rework += 1                                     # H-02 re-enters; N-10 caps
            if self._rework > MAX_REWORK:
                await self._act("mark_blocked", inp, "rework_limit")
                self._transition("BLOCKED", "rework_limit")
                return {"task_id": inp.task_id, "status": "blocked", "reason": "rework_limit"}

    async def _act(self, activity_name: str, *args):
        """Dispatch one activity under its contract; refuses after cancel (N-15)."""
        ok, reason = self.may_dispatch(activity_name)
        if not ok:
            raise CancelledError(reason)
        contract = ACTIVITY_CONTRACTS[activity_name]
        return await workflow.execute_activity(
            activity_name, args,
            start_to_close_timeout=contract.start_to_close,
            heartbeat_timeout=contract.heartbeat_timeout,
            retry_policy=RetryPolicy(maximum_attempts=contract.maximum_attempts,
                                     backoff_coefficient=2.0),
        )

    def may_dispatch(self, activity_name: str) -> tuple[bool, str]:
        if self._cancel_requested or self._phase == "CANCELLED":
            return False, "CANCELLED_NO_FURTHER_DISPATCH"
        if self._phase in TERMINAL:
            return False, f"TERMINAL:{self._phase}"
        if activity_name not in ACTIVITY_CONTRACTS:
            return False, "ACTIVITY_NOT_REGISTERED"
        return True, "OK"

    def _transition(self, to: Phase, reason: str) -> None:
        allowed = TRANSITIONS.get(self._phase, frozenset())
        if to not in allowed and to not in ("FAILED", "CANCELLED"):
            raise BadRequestError(f"illegal transition {self._phase}->{to}")
        workflow.logger.info("transition %s->%s reason=%s", self._phase, to, reason)
        self._phase = to

    @workflow.signal
    async def signal_cancel(self, reason: str = "OPERATOR_CANCEL") -> None:  # N-15
        self._cancel_requested = True
        if self._phase in TERMINAL:
            return
        self._transition("CANCELLED", reason)

    @workflow.signal
    async def signal_approval(self, approval_ref: str) -> None:
        if self._cancel_requested:
            return
        if not approval_ref.strip():
            raise BadRequestError("empty approval_ref")
        self._approval_ref = approval_ref
        if self._phase == "WAITING_APPROVAL":
            self._transition("RUNNING_STEP", "APPROVAL_RECEIVED")

    @workflow.signal
    async def signal_blocker_resolved(self, blocker_ref: str) -> None:
        if self._cancel_requested:
            return
        if not blocker_ref.strip():
            raise BadRequestError("empty blocker_ref")
        self._blocker_ref = blocker_ref
        if self._phase == "WAITING_BLOCKER":
            self._transition("RUNNING_STEP", "BLOCKER_RESOLVED")

    @workflow.signal
    async def signal_recovered(self) -> None:
        """Called by the activity worker after N-03/N-04 same-attempt recovery."""
        self._recovery_count += 1

    @workflow.query
    def query_status(self) -> dict:
        return {"phase": self._phase, "cancel_requested": self._cancel_requested,
                "approval_ref": self._approval_ref, "blocker_ref": self._blocker_ref,
                "rework": self._rework, "recovery_count": self._recovery_count}
```

### M-01 Admission (starter)
> **OD2 (29 Oct 2026):** the thin endpoint `POST /workflows/start` (`rouvr/api.py`) is the start path Rouvr uses; it calls this starter. `rouvr start` stays as an operator CLI. Outbox-driven start: later/optional.

One starter function shared by `rouvr start <task_id>` and `POST /workflows/start`. `AlreadyStarted` is a readback, not an error (N-01). Any other start error fails without writing WorkHub state.
```python
from temporalio.service import WorkflowAlreadyStartedError


@dataclass(frozen=True, slots=True)
class StartResult:
    workflow_id: str
    run_id: str
    started: bool          # False when the run already existed


async def start_task_workflow(*, tenant_id: str, task_id: str,
                              candidate_sha: str, plan_sha256: str,
                              start_request_id: str, client) -> StartResult:
    inp = TaskWorkflowInput(
        tenant_id=tenant_id, task_id=task_id,
        candidate_sha=candidate_sha, plan_sha256=plan_sha256,
        start_request_id=start_request_id, correlation_id=task_id,
    )
    inp.validate()
    workflow_id = workflow_id_for_task(tenant_id=tenant_id, task_id=task_id)
    try:
        handle = await client.start_workflow(
            WORKFLOW_TYPE, inp, id=workflow_id,
            id_reuse_policy="REJECT_DUPLICATE", task_queue="rouvr-orchestrator",
        )
        return StartResult(workflow_id, handle.run_id, True)
    except WorkflowAlreadyStartedError:                            # N-01
        handle = client.get_workflow_handle(workflow_id)
        return StartResult(workflow_id, handle.run_id, False)
```
Acceptance: a second start returns the existing run; the Temporal UI shows one run per task.

### M-02 Ownership (claim, lease, attempt)
Ingress authority precedes any durable write, and authority is reverified against fresh reads because lease/contract may have moved during queueing. Renewal: `assign_task` renews on every ~30s heartbeat; lease TTL ~2 min. Worker death stops renewal → expiry → reclaim (N-16). Phase 2 adds fencing tokens (stale writes → 409).
```python
from temporalio import activity


@dataclass(frozen=True, slots=True)
class Attempt:
    attempt_id: str
    seq: int
    task_id: str
    tenant_id: str
    persona: str
    repo: str
    branch: str
    state: str = "open"
    muse_session_id: str | None = None
    superseded_session_ids: tuple[str, ...] = ()
    workflow_run_id: str = ""
    pr_url: str | None = None
    builder_principal: str = ""


@dataclass(frozen=True, slots=True)
class VerifiedBinding:
    task_id: str
    assignment_id: str
    workhub_attempt_id: str
    lease_epoch: int
    lease_holder: str
    authority_ref: str
    plan_digest: str


def verify_binding(*, carried: dict, live_task: dict,
                   live_binding: dict, evidence_rows: list[dict]) -> VerifiedBinding:
    """Exact-compare every carried field against fresh owner reads (R1/R4/R6).
    Refuses N-02/N-05/N-06 before any durable write or spawn. Caller-supplied
    dictionaries are never authority on their own."""
    if str(live_task.get("id")) != str(carried["task_id"]):
        raise ContractError("BINDING_TASK_MISMATCH")
    if str(live_task.get("tenant_id")) != str(carried["tenant_id"]):
        raise ContractError("BINDING_TENANT_MISMATCH")
    if str(live_task.get("active_execution_contract_ref")) != str(carried["contract_ref"]):
        raise ContractMovedError("BINDING_CONTRACT_MOVED")                 # N-06
    if str(live_binding.get("assignment_status")) != "active":
        raise MovedError("BINDING_ASSIGNMENT_NOT_ACTIVE")                  # N-05
    if int(live_binding.get("assignment_record_version", 0)) != int(carried["assignment_revision"]):
        raise MovedError("BINDING_ASSIGNMENT_MOVED")                       # N-05
    if int(live_binding.get("lease_epoch", 0)) != int(carried["lease_epoch"]):
        raise MovedError("BINDING_LEASE_MOVED")                            # N-05
    if str(live_binding.get("holding_principal")) != str(carried["lease_holder"]):
        raise MovedError("BINDING_LEASE_HOLDER_MISMATCH")
    for key in ("authority_ref", "plan_digest"):
        if str(live_binding.get(key)) != str(carried[key]):
            raise ContractMovedError(f"BINDING_{key.upper()}_MISMATCH")     # N-06
    if live_binding.get("route_digest") is not None and \
            str(live_binding["route_digest"]) != str(carried.get("route_digest", "")):
        raise ContractMovedError("BINDING_ROUTE_FIELD_MISMATCH")           # N-06
    if not has_exact_handoff_receipt(evidence_rows, carried):
        raise ContractError("BINDING_ATTEMPT_UNPROVEN")
    if not has_pinned_selection(evidence_rows, carried):
        raise ContractError("BINDING_SELECTION_UNPROVEN")
    return VerifiedBinding(task_id=str(carried["task_id"]),
                           assignment_id=str(carried["assignment_id"]),
                           workhub_attempt_id=str(carried["workhub_attempt_id"]),
                           lease_epoch=int(carried["lease_epoch"]),
                           lease_holder=str(carried["lease_holder"]),
                           authority_ref=str(carried["authority_ref"]),
                           plan_digest=str(carried["plan_digest"]))


def has_exact_handoff_receipt(rows: list[dict], carried: dict) -> bool:
    """True when a `temporal_handoff` row proves every attempt field: its fence
    equals the binding-reconstructed fence exactly, and its payload attempt
    fields equal the binding's exactly. No substring inference."""
    expected_fence = {
        "claim_id": str(carried["assignment_id"]),
        "fence_token": {"assignment_revision": int(carried["assignment_revision"]),
                        "lease_epoch": int(carried["lease_epoch"])},
        "idempotency_key": f"temporal-handoff-{carried['controller_attempt_id']}-e{carried['lease_epoch']}",
    }
    for row in rows:
        if row.get("evidence_type") != "temporal_handoff":
            continue
        payload = row.get("evidence_json")
        if not isinstance(payload, dict) or payload.get("effect_fence") != expected_fence:
            continue
        if all(payload.get(k) == carried.get(k) for k in
               ("workhub_attempt_id", "attempt_version", "executor_sub", "controller_attempt_id")):
            return True
    return False


def has_pinned_selection(rows: list[dict], carried: dict) -> bool:
    """True when the carried selection equals the `aix_selection` block of the
    single `execution_spec` row pinned to the live contract ref."""
    selection = carried.get("selection")
    if not isinstance(selection, dict):
        return False
    for key in ("tenant_id", "project_id", "task_id", "task_number"):
        if selection.get(key) != carried.get(key):
            return False
    live_ref = str(carried.get("contract_ref") or "")
    blocks = [p["aix_selection"] for row in rows
              if row.get("evidence_type") == "execution_spec"
              and isinstance((p := row.get("evidence_json")), dict)
              and str(p.get("contract_ref") or "") == live_ref
              and isinstance(p.get("aix_selection"), dict)]
    if len(blocks) != 1:
        return False
    return all(blocks[0].get(k) == selection.get(k) for k in
               ("tenant_id", "project_id", "task_id", "task_number", "task_id",
                "selection_id", "worker_id", "candidate_sha", "plan_sha256", "correlation_id"))


@activity.defn(name="claim_assignment_and_lease")
async def claim_assignment_and_lease(inp: TaskWorkflowInput) -> VerifiedBinding:
    """H-01 first effect. N-02 (403/grant refusal) fails here with zero turns."""
    wh = workhub_client_for(inp.tenant_id)
    try:
        task = await wh.get_task_by_number(inp.task_id)            # R1
        binding = await wh.get_assignment_binding(inp.task_id)     # R4
        rows = await wh.list_task_evidence(inp.task_id)            # R6
    except WorkHubHttpError as exc:
        raise classify_http_error(exc) from exc
    carried = build_carried_binding(inp, task, binding)
    verified = verify_binding(carried=carried, live_task=task,
                              live_binding=binding, evidence_rows=rows)
    await wh.file_handoff_receipt(inp.task_id, verified)           # + exact readback (N-13)
    activity.heartbeat("claimed")
    return verified


@activity.defn(name="open_or_reuse_attempt")
async def open_or_reuse_attempt(inp: TaskWorkflowInput) -> Attempt:
    """Idempotent on (task, step, attempt). Retries (N-03/N-04) return the
    stored attempt; a conflicting claim refuses without poisoning it."""
    store = attempt_store()
    key = ("attempt", inp.task_id, current_step_id(), current_attempt_seq())
    claimed, record = await store.claim_or_get(
        key, lambda: Attempt(attempt_id=new_attempt_id(inp), seq=current_attempt_seq(),
                             task_id=inp.task_id, tenant_id=inp.tenant_id,
                             persona="coder", repo=current_repo(), branch=current_branch(),
                             state="claimed"))
    if not claimed and record.task_id != inp.task_id:
        raise ContractError("ATTEMPT_BINDING_CONFLICT")
    return record


async def renew_lease(wh, attempt: Attempt) -> int:
    """Returns the new lease epoch. 409/moved raises MovedError (N-05)."""
    try:
        return await wh.renew_lease(attempt.task_id, holder=attempt.builder_principal)
    except WorkHubHttpError as exc:
        raise classify_http_error(exc) from exc


async def reclaim_dead_assignment(*, tenant_id: str, task_id: str) -> VerifiedBinding:
    """N-16: only after the lease expired — the live binding read must show
    no active assignment before the reclaim endpoint is called."""
    wh = workhub_client_for(tenant_id)
    try:
        live = await wh.get_assignment_binding(task_id)            # R4
    except WorkHubHttpError as exc:
        err = classify_http_error(exc)
        if not isinstance(err, NoAssignmentError):
            raise err from exc
        live = None
    if live is not None and live.get("assignment_status") == "active":
        raise MovedError("RECLAIM_REFUSED_ACTIVE_ASSIGNMENT")
    try:
        return await wh.reclaim_assignment(task_id)
    except WorkHubHttpError as exc:
        raise classify_http_error(exc) from exc
```
Acceptance: assignment, lease and attempt visible in WorkHub; killing the worker stops renewal and the lease expires or is reclaimed.

### M-03 assignTask (the governed effect)
Full step order. Every gate before spawn refuses without side effects; the two preflight gates use fresh live reads (nothing cached). Prompt assembly (respawn/rework): prior `muse_progress` evidence + verdict reasons + `"PR #N exists, continue on branch B"` + accepted scope. Secrets never enter the prompt. Idempotency (two layers): workflow-side calls keyed by `activity_id` (replay returns the stored envelope with `idempotent_replay=true`); side effects inside Muse (push/PR) guarded by the existing-PR checks + immediate recording + one branch per task (N-11).
```python
import asyncio


class RecoveryMode(StrEnum):
    RESUME = "resume"    # N-03: spike proved `muse exec --resume`
    RESPAWN = "respawn"  # N-04: fallback on the same attempt


@dataclass(frozen=True, slots=True)
class AssignTaskResult:
    attempt_id: str
    session_id: str
    mode: RecoveryMode
    pr_url: str | None
    files_changed: tuple[str, ...]
    evidence_refs: tuple[str, ...]


async def preflight(inp: TaskWorkflowInput, carried: dict) -> VerifiedBinding:
    """One preflight gate: fresh live reads (R1/R4/R6), exact compare, no cache.
    Called twice per effect — before staging and immediately before activation —
    because authority may move during staging."""
    wh = workhub_client_for(inp.tenant_id)
    try:
        live_task = await wh.get_task_by_number(inp.task_id)
        live_binding = await wh.get_assignment_binding(inp.task_id)
        rows = await wh.list_task_evidence(inp.task_id)
    except WorkHubHttpError as exc:
        raise classify_http_error(exc) from exc
    return verify_binding(carried=carried, live_task=live_task,
                          live_binding=live_binding, evidence_rows=rows)


@activity.defn(name="assign_task")
async def assign_task(inp: TaskWorkflowInput, attempt: Attempt) -> AssignTaskResult:
    """H-01 build. Gates 1–7 refuse before any spawn-adjacent effect."""
    if activity.is_cancelled():
        raise CancelledError("cancelled before spawn")                   # gate 1
    if not is_muse_model_id(inp.accepted_model_id):
        raise ContractError("MODEL_ROUTE_NOT_MUSE")                      # gate 2
    carried = build_carried_binding(inp)                                 # gate 3
    if not carried.get("workhub_attempt_id"):
        raise ContractError("WORKHUB_BINDING_MISSING")

    await preflight(inp, carried)                                        # GATE 1 (N-02/N-05/N-06)

    registry = persona_registry()
    persona = registry.resolve(persona_name=attempt.persona, tenant_id=inp.tenant_id)  # M-04
    persona.ensure_repo_allowed(attempt.repo)                            # gate 5; E_AUTHZ
    spec = exec_spec_provider().get(inp, attempt)                        # M-05
    workspace = prepare_workspace(inp, attempt, spec)                    # gate 6: staging
    ensure_workspace_isolated(workspace)                                 # gate 6; E_SCOPE on taint

    await preflight(inp, carried)                                        # GATE 2: fresh reads again

    wh = workhub_client_for(inp.tenant_id)
    github = github_client_for(persona)
    pr = await github.open_pr_for_branch(attempt.repo, attempt.branch)   # N-11 defence 1
    if pr is not None:
        await wh.record_pr(attempt, pr.url)                              # immediate record

    mode = RecoveryMode.RESUME if (config.RESUME_SUPPORTED and attempt.muse_session_id) \
        else RecoveryMode.RESPAWN                                        # N-03 vs N-04
    session_id = attempt.muse_session_id or new_session_id(inp, attempt)
    await wh.save_session(attempt, session_id, supersedes=attempt.muse_session_id)
    prompt = build_task_prompt(inp, attempt, spec, pr)

    turn_handle = await muse_launcher().start_turn(                      # M-06: one bounded turn
        TurnParams(model_id=persona.model, workspace=workspace, prompt=prompt,
                   session_id=session_id, mode=mode,
                   approval_mode="denyUnmatched", permission_profile=":read-only",
                   token_budget=spec.token_budget, timeout_seconds=spec.turn_timeout_s))
    try:
        while not turn_handle.done():                                    # M-02 renewal loop
            try:
                epoch = await renew_lease(wh, attempt)
            except (MovedError, NoAssignmentError) as exc:               # N-05
                await turn_handle.cancel()                               # cancel owns the worker
                raise exc
            activity.heartbeat({"session": session_id, "lease_epoch": epoch})
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
    except asyncio.CancelledError:                                        # N-15 mid-turn
        await turn_handle.cancel()
        raise CancelledError("cancelled during turn")

    turn = turn_handle.result()          # raises TransportError/ScopeError with evidence (N-07)
    post = await github.open_pr_for_branch(attempt.repo, attempt.branch)  # N-11 defence 3
    pr_url = attempt.pr_url
    if post is not None and pr is None:
        await wh.record_pr(attempt, post.url)
        pr_url = post.url
    return AssignTaskResult(attempt_id=attempt.attempt_id, session_id=session_id,
                            mode=mode, pr_url=pr_url,
                            files_changed=turn.files_changed, evidence_refs=turn.evidence_refs)
```

### M-04 Persona registry (claim-time bindings)
> **OD3 (29 Oct 2026, owner decision): source = the existing AIX mapping.** `rouvr/personas.py::load_aix_mapping` reads it (env `PERSONA_SOURCE=aix`, `AIX_MAPPING_PATH`). **ASSUMPTION (UNVERIFIED):** the mapping format is `{"roles": {"builder"|"reviewer"|"closer": {principal, creds_file, model?}}}`. The port map only shows a static role → principal mapping (`bridge_creds.py`, `LANE_WORKHUB_CREDS`), so confirm the format in R6. AIX `reviewer` maps to Rouvr `verifier`. It has one tenant, and the model defaults to wonder or spark per role. The YAML below remains as the fallback (`PERSONA_SOURCE=yaml`).

Source (original design, now fallback): one tenant-keyed host-local YAML (refs only, never secrets); secrets resolve on the host from the vault/per-persona env file at launch. Cache 5-min TTL, invalidate on config change; secrets never cached beyond launch. Claim-time rule: any worker process can run any persona it is handed; bots stay stateless and role-agnostic.
```python
@dataclass(frozen=True, slots=True)
class Persona:
    name: str
    role: str                  # planner | builder | verifier | closer | curator
    muse_login_ref: str        # ref only, never a secret; doubles as the principal id
    workhub_cred_ref: str      # ref only, never a secret
    allowed_repos: tuple[str, ...]
    model: str
    autonomy_cap: str          # L0 | L1 | L2

    def ensure_repo_allowed(self, repo: str) -> None:
        if repo not in self.allowed_repos:
            raise AuthzError(f"repo_not_allowed:{repo}")

    def resolve_muse_login(self, host_vault) -> str:
        """Secrets resolve on the host at launch; never cached beyond it."""
        return host_vault.read(self.muse_login_ref)

    def resolve_workhub_creds(self, host_vault):
        return host_vault.read(self.workhub_cred_ref)


ROLE_AUTHORITY: dict[str, dict[str, frozenset[str]]] = {
    "planner":  {"allowed": frozenset({"propose_decomposition", "request_work"}),
                 "denied": frozenset({"execute_assignment", "accept", "reject", "close"})},
    "builder":  {"allowed": frozenset({"execute_assignment", "write_evidence", "report_result"}),
                 "denied": frozenset({"accept", "reject", "close", "propose_decomposition"})},
    "verifier": {"allowed": frozenset({"accept", "reject", "report_result"}),
                 "denied": frozenset({"execute_assignment", "close", "propose_decomposition"})},
    "closer":   {"allowed": frozenset({"close"}),
                 "denied": frozenset({"execute_assignment", "accept", "reject"})},
    "curator":  {"allowed": frozenset({"summarize_evidence"}),
                 "denied": frozenset({"execute_assignment", "accept", "reject", "close"})},
}


def can_act(*, role: str, action: str) -> bool:
    """Pure, deny-wins authority check."""
    record = ROLE_AUTHORITY.get(role)
    if record is None or action in record["denied"]:
        return False
    return action in record["allowed"]


class PersonaRegistry:
    def __init__(self, tenants: dict[str, dict[str, Persona]]) -> None:
        self._tenants = tenants

    def resolve(self, *, persona_name: str, tenant_id: str) -> Persona:
        try:
            return self._tenants[tenant_id][persona_name]
        except KeyError:
            raise ContractError(f"unknown persona {tenant_id}/{persona_name}") from None

    def verifier_for(self, *, tenant_id: str, builder_principal: str) -> Persona:
        """N-08: the verifier principal must differ from the builder's."""
        for persona in self._tenants[tenant_id].values():
            if persona.role == "verifier" and persona.muse_login_ref != builder_principal:
                return persona
        raise SelfReviewError("no_independent_verifier")
```
Acceptance: coder edits + opens one PR under the coder login; verifier reviews under a different login; no secret appears in specs, prompts, logs or evidence.

### M-05 Exec spec provider (non-secret policy slot)
```python
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ExecSpec:
    instructions: str
    scope_files: tuple[str, ...]
    token_budget: int | None
    turn_timeout_s: float
    model_route: str


class ExecSpecProvider(Protocol):
    def get(self, inp: TaskWorkflowInput, attempt: Attempt) -> ExecSpec: ...


class LocalExecSpecProvider:
    """Phase-1 default: task intent + acceptance + repo instruction files.
    Resolves non-secret policy only — never credentials or scheduling."""

    def get(self, inp: TaskWorkflowInput, attempt: Attempt) -> ExecSpec:
        task = read_task_snapshot(inp.task_id)     # intent + acceptance + scope
        repo_docs = read_repo_instructions(attempt.repo, attempt.branch)  # AGENTS.md / MUSE.md
        return ExecSpec(
            instructions=f"{task.intent}\n\nAcceptance:\n{task.acceptance}\n\n{repo_docs}",
            scope_files=tuple(task.accepted_scope),
            token_budget=task.token_budget,
            turn_timeout_s=1500.0,
            model_route=inp.accepted_model_id or "muse-wonder-1.0",
        )
```
- SDK slot: a drop-in `ExecSpecProvider` returning the same shape; it resolves non-secret policy only. SDK acceptance (to displace the default): branch + commit + one live run (SDK in → Muse edits → result out). Otherwise the local default ships.
- Capability posture is owner-supplied (`MuseCapabilityPolicy`, default `denyUnmatched` + `:read-only`); `allowAll` and `:unrestricted` are refused at wiring time, never silently selected. Binding posture to the accepted plan is a phase-2 plan-owner decision.

### M-06 Muse launcher (one bounded turn per attempt)
```python
APPROVAL_MODES = frozenset({"allowAll", "promptUnmatched", "onRequest", "denyUnmatched"})
MATERIALIZE_MAX_FILES = 10_000
MATERIALIZE_MAX_BYTES = 256 * 1024 * 1024


class MuseSessionError(Exception):
    """Transport refusal. Mapped to TransportError by the launcher."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(code if not detail else f"{code}:{detail}")


def normalize_model_id(value: str) -> str:
    return value.strip().lower().removeprefix("openrouter/")


def is_muse_model_id(value: str) -> bool:
    family = normalize_model_id(value).split("/")[-1]
    return family == "muse" or family.startswith("muse-") or family.startswith("muse_")


@dataclass(frozen=True, slots=True)
class MuseCapabilityPolicy:
    """Owner-supplied posture, never caller-supplied. `allowAll` and
    `:unrestricted` are refused at wiring time, never silently selected."""
    approval_mode: str = "denyUnmatched"
    permission_profile: str = ":read-only"

    def validate(self) -> None:
        if self.approval_mode not in APPROVAL_MODES:
            raise BadRequestError(f"unknown approval mode {self.approval_mode!r}")
        if self.approval_mode == "allowAll":
            raise BadRequestError("allowAll disables approval gating")
        if not self.permission_profile.strip() or self.permission_profile.strip() == ":unrestricted":
            raise BadRequestError("unrestricted permission profile refused")


@dataclass(frozen=True, slots=True)
class TurnParams:
    model_id: str
    workspace: str
    prompt: str
    session_id: str
    mode: RecoveryMode
    approval_mode: str = "denyUnmatched"
    permission_profile: str = ":read-only"
    token_budget: int | None = None
    timeout_seconds: float = 300.0


@dataclass(frozen=True, slots=True)
class ToolFailure:
    item_id: str
    kind: str
    status: str


@dataclass(frozen=True, slots=True)
class TurnResult:
    session_id: str
    turn_id: str
    agent_text: str
    model_echo: str = ""                     # provider-echoed model; "" = absent, never a match
    files_changed: tuple[str, ...] = ()      # observed by snapshot diff, not reported
    evidence_refs: tuple[str, ...] = ()
    token_usage: dict | None = None
    tool_failures: tuple[ToolFailure, ...] = ()


class TurnHandle:
    """Handle for one running turn. `cancel` owns the worker (N-05/N-15):
    shielded await, stop asserted, process group + readers waited out
    bounded before cancellation propagates; unproven shutdown refuses."""

    def done(self) -> bool: ...
    async def cancel(self) -> None: ...
    def result(self) -> TurnResult: ...      # raises TransportError/ScopeError (N-07)


class MuseLauncher:
    def __init__(self, *, capability_policy: MuseCapabilityPolicy | None = None,
                 default_timeout_s: float = 300.0) -> None:
        self._policy = capability_policy or MuseCapabilityPolicy()
        self._policy.validate()
        self._default_timeout_s = min(max(default_timeout_s, 1.0), 1800.0)

    async def start_turn(self, params: TurnParams) -> TurnHandle:
        if params.approval_mode != self._policy.approval_mode or \
           params.permission_profile != self._policy.permission_profile:
            raise BadRequestError("turn posture differs from wired capability policy")
        return await self._spawn(params)

    async def _spawn(self, params: TurnParams) -> TurnHandle: ...


async def run_bounded_turn(params: TurnParams) -> TurnResult:
    """One fresh process per turn: spawn → `initialize` → `permissionProfile/list`
    → `session/start` → `turn/start` → view events → terminal `turn/completed`
    → terminate + verify process-group death → residue validation → after-exit
    snapshot → atomic transcript publish. Wire: UUIDv7 command ids, strict JSON
    (duplicate keys rejected), cursor-anchored evidence, gap refusal, joined
    agent messages, input-echo verification, approval observation without client
    decisions. Only a clean shutdown (exit 0 after stdin close, no signal, no
    surviving group) yields a result; anything else raises MuseSessionError (N-07)."""
    before = snapshot_workspace(params.workspace)
    proc = spawn_muse_process(params)        # resume flag added when mode == RESUME
    try:
        terminal = await drive_turn_to_terminal(proc, params)   # bounded by timeout_seconds
    finally:
        await terminate_and_verify_group_death(proc)            # raises on unclean shutdown
    validate_no_postmortem_residue(proc)
    after = snapshot_workspace(params.workspace)
    return publish_turn_evidence(params, terminal, diff_snapshots(before, after))


def enforce_post_turn(*, params: TurnParams, turn: TurnResult,
                      scope_files: tuple[str, ...]) -> TurnResult:
    """Post-turn enforcement (N-07). Failures keep full evidence; success is
    never inferred from agent text alone."""
    if turn.tool_failures:
        first = turn.tool_failures[0]
        raise TransportError(f"MUSE_TOOL_FAILED:{first.item_id}:{first.status}")
    if turn.token_usage is not None and params.token_budget is not None \
            and int(turn.token_usage.get("total", 0)) > params.token_budget:
        raise ScopeError("MUSE_BUDGET_EXCEEDED")
    if turn.model_echo and normalize_model_id(turn.model_echo) != normalize_model_id(params.model_id):
        raise ScopeError("EXECUTOR_MODEL_MISMATCH")
    for path in turn.files_changed:
        if escapes_workspace(params.workspace, path):
            raise ScopeError(f"MUSE_PATH_ESCAPES_WORKSPACE:{path}")
        if scope_files and not covered_by_scope(scope_files, path):
            raise ScopeError(f"MUSE_PATH_OUT_OF_SCOPE:{path}")
    return turn
```
- Evidence is observed, not reported: before/after snapshots diffed after process + readers exit; transcripts published atomically into a supervisor-owned dir outside the workspace (held fd, ownership/mode policy, unique no-follow temp, hard-link no-replace).
- Bounds: turn timeout ≤ 1800s (under the `assign_task` start-to-close); per-step token budget may narrow it; materialization guards (≤10k files, ≤256MB) refuse explicitly.

### M-07 Recovery (resume or respawn, same attempt)
Trigger: heartbeat timeout on `assign_task` (90s) → Temporal retries the activity with the SAME `attempt_id`. Recovery reads `muse_session_id`, `pr_url`, `muse_progress` from WorkHub, runs preflight (both gates), re-claims if the lease moved (N-05), then resumes (`muse exec --resume S`, session per attempt, same worktree) or respawns (new session `S'` on the same attempt/task/branch with progress prompt). Decision input is the task-1 spike's written result. Invariants: no duplicate attempt, no duplicate PR (N-11 checks re-run), `recovery_count` incremented via `signal_recovered`, superseded session retained for audit.
```python
def build_task_prompt(inp: TaskWorkflowInput, attempt: Attempt,
                      spec: ExecSpec, pr) -> str:
    """Single prompt builder for first run, rework (H-02) and respawn (N-04):
    prior progress + verdict reasons + existing-PR guard + accepted scope.
    Secrets never enter the prompt."""
    wh = workhub_client_for(inp.tenant_id)
    progress = wh.read_progress(attempt)          # muse_progress rows; "" on first run
    verdict = wh.read_latest_verdict(attempt)     # None on first run
    parts = [spec.instructions, f"Scope:\n" + "\n".join(spec.scope_files)]
    if progress:
        parts.append("Prior progress on this attempt (resume from here):\n" + progress)
    if verdict is not None and not verdict.approved:
        parts.append("Verifier rework reasons (must fix):\n" + verdict.reasons)
    if pr is not None:
        parts.append(f"PR {pr.url} already exists for branch {attempt.branch}; "
                     f"continue on it, do not open a second PR.")
    return "\n\n".join(parts)
```
Acceptance: assign → build → kill → resume/respawn → verify → close with no human input, recorded (task 8).

### M-08 Test runner + verifier
```python
@dataclass(frozen=True, slots=True)
class TestEvidence:
    run_id: str
    passed: bool
    summary: str
    evidence_id: str


@activity.defn(name="run_tests")
async def run_tests(inp: TaskWorkflowInput, attempt: Attempt) -> TestEvidence:
    """Failures are evidence (N-09). A missing runner is an explicit failure,
    never a silent pass."""
    runner = test_runner()
    if runner is None:
        raise DownstreamError("TEST_RUNNER_MISSING")
    report = await runner.run(task_id=inp.task_id, attempt_id=attempt.attempt_id,
                              repo=attempt.repo, branch=attempt.branch)
    wh = workhub_client_for(inp.tenant_id)
    row = await attach_evidence_exact(
        wh, task_id=inp.task_id, evidence_type="test_evidence",
        evidence_ref=f"testrun:{report.run_id}", evidence_json=report.as_dict(),
        idempotency_key=activity_id_for(task_id=inp.task_id, activity_name="run_tests",
                                        step_id=None, attempt_id=attempt.attempt_id))
    return TestEvidence(run_id=report.run_id, passed=report.passed,
                        summary=report.summary, evidence_id=row["id"])


@dataclass(frozen=True, slots=True)
class Verdict:
    approved: bool
    reasons: str
    evidence_id: str


@activity.defn(name="verify")
async def verify(inp: TaskWorkflowInput, attempt: Attempt,
                 test_evidence: TestEvidence) -> Verdict:
    """Separate principal (N-08); input is acceptance criteria + diff + test
    evidence only. Output is `approve` or `rework + reasons`."""
    registry = persona_registry()
    verifier = registry.verifier_for(tenant_id=inp.tenant_id,
                                     builder_principal=attempt.builder_principal)  # N-08
    if not can_act(role="verifier", action="accept"):
        raise SelfReviewError("verifier_role_cannot_accept")
    wh = workhub_client_for(inp.tenant_id)
    diff = await wh.read_diff(attempt)
    checks = build_verification_checks(inp, attempt, diff, test_evidence)
    for check in checks:                                                     # N-09
        if not await wh.evidence_exists(inp.task_id, check.artifact_ref):
            raise IneligibleError(f"artifact_ref_not_on_task:{check.artifact_ref}")
    decision = await run_verifier_turn(verifier, inp, attempt, diff, test_evidence, checks)
    row = await attach_evidence_exact(
        wh, task_id=inp.task_id, evidence_type="review_verdict",
        evidence_ref=f"verdict:{attempt.attempt_id}:{decision.seq}",
        evidence_json={"decision": decision.value, "reasons": decision.reasons,
                       "checks": [c.as_dict() for c in checks]},
        idempotency_key=f"verdict-{attempt.attempt_id}-{decision.seq}")
    return Verdict(approved=(decision.value == "approve"),
                   reasons=decision.reasons, evidence_id=row["id"])
```
Honest gap terminals (`GAPS_FOUND`) are successful executions carrying a domain outcome, not activity failures. Acceptance: different principal reviews; rework loops at least once in a test; approval writes the result and closes.

### M-09 Result + close (formal, exactly-once)
All evidence writes in every module (claim receipt, session, PR, progress, test, verdict, recovery) go through `attach_evidence_exact`. The `WorkHubClient` helpers used above (`file_handoff_receipt`, `record_pr`, `save_session`) are thin wrappers over it with fixed `evidence_type` values.
```python
import uuid


def new_idempotency_key() -> str:
    return f"rouvr-{uuid.uuid4().hex}"


def json_contains(stored, submitted) -> bool:
    """True when stored JSON preserves every submitted value."""
    if isinstance(submitted, dict):
        return isinstance(stored, dict) and all(
            k in stored and json_contains(stored[k], v) for k, v in submitted.items())
    if isinstance(submitted, list):
        return (isinstance(stored, list) and len(stored) == len(submitted)
                and all(json_contains(s, v) for s, v in zip(stored, submitted)))
    return stored == submitted


async def attach_evidence_exact(wh, *, task_id: str, evidence_type: str,
                                evidence_ref: str, evidence_json: dict,
                                idempotency_key: str) -> dict:
    """R5 + exact readback (N-13). No verdict, result or close may cite
    unproven evidence. Retried once with the same key (byte-identical replay)."""
    try:
        row = await wh.attach_evidence(task_id, {"evidence_type": evidence_type,
                                                 "evidence_ref": evidence_ref,
                                                 "evidence_json": evidence_json},
                                       idempotency_key=idempotency_key)
    except WorkHubHttpError as exc:
        raise classify_http_error(exc) from exc
    if not row.get("id"):
        raise EvidenceError("evidence_id_missing")
    rows = await wh.list_task_evidence(task_id)
    matches = [r for r in rows if r.get("id") == row["id"]]
    if len(matches) != 1:
        raise EvidenceError("evidence_exact_readback_missing")
    back = matches[0]
    if back.get("evidence_type") != evidence_type \
            or back.get("evidence_ref") != evidence_ref \
            or not json_contains(back.get("evidence_json", {}), evidence_json):
        raise EvidenceError("evidence_exact_readback_mismatch")
    return back


@activity.defn(name="record_result")
async def record_result(inp: TaskWorkflowInput, attempt: Attempt, verdict: Verdict) -> dict:
    """Plain WorkHub result linking task, attempt, run id, session, PR and test
    run (phase 2 signs a hash of the same fields). The result claim is atomic
    with the terminal mark — no crash window between recorded result and
    terminal transition. The same `result_id` with a different core/round
    refuses `REPLAY_CONFLICT`, never deduplicates."""
    wh = workhub_client_for(inp.tenant_id)
    result_id = f"result-{attempt.attempt_id}"
    try:
        return await wh.claim_result_with_mark(
            result_id,
            {"task_id": inp.task_id, "attempt_id": attempt.attempt_id,
             "workflow_run_id": attempt.workflow_run_id,
             "muse_session_id": attempt.muse_session_id, "pr_url": attempt.pr_url,
             "verdict": "approved" if verdict.approved else "rework",
             "verdict_evidence_id": verdict.evidence_id})
    except WorkHubHttpError as exc:
        raise classify_http_error(exc) from exc


@activity.defn(name="close_task")
async def close_task(inp: TaskWorkflowInput) -> dict:
    """evaluate → batch-transition → terminal readback (N-09, N-14).
    Distinct fresh keys per close; a replay flagged `idempotent_replay` is
    refused as a fresh close — a repeated close with the same evaluation is
    a readback, not a second transition."""
    wh = workhub_client_for(inp.tenant_id)
    try:
        task = await wh.get_task_by_number(inp.task_id)              # R1
    except WorkHubHttpError as exc:
        raise classify_http_error(exc) from exc
    if task.get("status") != "needs_review":
        raise IneligibleError("task_not_in_needs_review")
    eval_key, walk_key = new_idempotency_key(), new_idempotency_key()
    try:
        evaluation = await wh.evaluate(                              # R7
            inp.task_id, decision="pass", checks=await wh.read_checks(inp.task_id),
            idempotency_key=eval_key)
    except WorkHubHttpError as exc:
        raise classify_http_error(exc) from exc
    if evaluation.get("idempotent_replay") is True:
        raise EvidenceError("close_eval_replay_refused")
    if evaluation.get("eligible_for_done") is not True:
        raise IneligibleError("evaluation_not_eligible_for_done")
    try:
        walk = await wh.transition_batch(                            # R8
            inp.task_id, status="done", reason="evaluation eligible_for_done=true",
            expected_record_version=task.get("record_version"),
            idempotency_key=walk_key, operation_id=f"{walk_key}:batch")
    except WorkHubHttpError as exc:
        raise classify_http_error(exc) from exc
    if walk.get("updated") != 1:                                     # N-14
        final = await wh.get_task_by_number(inp.task_id)
        if final.get("status") == "done" and final.get("evaluation_id") == evaluation.get("id"):
            return {"task_id": inp.task_id, "status": "done",        # idempotent close
                    "evaluation_id": evaluation.get("id")}
        raise MovedError("transition_not_confirmed")
    final = await wh.get_task_by_number(inp.task_id)
    if final.get("status") != "done":
        raise MovedError("terminal_readback_failed")
    return {"task_id": inp.task_id, "status": "done",
            "evaluation_id": evaluation.get("id"),
            "record_version": final.get("record_version")}


@activity.defn(name="mark_blocked")
async def mark_blocked(inp: TaskWorkflowInput, reason: str) -> dict:
    """Phase-1 terminal for blocked tasks; phase 2 adds `escalate_once` +
    owner signal. Must land; failure pages the operator."""
    wh = workhub_client_for(inp.tenant_id)
    try:
        return await wh.mark_blocked(inp.task_id, reason=reason,
                                     idempotency_key=f"blocked-{inp.task_id}-{reason}")
    except WorkHubHttpError as exc:
        raise classify_http_error(exc) from exc


# --- host interfaces: declared here, implemented by the runtime ---
def workhub_client_for(tenant_id: str) -> WorkHubClient: ...
def persona_registry() -> PersonaRegistry: ...
def exec_spec_provider() -> ExecSpecProvider: ...
def muse_launcher() -> MuseLauncher: ...
def github_client_for(persona: Persona) -> GitHubClient: ...
def test_runner() -> TestRunner | None: ...
def attempt_store() -> AttemptStore: ...
```
Acceptance: result row + `done` + terminal readback; a repeated close with the same evaluation is a readback, not a second transition.

## LLD: caching, queues, errors
### Cache
- Persona registry + exec specs: in-process, 5-min TTL, invalidated on config change. Secrets never cached beyond launch (resolved per effect on the host, held only for the spawn).
- Activity idempotency: in-worker idempotency store keyed by `activity_id` (see `IdempotencyStore`); replay returns the stored envelope with `idempotent_replay=true`. Backend dedupes on the same key. Preflight performs live owner reads per effect — never cached.
- Forbidden caches: lease/assignment state, binding verification results, PR existence. Every effect re-reads.
```python
import random


class IdempotencyStore:
    """In-worker replay store keyed by activity_id. Replays return the
    stored envelope with `idempotent_replay=True`."""

    def __init__(self) -> None:
        self._envelopes: dict[str, dict] = {}

    def cached(self, activity_id: str) -> dict | None:
        envelope = self._envelopes.get(activity_id)
        if envelope is None:
            return None
        return {**envelope, "idempotent_replay": True}

    def remember(self, activity_id: str, envelope: dict) -> None:
        self._envelopes[activity_id] = dict(envelope)


def retry_policy_for(activity_name: str) -> RetryPolicy:
    """N-12: per-activity policy from `ACTIVITY_CONTRACTS`. Only `retryable`
    RouvrErrors retry — enforced by `to_application_error`."""
    contract = ACTIVITY_CONTRACTS[activity_name]
    return RetryPolicy(maximum_attempts=contract.maximum_attempts,
                       backoff_coefficient=2.0)


def to_application_error(err: RouvrError) -> ApplicationError:
    """Non-retryable failures fail fast; retryable ones back off + jitter."""
    return ApplicationError(err.code, str(err), non_retryable=not err.retryable)


def backoff_with_jitter(*, attempt: int, base_s: float = 1.0, cap_s: float = 60.0) -> float:
    """Exponential backoff with jitter for manual sleeps (N-12)."""
    return min(cap_s, base_s * (2.0 ** attempt)) * random.uniform(0.5, 1.5)
```

### Queues
| Queue | Activities | Concurrency (phase 1) | Notes |
|---|---|---|---|
| `rouvr-orchestrator` | claim, open_attempt, record_result, close, mark_blocked | 10 | Short, idempotent WorkHub writes |
| `rouvr-builder` | `assign_task` (Muse turns) | 2–4 (model-bound) | One bounded turn per slot; heartbeat 90s |
| `rouvr-test` | `run_tests` (testrun-runner) | 4 | Evidence writes |
| `rouvr-verifier` | `verify` | 4 | Separate principal from builder |
Phase 1 runs one host at low concurrency; per-tenant and per-model rate limiters cap fan-out. Phase 2: outbox relay publishes to the starter and F1 messaging; consumers dedupe by event id.

### Activity contracts (timeouts × retries)
| Activity | start_to_close | heartbeat timeout | max attempts | On expiry |
|---|---|---|---|---|
| `claim_assignment_and_lease` | 30s | — | 3 | Fail → blocked (`E_AUTHZ`/`E_CONTRACT` non-retryable, no spawn) |
| `open_or_reuse_attempt` | 30s | — | 3 | Fail → blocked; never a duplicate attempt |
| `assign_task` | 1800s (= schedule_to_close) | 90s, renew every ~30s | 3 (backoff ×2 + jitter) | Heartbeat timeout → same-attempt recovery (N-03/N-04); start_to_close expiry → `ActivityError` → FAILED with correlation id, never silent `RUNNING_STEP` |
| `run_tests` | 900s | — | 2 | Failures are evidence (N-09); infra failure retries once |
| `verify` | 900s | — | 1 | Single authoritative verdict per attempt; rework opens a new attempt |
| `record_result` / `close_task` | 120s / 120s | — | 2 / 2 | CAS + readback make retries safe (N-13/N-14) |
| `mark_blocked` | 30s | — | 3 | Must land; failure pages the operator |
Design rule: long model-bearing calls carry NO heartbeat timeout that turns slow models into false timeouts — except `assign_task`, whose heartbeat is the lease-renewal liveness signal by design.

### Retryable vs non-retryable (full table)
| Code | Signals | Retry? | Handling | Paths |
|---|---|---|---|---|
| `E_THROTTLE` | Model 429/5xx, WorkHub 429 | Yes (backoff+jitter) | Inside activity policy | N-12 |
| `E_DOWNSTREAM` | WorkHub 5xx, network errors, worker death | Yes | Heartbeat-timeout recovery on same attempt | N-03/N-04/N-12 |
| `E_TRANSPORT` | `MuseSessionError`, `MUSE_TOOL_FAILED`, `MUSE_WORKSPACE_TAINTED` | Yes, within policy | Retry turn; evidence preserved | N-07 |
| `E_EVIDENCE` | `evidence_exact_readback_*`, `evidence_id_missing` | Once, byte-identical key | Then block `evidence_unproven` | N-13 |
| `E_MOVED` | 409 lease/assignment moved, CAS conflict | Re-read once, not blind retry | Re-claim or re-evaluate once, then block | N-05/N-14 |
| `E_CONTRACT` | 400 `invalid_contract`, `BINDING_*`, `SELECTION_BINDING_MISMATCH` | No | Fail → `mark_blocked("contract")` | H-01 gate, N-06 |
| `E_CONTRACT_MOVED` | 422, `BINDING_CONTRACT_MOVED` | No | Block; phase 2 re-approves | N-06 |
| `E_AUTHZ` | 403, `repo_not_allowed`, revoked key, `preclaim-task-read-refused` | No | Fail closed, zero turns → blocked | N-02 |
| `E_SELF_REVIEW` | builder == verifier | No (re-claim once with a different principal) | Then block `no_independent_verifier` | N-08 |
| `E_SCOPE` | `MUSE_PATH_*`, `MUSE_BUDGET_EXCEEDED`, `EXECUTOR_MODEL_MISMATCH` | No | Rework with tightened scope or blocked | N-07 |
| `E_INELIGIBLE` | test failures, `eligible_for_done=false` | No (rework loop, bounded) | Rework ≤ MAX, then `rework_limit` | N-09/N-10 |
| `E_BAD_REQUEST` | `KEY.REQUIRED`, `PAYLOAD.*`, malformed | No (caller bug) | Fix caller | — |
| `E_CANCELLED` | `signal_cancel`, `CANCELLED` | No | `CANCELLED`, release lease, own the worker | N-15 |

### Recovery fallback
If the spike shows resume isn't solid, respawn the coder on the same task, branch and attempt with prior progress from WorkHub (N-04), and say so honestly in the demo.

### Phase 2 reference pseudocode  *(ADDED — proposal; same style as §LLD core modules, not normative for phase 1)*
```python
# --- outbox relay (N-17) ---
async def relay_outbox_once(db, publishers, *, batch: int = 100) -> int:
    rows = await db.fetch(
        "SELECT * FROM outbox WHERE published_at IS NULL "
        "ORDER BY created_at LIMIT $1 FOR UPDATE SKIP LOCKED", batch)
    for row in rows:
        try:
            await publishers[row["event_type"]].publish(event_id=row["event_id"], payload=row["payload"])
            await db.execute("UPDATE outbox SET published_at=now() WHERE id=$1", row["id"])
        except Exception:
            await db.execute("UPDATE outbox SET attempts=attempts+1 WHERE id=$1", row["id"])
    return len(rows)

# --- escalate once (H-04) ---
async def escalate_once(tx, *, tenant_id, task_id, reason, contract_rev, recipient) -> bool:
    key = sha256(f"{tenant_id}|{task_id}|{reason}|{contract_rev}".encode()).hexdigest()
    inserted = await tx.fetchval(
        "INSERT INTO escalations(tenant_id,task_id,dedupe_key,reason,recipient,status) "
        "VALUES($1,$2,$3,$4,$5,'pending') ON CONFLICT (tenant_id,dedupe_key) DO NOTHING RETURNING id",
        tenant_id, task_id, key, reason, recipient)
    if inserted:
        await tx.execute("INSERT INTO outbox(tenant_id,event_id,aggregate,aggregate_id,event_type,payload) "
                         "VALUES($1,gen_random_uuid(),'task',$2,'task.blocked',$3)",
                         tenant_id, task_id, {"reason": reason, "escalation_id": inserted})
    return bool(inserted)

# --- scheduled run (H-03) ---
@workflow.defn
class ScheduledRunWorkflow:
    @workflow.run
    async def run(self, sched: dict) -> dict:
        fire = workflow.info().start_time.isoformat()
        task = await workflow.execute_activity(
            create_task_from_template, {"schedule": sched, "idem": f"{sched['id']}:{fire}"},
            start_to_close_timeout=timedelta(seconds=30), retry_policy=retry_policy_for("claim_assignment_and_lease"))
        if task["active_execution_contract_ref"] != sched["approved_revision"]:
            await workflow.execute_activity(mark_blocked_and_escalate, {"task": task, "reason": "contract_moved"},
                                            start_to_close_timeout=timedelta(seconds=30))
            return {"status": "blocked"}
        return await workflow.execute_child_workflow(
            TaskWorkflow.run, build_input(task), id=workflow_id_for_task(tenant_id=sched["tenant_id"], task_id=task["id"]),
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)

# --- signed receipt (phase 2) ---
def build_receipt(*, task_id, contract_rev, attempt_id, run_id, session_id, pr_url, test_run_id, signer) -> dict:
    fields = [task_id, contract_rev, attempt_id, run_id, session_id, pr_url, test_run_id]
    digest = sha256("\n".join(fields).encode()).hexdigest()
    return {"digest_sha256": digest, "signature": signer.sign(digest), "key_id": signer.key_id}
```

## LLD: cross-cutting contracts
- **Trace:** trace id = task id, carried as `correlation_id` through workflow input → activity inputs (`causation_id` per hop) → backend command bodies → evidence rows. Search attributes: `TenantId`, `TaskId`, `CandidateSha`, `Phase`, `WorkflowVersion`.
- **Determinism:** workflow code uses only the injected clock (live: the Temporal workflow clock); no wall-clock, no randomness, no I/O in workflow code. Replay and version migration follow the `workflow_version` gate in `TaskWorkflowInput.validate`.
- **Secret hygiene:** credentials/JWTs/raw owner responses never leave their owning module (preflight verifier, binding transport, registry resolver). Allowed logging: task/attempt/session/run ids, PR URLs, evidence ids, reason codes. Free text is redacted before entering evidence; response bodies are sanitized before logging (see `sanitize_for_log`).
- **Authority order (every effect):** shape validation → live owner re-read → exact compare → effect. Caller-supplied dictionaries are never authority; absence or substitution refuses before spawn. Two preflight gates per Muse effect (before staging, immediately before activation) close the stage-then-move window.
- **Exactly-once effects:** one workflow per task (`REJECT_DUPLICATE`); one attempt per failure (resume/respawn reuse); one PR per attempt chain (N-11); one result per round (atomic claim+mark); one close per evaluation (CAS + readback). No silent fallbacks anywhere: every refusal names its code.
```python
SENSITIVE_KEYS = frozenset({
    "access_token", "authorization", "client_secret", "credential",
    "credentials", "jwt", "password", "private_key", "refresh_token",
    "secret", "token",
})


def sanitize_for_log(value):
    """Recursively redact fields that could expose credentials or tokens."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k.lower() in SENSITIVE_KEYS else sanitize_for_log(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_for_log(v) for v in value]
    return value
```

## LLD: deployment
### Topology (phase 1)
- The existing lane host runs the Temporal server + lane worker; the new workflow and activities deploy into it. WorkHub stays as deployed. Services run under systemd (Docker Compose where already used).
- Worker processes per role queue: `rouvr-orchestrator`, `rouvr-builder` (assignTask + Muse, 2–4 slots), `rouvr-test` (testrun-runner), `rouvr-verifier`. Each worker binds its backend factory at bootstrap; unbound dispatch/verify fails closed (`BACKEND_NOT_BOUND`), never executes elsewhere.
- Health: `GET /api/v1/capabilities` (R9) + Temporal worker poller proof + `mark_blocked` landing. Single region, single Temporal server in phase 1; WorkHub replica + 99.5% target in phase 2.

### On-host layout
```
/srv/rouvr/
  personas/tenants.yaml          # 0640, refs only (M-04); 0600 per-persona env files beside it
  workspaces/<task>/<attempt>/   # persistent per-attempt dir: git worktree on branch B,
                                 # AGENTS.md/MUSE.md, materialized scope (≤10k files/256MB)
  evidence/<task>/<attempt>/     # supervisor-owned, NOT under the workspace: transcripts,
                                 # snapshots, turn records (0600, held-fd atomic publish)
  state/recovery/                # intent.json + lease/slot archives for operator recovery (N-16)
```
- Workspaces persist until 7 days after close, then GC. Evidence is immutable once published (hard-link no-replace; same-name republish refuses `MUSE_EVIDENCE_REDIRECTED`).
- Creds files are 0600; the credential loader refuses anything else. JWTs in memory only, never argv/logs.

### Config (all config-driven, nothing hardcoded per tenant)
| Key | Default | Notes |
|---|---|---|
| `MAX_REWORK` | 2 | Per-task overridable (N-10) |
| `LEASE_TTL_S` / `HEARTBEAT_S` | 120 / 30 | Heartbeat timeout 90s |
| `ASSIGN_START_TO_CLOSE_S` | 1800 | Turn timeout ≤ this |
| `VERIFY_MAX_ATTEMPTS` | 1 | One verdict per attempt |
| `BUILDER_CONCURRENCY` | 2 | Model-bound |
| `RESUME_SUPPORTED` | spike result | N-03 vs N-04 |
| `SPEC_PROVIDER` | `local` | `sdk` slot when proven (M-05) |

### Observability (phase 1: no new stack)
- Temporal UI (one run per task, history shows renewals, `recovery_count`, rework count) + WorkHub evidence (result links task/attempt/run/session/PR/test run). `query_status` exposes phase + search attributes per workflow.
- Log line per effect: `{task, attempt, activity_id, correlation_id, outcome, reason_code, evidence_refs}`. Reason codes are the stable `E_*` / `BINDING_*` / `MUSE_*` vocabulary — grep-able, no free text.
- Phase 2 adds OpenTelemetry spans (Rouvr → WorkHub → workflow → activity → Muse session), Prometheus/Grafana (escalations, token spend, lease renewals, recovery rate, rework rate).

### Cutover
1. Land tasks 1–7 behind the new queues; legacy (AIX canary, VPS2 watcher, host loop driver, direct TaskLoop launcher) untouched and still serving.
2. Task 8 rehearsal on a scratch task: assign → build → kill → resume/respawn → verify → close, recorded. Gate: one PR, one result, `done` + terminal readback.
3. Flip intake to `rouvr start` for new tasks; legacy drains.
4. Phase 2 retires legacy after the acceptance window, then adds unattended wake, escalation, receipts, outbox, OTel, contract-revision approval and the multi-tenant SDK spec.
- CI on `github.com/Anxy/rouvr`. Rollback: stop new starters; in-flight workflows finish or cancel (N-15); legacy paths never deleted until step 4.

## Testing strategy  *(ADDED — proposal; acceptance checks themselves are in §Build plan)*
| Layer | What | How | Gate |
|---|---|---|---|
| Unit | `verify_binding`, `classify_http_error`, `json_contains`, key builders, `sanitize_for_log`, transition table | pytest, table-driven per `E_*` code | Every PR |
| Workflow | TaskWorkflow phases, rework bound, cancel during activity | Temporal `WorkflowEnvironment` time-skipping + mocked activities | Every PR |
| Replay / determinism | Recorded histories of H-01, H-02, N-03/N-04 | `Replayer` against new workflow code | Before deploy |
| Contract | WorkHub routes R1–R10 shapes and error strings | Recorded fixtures checked against the live OpenAPI (task 2) | Nightly |
| Integration | assignTask against a scratch repo + sandbox WorkHub project | Real Muse CLI, `muse-spark`, one-file change | Pre-merge on `main` |
| Fault injection | Kill worker (SIGKILL), kill host, WorkHub 503, revoke cred, PR created mid-turn | Scripted chaos on scratch task; assert one PR, one result, `done` | Task 8 + weekly |
| Security | Secret never in prompt/log/evidence | grep evidence + logs for SENSITIVE_KEYS values after each e2e | Every e2e |

## CI/CD  *(ADDED — proposal; doc states only "CI on github.com/Anxy/rouvr")*
1. PR: lint (ruff), type check (mypy/pyright), unit + workflow tests, replay tests on committed histories.
2. Merge to `main`: build a versioned worker artifact (wheel or container), integration test on scratch WorkHub project.
3. Deploy: systemd unit per queue on the lane host; roll builder workers last; workers drain (`graceful_shutdown_timeout` ≥ heartbeat timeout) so in-flight turns either finish or hand off via heartbeat timeout.
4. Post-deploy smoke: `GET /healthz`, one `rouvr start` on a scratch task to `done`.
5. Rollback: redeploy previous artifact; workflow changes are versioned so old histories still replay.

## Runbooks and alerts  *(ADDED — proposal)*
| Alert | Condition | First action |
|---|---|---|
| Task stuck | phase `RUNNING_STEP` > `ASSIGN_START_TO_CLOSE_S` + 5 min | `rouvr status`; check lease in WorkHub; N-16 reclaim |
| `mark_blocked` failed | activity exhausted 3 attempts (stated: pages operator) | Manually block in WorkHub; file `controller_terminal_recovery_v1` |
| No pollers | queue poller count 0 for 2 min | Restart worker unit |
| Recovery rate high | `recovery_count` > 1 on >20% of tasks/day | Check host stability, resume spike result |
| Duplicate PR | >1 open PR on a task branch | Close newer PR, inspect N-11 defences |
| Token spend | > budget/day per tenant | Lower `BUILDER_CONCURRENCY`, inspect rework rate |

## Rollout and migration  *(ADDED — complements §Cutover)*
- Feature flag `ROUVR_INTAKE_ENABLED` per tenant (proposal) gates step 3 of Cutover.
- Legacy `lane-task-<n>` workflows are not migrated; they finish on the legacy path. New tasks only use `rouvr-task-…` IDs.
- Phase 2 tables are additive; outbox is introduced with the relay in shadow mode (publish to log only) before the starter consumes it.

## Capacity (estimates)
| Quantity | Estimate | Implication |
|---|---|---|
| Tasks/day early | 50 | Trivial for WorkHub and Temporal |
| Concurrent workers | 5–10 | Limited by model rate limits and CPU |
| Avg task duration | 20 min | Lease ~2 min, renewed every 30 s heartbeat |
| Tokens/task | 200k | ~10M tokens/day at 50 tasks |
| Step/evidence records | ~200/task | ~3.6M/year. Partition later (phase 2). |
| Workspace disk | 500 MB/attempt | Clean up 7 days after close |

### Capacity: derived math  *(ADDED — arithmetic on the stated estimates; assumptions marked)*
| Quantity | Derivation | Result |
|---|---|---|
| Peak concurrent tasks | 50 tasks/day × 20 min ÷ (8 working h assumed) | ≈ 2 avg; 5–10 burst (matches stated workers) |
| Lease renewals | 1 per 30 s per running task × 10 | 0.33 writes/s peak |
| WorkHub writes per task | claim, attempt, handoff, session, ~10 progress, PR, test, verdict, result, evaluate, transition (assumed ~20 + reads) | ~1,000 writes/day |
| Temporal actions per task | ~10 activities × (schedule+start+complete) + heartbeats 40 | ~70; ~3.5k/day |
| Tokens | 200k × 50 | 10M/day (stated); 20 min task ⇒ ~170 tokens/s per active task |
| Model throughput needed | 10 concurrent × 170 tok/s | ~1.7k tok/s ≈ 100k TPM; check against Meta Model API limits (UNVERIFIED) |
| Disk | 500 MB × attempts alive 7 days (50/day × 1.3 attempts assumed × 7) | ≈ 230 GB worst case on the builder host |
| Evidence rows | ~200/task × 50 × 365 | 3.65M/year (stated) |

## Build plan: task order (superseded by Part I roadmap R1–R10; kept for traceability)
No dates or day estimates. Each step starts only once the previous one is proven live.

| # | Task | Acceptance check | Paths proven |
|---|---|---|---|
| 1 | **Muse resume spike:** start a headless `muse exec`, kill it, resume it by session ID | A written result. Either the resumed session continues with prior context (the resume path), or it doesn't (the respawn fallback becomes the design). | Selects N-03 vs N-04 |
| 2 | **List the small WorkHub fixes** in the current service: atomic create with persona, repo and branch; session, run and PR fields; builder ≠ verifier check; state and error mapping; `lease_expires_at` on the assignment read | Each item confirmed against the live OpenAPI and code. Fixes merged in the current service. No new WorkHub. | Unblocks H-01, N-02, N-08 |
| 3 | **Admission:** Rouvr starts `TaskWorkflow` with ID = task ID, and duplicates are rejected | A second start returns the existing run, and the Temporal UI shows one run per task | N-01 |
| 4 | **Ownership** through the existing endpoints, with renewal as the heartbeat | Assignment, lease and attempt are visible in WorkHub. Killing the worker stops renewal and the lease expires or is reclaimed. | N-05, N-16 |
| 5 | **assignTask:** persona config, single Muse launcher, session per attempt, existing-PR check, both preflight gates | The coder persona edits the repo and opens one PR. The PR URL and session ID are in WorkHub. A rerun doesn't open a second PR. Refusals precede spawn. | H-01 build, N-02, N-06, N-07, N-11 |
| 6 | **Test runner as evidence** (existing testrun-runner) | Test evidence is attached to the attempt in WorkHub, pass or fail | N-09 (evidence half) |
| 7 | **Verifier persona** approves or sends work back, then formal result submit and close | A different principal reviews. Rework loops at least once in a test. Approval writes the result and closes the task. CAS + readback hold. | H-02, N-08, N-09, N-10, N-13, N-14 |
| 8 | **Full kill and recover rehearsal,** then recording | Assign → build → kill → resume/respawn → verify → close with no human input, recorded; still one PR and one result | N-03/N-04 end to end |
| 9 | **Phase 2:** unattended wake (schedule or WorkHub event), one-shot escalation, signed receipts, outbox relay, OpenTelemetry, contract-revision approval, legacy retirement, multi-tenant SDK exec spec | Each item has its own acceptance check when scheduled | N-10/N-15 escalations, phase-2 receipts |

The SDK can slot into step 5 if the teammate's branch is proven. Otherwise the local default is used.

## Alignment with the original AIX plan
**Unchanged:** WorkHub as the record; headless runs; no self-review; leases and stall recovery; independent verification; stateless runs; the F4 Execution, F5 Verification and F6 Recovery phases.

**Changed:**
1. Temporal replaces the canary controller and the planned NATS JetStream bus.
2. Named personas are reconciled as claim-time role bindings, so bots stay role-agnostic.
3. Runner-issued credentials become the host persona registry.
4. The context bundle is simplified to the task plus `AGENTS.md`/`MUSE.md`.
5. "Verdict creates the next job" becomes the workflow's rework loop.

Roughly 70% of the intent is the same. The changes are plumbing, not direction.

## Risks
1. Muse resume doesn't work headless. Mitigation: the respawn fallback, decided by task 1.
2. Duplicate PRs or pushes from inside Muse. Mitigation: the existing-PR check and immediate PR recording.
3. WorkHub grant or claim refusals (like the canary's preclaim failure) hit Temporal too. Mitigation: task 2.
4. Scope creep into phase 2. Mitigation: explicit labels.
5. The demo story differs from the public README (morning report, Llama wording).

6. *(ADDED)* Model rate limits below ~100k TPM cap concurrency before CPU does. Mitigation: per-model limiter, `muse-spark` for checks.
7. *(ADDED)* Single Temporal server and lane host are a single point of failure in phase 1. Mitigation: documented restart runbook; phase 2 HA.
8. *(ADDED)* Prompt injection from repository content. Mitigation: read-only posture by default, egress allowlist, no extra secrets in env.
9. *(ADDED)* Workspace disk growth (≈230 GB worst case). Mitigation: 7-day GC plus a disk alert at 80%.

10. *(from 29 Oct port map)* Grant refusals: `preclaim-task-read-refused` is an Auth/WorkHub grant problem; if not fixed in A1, Temporal parks on the same refusal.
11. *(from 29 Oct port map)* Headless MCP in Muse is unverified; R7 depends on it; fallback is a small tool loop around Muse.
12. *(from 29 Oct port map)* Core carries 472 pre-existing failing tests; CI must scope to the demo path.
13. *(from 29 Oct port map)* Model-written evidence: today build evidence is self-reported and the reviewer reads the builder's report; R8/R8a replace it with system evidence.
14. *(from 29 Oct port map)* History: VMG Hawks line in `1d8e8e4` (removed in `1702c6a`); flagged only, no history rewrite unless the owner decides.

## Open decisions
1. ✅ **RESOLVED 29 Oct 2026 (owner decision): coding only.** Recurring/operational tasks out of scope for now. *(Was: Demo story: coding only, or coding plus the operational task. This decides whether any operational work is in phase 1.)*
2. ✅ **RESOLVED 29 Oct 2026 (owner decision): thin start endpoint.** Rouvr calls `POST /workflows/start`. The outbox starter is later/optional. *(Was: CLI or thin orchestrator endpoint.)*
3. ✅ **RESOLVED 29 Oct 2026 (owner decision): the AIX mapping.** The existing AIX role→principal mapping is the persona registry source. Its format is an assumption until R6. *(Was: tenant-keyed YAML (recommended) or the AIX mapping.)*
4. **SDK:** the teammate needs to show the branch, the commit and one live run (SDK in → Muse edits → result out). Otherwise the local default is used.
5. **An owner for each task** in the build plan.
6. *(ADDED)* Where Rouvr-owned attempt metadata lives: WorkHub task metadata vs evidence rows (task 2).
7. *(ADDED)* Phase 2 tables in WorkHub's Postgres (same-transaction outbox) or a separate Rouvr DB with CDC.
8. *(ADDED)* Temporal namespace per tenant or shared namespace with search attributes.
9. *(ADDED)* Signing key custody for receipts (host key vs KMS).
10. *(ADDED)* Real SLO targets once task 8 produces measured timings.



**Status after 29 Oct (consolidation):**
- OD1 — **resolved 29 Oct (owner): coding only.**
- OD2 — **resolved 29 Oct (owner): thin endpoint** (`rouvr start` kept as operator CLI only). Supersedes the earlier consolidation note that said "both".
- OD3 — **resolved 29 Oct (owner): AIX mapping.** Supersedes the earlier consolidation note that said "YAML". This goes against the port map's suggestion ("Rouvr gets a persona registry with `cred_ref`"), see C17.
- OD4 SDK — **resolved:** optional slot, not ported until it shows a model-backed run (README says it does not run Codex and doesn't mention Muse).
- OD5 owners — partly resolved: the hidden-test author is the `test_author` persona (R8a); human owners per R item are still open.
- New: history rewrite for `1d8e8e4` (force-push/squash/leave) — open, flagged only.
- New: do Muse headless MCP tools work (R2/R7)?


---

# Part III — LLD implementation guide (ADDED — proposal, v6)
> A proposal: a complete, typed reference implementation of Part II, in `rouvr-scaffold.zip`. `pytest -q` gives 19 passed, including a real Temporal time-skipping workflow test.
>
> **v6 (29 Oct owner decisions):** adds the thin start endpoint `rouvr/api.py` (OD2) and an AIX-mapping persona loader (OD3, format ASSUMED). Recurring schedules are marked out of scope (OD1).
>
> **v5:** the verified Muse argv, the WorkHub route names, `WorkHubPort`, reclaim-dead on retry and the verdict alias.
>
> **Not yet in the scaffold:** the R7 `submit_result` gate, the R8 cheat-scan and hidden-test fetch, the `ledger/` backend, the `test_author` role, and the session-binding row writer.
## Project layout
```
rouvr-scaffold/
.env.example
BUILD_ORDER.md
Dockerfile
README.md
docker-compose.yml
migrations/001_phase2.sql
pyproject.toml
rouvr/__init__.py
rouvr/activities.py
rouvr/api.py
rouvr/config.py
rouvr/contracts.py
rouvr/db.py
rouvr/errors.py
rouvr/escalation.py
rouvr/execspec.py
rouvr/gitops.py
rouvr/host.py
rouvr/ids.py
rouvr/lease.py
rouvr/muse.py
rouvr/outbox.py
rouvr/personas.py
rouvr/receipts.py
rouvr/starter.py
rouvr/testrunner.py
rouvr/workers.py
rouvr/workflows.py
rouvr/workhub/__init__.py
rouvr/workhub/client.py
rouvr/workhub/port.py
rouvr/workhub/routes.py
tests/test_api_personas.py
tests/test_unit.py
tests/test_workflow.py
```
## `pyproject.toml`
```toml
[project]
name = "rouvr"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["temporalio>=1.7", "httpx>=0.27", "asyncpg>=0.29", "pyyaml>=6", "cryptography>=42"]
[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]
[project.scripts]
rouvr = "rouvr.starter:main"
rouvr-worker = "rouvr.workers:main"
rouvr-relay = "rouvr.outbox:main"
[tool.pytest.ini_options]
asyncio_mode = "auto"
```
## `.env.example`
```bash
# PROPOSAL. Copy to .env. Never put secrets here except in host-local files referenced by personas.
ROUVR_TENANT_ID=default
ROUVR_PROJECT_ID=rouvr
TEMPORAL_ADDRESS=localhost:7233
TEMPORAL_NAMESPACE=default
WORKHUB_BASE_URL=http://localhost:8080
ROUVR_DATABASE_URL=postgresql://rouvr:rouvr@localhost:5432/rouvr
PERSONA_REGISTRY_PATH=/srv/rouvr/personas/tenants.yaml
WORKSPACE_ROOT=/srv/rouvr/workspaces
EVIDENCE_ROOT=/srv/rouvr/evidence
MUSE_BIN=muse
MAX_REWORK=2
LEASE_TTL_S=120
HEARTBEAT_S=30
ASSIGN_START_TO_CLOSE_S=1800
BUILDER_CONCURRENCY=2
RESUME_SUPPORTED=false
SPEC_PROVIDER=local
RECEIPT_SIGNING_KEY_PATH=/srv/rouvr/keys/receipt_ed25519.pem
RECEIPT_KEY_ID=rouvr-receipt-1
TESTRUN_RUNNER_URL=http://localhost:8090

# Owner decision 3: persona registry source = AIX mapping (format ASSUMED, see rouvr/personas.py)
PERSONA_SOURCE=aix
AIX_MAPPING_PATH=/srv/rouvr/personas/aix_mapping.json
# Owner decision 2: thin start endpoint
START_API_TOKEN_FILE=/srv/rouvr/start_api.token
```
## `docker-compose.yml`
```yaml
# PROPOSAL: local dev stack. Phase 1 production reuses the existing lane host (systemd).
services:
  postgres:
    image: postgres:16
    environment: {POSTGRES_USER: rouvr, POSTGRES_PASSWORD: rouvr, POSTGRES_DB: rouvr}
    ports: ["5432:5432"]
    volumes: ["./migrations:/docker-entrypoint-initdb.d:ro"]
  temporal:
    image: temporalio/auto-setup:1.24
    environment: {DB: postgres12, DB_PORT: 5432, POSTGRES_USER: rouvr, POSTGRES_PWD: rouvr, POSTGRES_SEEDS: postgres}
    ports: ["7233:7233"]
    depends_on: [postgres]
  temporal-ui:
    image: temporalio/ui:2.26.2
    environment: {TEMPORAL_ADDRESS: temporal:7233}
    ports: ["8233:8080"]
    depends_on: [temporal]
  worker-orchestrator: &worker
    build: .
    env_file: .env
    environment: {TEMPORAL_ADDRESS: "temporal:7233", ROUVR_DATABASE_URL: "postgresql://rouvr:rouvr@postgres:5432/rouvr"}
    command: ["rouvr-worker", "--queue", "rouvr-orchestrator"]
    depends_on: [temporal]
  worker-builder: {<<: *worker, command: ["rouvr-worker", "--queue", "rouvr-builder"], volumes: ["/srv/rouvr:/srv/rouvr"]}
  worker-test: {<<: *worker, command: ["rouvr-worker", "--queue", "rouvr-test"]}
  worker-verifier: {<<: *worker, command: ["rouvr-worker", "--queue", "rouvr-verifier"], volumes: ["/srv/rouvr:/srv/rouvr"]}
  outbox-relay: {<<: *worker, command: ["rouvr-relay"]}
```
## `Dockerfile`
```
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY rouvr ./rouvr
RUN pip install --no-cache-dir .
# The Muse CLI must be installed on the builder/verifier image or mounted from the host (UNVERIFIED packaging).
```
## `migrations/001_phase2.sql`
```sql
-- PROPOSAL (phase 2 tables; phase 1 uses WorkHub only). Idempotent.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE IF NOT EXISTS step_log (
  id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, task_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
  activity_id TEXT NOT NULL, activity_name TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('ok','failed','replayed','cancelled')),
  reason_code TEXT, evidence_refs TEXT[] NOT NULL DEFAULT '{}', correlation_id TEXT NOT NULL,
  envelope JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (tenant_id, activity_id));
CREATE INDEX IF NOT EXISTS step_log_task_idx ON step_log (tenant_id, task_id, created_at);
CREATE TABLE IF NOT EXISTS outbox (
  id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, event_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
  aggregate TEXT NOT NULL, aggregate_id TEXT NOT NULL, event_type TEXT NOT NULL, payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), published_at TIMESTAMPTZ, attempts INT NOT NULL DEFAULT 0,
  last_error TEXT);
CREATE INDEX IF NOT EXISTS outbox_unpublished_idx ON outbox (created_at) WHERE published_at IS NULL;
CREATE TABLE IF NOT EXISTS escalations (
  id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, task_id TEXT NOT NULL, dedupe_key TEXT NOT NULL,
  reason TEXT NOT NULL, recipient TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','sent','acknowledged','resolved')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), sent_at TIMESTAMPTZ, resolved_at TIMESTAMPTZ,
  UNIQUE (tenant_id, dedupe_key));
CREATE INDEX IF NOT EXISTS escalations_open_idx ON escalations (tenant_id, status) WHERE status <> 'resolved';
CREATE TABLE IF NOT EXISTS receipts (
  id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, task_id TEXT NOT NULL, contract_rev TEXT NOT NULL,
  attempt_id TEXT NOT NULL, workflow_run_id TEXT NOT NULL, muse_session_id TEXT NOT NULL, pr_url TEXT NOT NULL,
  test_run_id TEXT NOT NULL, digest_sha256 CHAR(64) NOT NULL, signature BYTEA NOT NULL, key_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (tenant_id, task_id, attempt_id));
CREATE TABLE IF NOT EXISTS recurring_schedules (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, template_task_id TEXT NOT NULL, approved_revision TEXT NOT NULL,
  cron TEXT NOT NULL, timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata', enabled BOOLEAN NOT NULL DEFAULT false,
  last_fired_at TIMESTAMPTZ, created_by TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS recurring_enabled_idx ON recurring_schedules (tenant_id) WHERE enabled;
-- Local lease table: used ONLY by the dev fake WorkHub / tests to exercise fencing. Real leases live in WorkHub.
CREATE TABLE IF NOT EXISTS dev_leases (
  tenant_id TEXT NOT NULL, task_id TEXT NOT NULL, holder TEXT NOT NULL, lease_epoch BIGINT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL, PRIMARY KEY (tenant_id, task_id));
```
## `rouvr/config.py`
```python
"""rouvr/config.py — typed settings loaded from env (PROPOSAL)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise RuntimeError(f"missing required env var {name}")
    return v


def _bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    project_id: str
    temporal_address: str
    temporal_namespace: str
    workhub_base_url: str
    database_url: str
    persona_registry_path: Path
    workspace_root: Path
    evidence_root: Path
    muse_bin: str
    max_rework: int = 2
    lease_ttl_s: int = 120
    heartbeat_s: int = 30
    assign_start_to_close_s: int = 1800
    builder_concurrency: int = 2
    resume_supported: bool = False
    spec_provider: str = "local"
    receipt_key_path: Path = field(default=Path("/srv/rouvr/keys/receipt_ed25519.pem"))
    receipt_key_id: str = "rouvr-receipt-1"
    testrun_runner_url: str = "http://localhost:8090"
    persona_source: str = "aix"          # "aix" (owner decision 3) | "yaml"
    aix_mapping_path: Path = Path("/srv/rouvr/personas/aix_mapping.json")  # ASSUMPTION
    start_api_token_file: Path = Path("/srv/rouvr/start_api.token")

    @staticmethod
    def from_env() -> "Settings":
        s = Settings(
            tenant_id=_env("ROUVR_TENANT_ID", "default"),
            project_id=_env("ROUVR_PROJECT_ID", "rouvr"),
            temporal_address=_env("TEMPORAL_ADDRESS", "localhost:7233"),
            temporal_namespace=_env("TEMPORAL_NAMESPACE", "default"),
            workhub_base_url=_env("WORKHUB_BASE_URL").rstrip("/"),
            database_url=_env("ROUVR_DATABASE_URL", "postgresql://rouvr:rouvr@localhost:5432/rouvr"),
            persona_registry_path=Path(_env("PERSONA_REGISTRY_PATH", "/srv/rouvr/personas/tenants.yaml")),
            persona_source=_env("PERSONA_SOURCE", "aix"),
            aix_mapping_path=Path(_env("AIX_MAPPING_PATH", "/srv/rouvr/personas/aix_mapping.json")),
            start_api_token_file=Path(_env("START_API_TOKEN_FILE", "/srv/rouvr/start_api.token")),
            workspace_root=Path(_env("WORKSPACE_ROOT", "/srv/rouvr/workspaces")),
            evidence_root=Path(_env("EVIDENCE_ROOT", "/srv/rouvr/evidence")),
            muse_bin=_env("MUSE_BIN", "muse"),
            max_rework=int(_env("MAX_REWORK", "2")),
            lease_ttl_s=int(_env("LEASE_TTL_S", "120")),
            heartbeat_s=int(_env("HEARTBEAT_S", "30")),
            assign_start_to_close_s=int(_env("ASSIGN_START_TO_CLOSE_S", "1800")),
            builder_concurrency=int(_env("BUILDER_CONCURRENCY", "2")),
            resume_supported=_bool("RESUME_SUPPORTED", False),
            spec_provider=_env("SPEC_PROVIDER", "local"),
            receipt_key_path=Path(_env("RECEIPT_SIGNING_KEY_PATH", "/srv/rouvr/keys/receipt_ed25519.pem")),
            receipt_key_id=_env("RECEIPT_KEY_ID", "rouvr-receipt-1"),
            testrun_runner_url=_env("TESTRUN_RUNNER_URL", "http://localhost:8090"),
        )
        if s.heartbeat_s * 3 > s.lease_ttl_s + 30:
            raise RuntimeError("HEARTBEAT_S too large for LEASE_TTL_S (need >=3 beats per lease)")
        if s.spec_provider not in {"local", "workhub"}:
            raise RuntimeError("SPEC_PROVIDER must be local|workhub")
        return s
```
## `rouvr/errors.py`
```python
"""rouvr/errors.py — closed error taxonomy + retry mapping (PROPOSAL, mirrors doc C-00)."""
from __future__ import annotations

from datetime import timedelta
from enum import Enum

from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError


class Code(str, Enum):
    THROTTLE = "E_THROTTLE"
    DOWNSTREAM = "E_DOWNSTREAM"
    TRANSPORT = "E_TRANSPORT"
    EVIDENCE = "E_EVIDENCE"
    MOVED = "E_MOVED"
    CONTRACT = "E_CONTRACT"
    CONTRACT_MOVED = "E_CONTRACT_MOVED"
    AUTHZ = "E_AUTHZ"
    SELF_REVIEW = "E_SELF_REVIEW"
    SCOPE = "E_SCOPE"
    INELIGIBLE = "E_INELIGIBLE"
    BAD_REQUEST = "E_BAD_REQUEST"
    CANCELLED = "E_CANCELLED"


RETRYABLE = {Code.THROTTLE, Code.DOWNSTREAM, Code.TRANSPORT}
NON_RETRYABLE = [c.value for c in Code if c not in RETRYABLE]


class RouvrError(Exception):
    def __init__(self, code: Code, message: str, *, details: dict | None = None) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code, self.message, self.details = code, message, details or {}

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE

    def to_temporal(self) -> ApplicationError:
        return ApplicationError(self.message, self.details, type=self.code.value,
                                non_retryable=not self.retryable)


def from_http(status: int, body: dict | None) -> RouvrError:
    """Map a WorkHub HTTP response to a Rouvr code. Unknown 4xx => BAD_REQUEST (non-retryable)."""
    body = body or {}
    wh = str(body.get("code") or body.get("error") or "")
    msg = str(body.get("message") or wh or f"http {status}")
    if status == 429:
        return RouvrError(Code.THROTTLE, msg)
    if status >= 500:
        return RouvrError(Code.DOWNSTREAM, msg)
    if status in (401, 403):
        return RouvrError(Code.AUTHZ, msg)
    if status == 409:
        if "CONTRACT" in wh:
            return RouvrError(Code.CONTRACT_MOVED, msg)
        return RouvrError(Code.MOVED, msg)  # record_version / lease epoch CAS lost
    if status == 422 and "EVIDENCE" in wh:
        return RouvrError(Code.EVIDENCE, msg)
    if status == 422 and "SELF" in wh:
        return RouvrError(Code.SELF_REVIEW, msg)
    if status == 400 and wh == "KEY.REQUIRED":
        return RouvrError(Code.CONTRACT, "Idempotency-Key missing (client bug)")
    return RouvrError(Code.BAD_REQUEST, msg, details={"status": status, "workhub_code": wh})


def retry_policy_for(kind: str) -> RetryPolicy:
    """kind: 'workhub' | 'muse' | 'test' | 'verify'."""
    max_attempts = {"workhub": 8, "muse": 3, "test": 3, "verify": 3}[kind]
    return RetryPolicy(initial_interval=timedelta(seconds=2), backoff_coefficient=2.0,
                       maximum_interval=timedelta(seconds=60), maximum_attempts=max_attempts,
                       non_retryable_error_types=NON_RETRYABLE)
```
## `rouvr/ids.py`
```python
"""rouvr/ids.py — deterministic identifiers (doc: workflow_id / activity_id / handoff fence)."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(obj: Any, n: int) -> str:
    data = obj if isinstance(obj, bytes) else canonical_json(obj)
    return hashlib.sha256(data).hexdigest()[:n]


def workflow_id(tenant: str, task_id: str, contract_rev: str) -> str:
    return f"rouvr-task-{_SAFE.sub('_', tenant)}-{_SAFE.sub('_', task_id)}-{sha([tenant, task_id, contract_rev], 16)}"


def activity_id(name: str, *parts: Any) -> str:
    return f"rouvr-activity-{name}-{sha([name, *parts], 20)}"


def idempotency_key(op: str, *parts: Any) -> str:
    return f"rouvr-{op}-{sha([op, *parts], 32)}"


def handoff_fence(attempt: int, epoch: int) -> str:
    return f"temporal-handoff-{attempt}-e{epoch}"


_SECRETS = re.compile(r"(?i)(bearer\s+[A-Za-z0-9._-]+|(api[_-]?key|token|secret|password)=\S+)")


def sanitize_for_log(text: str, limit: int = 2000) -> str:
    return _SECRETS.sub("[REDACTED]", text)[:limit]
```
## `rouvr/contracts.py`
```python
"""rouvr/contracts.py — dataclasses passed through Temporal (must be JSON-serialisable)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Verdict = Literal["approve", "request_changes", "reject"]


@dataclass
class TaskWorkflowInput:
    tenant_id: str
    project_id: str
    task_id: str
    contract_rev: str
    requested_by: str
    max_rework: int = 2


@dataclass
class Binding:
    task_id: str
    builder_persona: str
    verifier_persona: str
    contract_rev: str
    record_version: int


@dataclass
class Lease:
    task_id: str
    holder: str
    lease_epoch: int
    attempt_no: int
    attempt_id: str
    expires_at: str  # ISO-8601 UTC


@dataclass
class AssignInput:
    wf: TaskWorkflowInput
    binding: Binding
    attempt_no: int
    rework_notes: str = ""
    resume_session_id: str | None = None


@dataclass
class AssignResult:
    attempt_id: str
    lease_epoch: int
    muse_session_id: str
    branch: str
    pr_url: str
    head_sha: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class TestResult:
    run_id: str
    passed: bool
    summary: str
    evidence_id: str


@dataclass
class VerifyResult:
    verdict: Verdict
    notes: str
    verifier_principal: str
    evidence_id: str


@dataclass
class CloseResult:
    final_state: str
    record_version: int
    receipt_digest: str | None = None
```
## `rouvr/workhub/routes.py`
```python
"""rouvr/workhub/routes.py — WorkHub routes Rouvr uses (R1..R10 from the doc).
Paths marked UNVERIFIED are placeholders: confirm against WorkHub before shipping."""
R1_TASK_BY_NUMBER = "/api/v1/projects/{project}/tasks/by-number/{number}"
R2_TASK = "/api/v2/workhub/tasks/{task_id}"
R3_TASK_LIST = "/api/v2/workhub/tasks"  # UNVERIFIED query params
R4_BINDING = "/api/v2/workhub/tasks/{task_id}/assignment-binding"  # prefix UNVERIFIED
R5_EVIDENCE_POST = "/api/v1/tasks/{task_id}/evidence"
R6_EVIDENCE_GET = "/api/v1/tasks/{task_id}/evidence"
R7_EVALUATE = "/api/v1/tasks/{task_id}/verification/evaluate"  # prefix UNVERIFIED
R8_TRANSITIONS = "/api/v1/projects/{project}/tasks/transitions/batch"  # prefix UNVERIFIED
R9_CAPABILITIES = "/api/v1/capabilities"
# R10 assignment lifecycle. [V] route names exist in hyeve-workhub-service workhub_contract_router.py
# (/assignments/claim 5297, /attempts 5341, /results 5376, /assignments/reclaim 5993, /leases/claim 6910,
#  /leases/renew 6941, /reclaim-dead-assignments 7090, /assignment-liveness/renew 7351).
# UNVERIFIED: the URL prefix and path params — confirm against live /openapi.json in roadmap A1.
R10_PREFIX = "/api/v1/workhub"  # UNVERIFIED
R10_CLAIM = R10_PREFIX + "/assignments/claim"
R10_ATTEMPT = R10_PREFIX + "/attempts"
R10_RESULT = R10_PREFIX + "/results"
R10_RECLAIM = R10_PREFIX + "/assignments/reclaim"
R10_LEASE_CLAIM = R10_PREFIX + "/leases/claim"
R10_LEASE_RENEW = R10_PREFIX + "/leases/renew"
R10_RECLAIM_DEAD = R10_PREFIX + "/reclaim-dead-assignments"
R10_LIVENESS_RENEW = R10_PREFIX + "/assignment-liveness/renew"
AUTH_SERVICE_TOKEN = "/v1/auth/service-tokens"
```
## `rouvr/workhub/port.py`
```python
"""rouvr/workhub/port.py — WorkHubPort (roadmap R3): one interface, two backends.
HttpWorkHub = WorkHubClient (real WorkHub, used for every R4–R10 acceptance check, owner decision 29 Oct);
LedgerWorkHub = the public/offline `ledger/` backend, which must grow the same assignment/lease/attempt/result shape."""
from __future__ import annotations

from typing import Protocol


class WorkHubPort(Protocol):
    async def get_task(self, task_id: str) -> dict: ...
    async def get_binding(self, task_id: str) -> dict: ...
    async def claim(self, task_id: str, principal: str, attempt_no: int, ttl_s: int) -> dict: ...
    async def open_attempt(self, task_id: str, attempt_no: int, epoch: int) -> dict: ...
    async def renew_lease(self, task_id: str, assignment_id: str, epoch: int, ttl_s: int, beat: int) -> dict: ...
    async def reclaim_dead(self, task_id: str, attempt_no: int) -> dict: ...
    async def attach_evidence(self, task_id: str, evidence_type: str, fence: str, payload: dict) -> dict: ...
    async def list_evidence(self, task_id: str) -> list[dict]: ...
    async def attach_evidence_exact(self, task_id: str, evidence_type: str, fence: str, payload: dict) -> str: ...
    async def record_result(self, task_id: str, attempt_id: str, epoch: int, result: dict) -> dict: ...
    async def evaluate(self, task_id: str, contract_rev: str) -> dict: ...
    async def transition_batch(self, items: list[dict], key_parts: tuple) -> dict: ...
```
## `rouvr/workhub/client.py`
```python
"""rouvr/workhub/client.py — async WorkHub client. Every mutation carries Idempotency-Key."""
from __future__ import annotations

import time
from typing import Any

import httpx

from rouvr.errors import Code, RouvrError, from_http
from rouvr.ids import idempotency_key
from rouvr.workhub import routes as R


class TokenSource:
    """Fetches + caches a service token (POST /v1/auth/service-tokens). Credentials come from the
    persona file on the host, never from workflow inputs."""

    def __init__(self, http: httpx.AsyncClient, client_id: str, client_secret: str) -> None:
        self._http, self._id, self._secret = http, client_id, client_secret
        self._token: str | None = None
        self._exp = 0.0

    async def get(self) -> str:
        if self._token and time.time() < self._exp - 60:
            return self._token
        r = await self._http.post(R.AUTH_SERVICE_TOKEN, json={"client_id": self._id, "client_secret": self._secret})
        if r.status_code != 200:
            raise from_http(r.status_code, _json(r))
        body = r.json()
        self._token, self._exp = body["token"], time.time() + int(body.get("expires_in", 900))
        return self._token


def _json(r: httpx.Response) -> dict | None:
    try:
        v = r.json()
        return v if isinstance(v, dict) else {"data": v}
    except ValueError:
        return None


class WorkHubClient:
    def __init__(self, base_url: str, tenant_id: str, project_id: str, token: TokenSource | str,
                 *, transport: httpx.AsyncBaseTransport | None = None, timeout_s: float = 15.0) -> None:
        self.tenant_id, self.project_id = tenant_id, project_id
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout_s, transport=transport)
        self._token = token

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _headers(self, idem: str | None) -> dict[str, str]:
        tok = self._token if isinstance(self._token, str) else await self._token.get()
        h = {"Authorization": f"Bearer {tok}", "X-Hyeve-Tenant-Id": self.tenant_id,
             "X-Project-ID": self.project_id, "Accept": "application/json"}
        if idem:
            h["Idempotency-Key"] = idem
        return h

    async def _call(self, method: str, path: str, *, json: Any = None, params: dict | None = None,
                    idem: str | None = None) -> dict:
        if method != "GET" and not idem:
            raise RouvrError(Code.CONTRACT, f"{method} {path} without Idempotency-Key")
        try:
            r = await self._http.request(method, path, json=json, params=params, headers=await self._headers(idem))
        except (httpx.TransportError, httpx.TimeoutException) as e:
            raise RouvrError(Code.TRANSPORT, type(e).__name__) from e
        if r.status_code >= 400:
            raise from_http(r.status_code, _json(r))
        return _json(r) or {}

    # ---- reads
    async def task_by_number(self, number: int) -> dict:
        return await self._call("GET", R.R1_TASK_BY_NUMBER.format(project=self.project_id, number=number))

    async def get_task(self, task_id: str) -> dict:
        return await self._call("GET", R.R2_TASK.format(task_id=task_id))

    async def get_binding(self, task_id: str) -> dict:
        return await self._call("GET", R.R4_BINDING.format(task_id=task_id))

    async def list_evidence(self, task_id: str) -> list[dict]:
        body = await self._call("GET", R.R6_EVIDENCE_GET.format(task_id=task_id))
        return list(body.get("items") or body.get("data") or [])

    async def capabilities(self) -> dict:
        return await self._call("GET", R.R9_CAPABILITIES)

    # ---- mutations
    async def claim(self, task_id: str, principal: str, attempt_no: int, ttl_s: int) -> dict:
        return await self._call("POST", R.R10_CLAIM,
                                json={"task_id": task_id, "principal": principal, "lease_ttl_s": ttl_s, "attempt_no": attempt_no},
                                idem=idempotency_key("claim", task_id, attempt_no))

    async def renew_lease(self, task_id: str, assignment_id: str, epoch: int, ttl_s: int, beat: int) -> dict:
        return await self._call("POST", R.R10_LEASE_RENEW,
                                json={"task_id": task_id, "assignment_id": assignment_id, "expected_lease_epoch": epoch, "lease_ttl_s": ttl_s},
                                idem=idempotency_key("renew", task_id, assignment_id, epoch, beat))

    async def open_attempt(self, task_id: str, attempt_no: int, epoch: int) -> dict:
        return await self._call("POST", R.R10_ATTEMPT,
                                json={"task_id": task_id, "attempt_no": attempt_no, "lease_epoch": epoch},
                                idem=idempotency_key("attempt", task_id, attempt_no))

    async def record_result(self, task_id: str, attempt_id: str, epoch: int, result: dict) -> dict:
        return await self._call("POST", R.R10_RESULT,
                                json={"task_id": task_id, "attempt_id": attempt_id, "lease_epoch": epoch, **result},
                                idem=idempotency_key("result", task_id, attempt_id))

    async def attach_evidence(self, task_id: str, evidence_type: str, fence: str, payload: dict) -> dict:
        return await self._call("POST", R.R5_EVIDENCE_POST.format(task_id=task_id),
                                json={"type": evidence_type, "fence": fence, "payload": payload},
                                idem=idempotency_key("evidence", task_id, evidence_type, fence))

    async def evaluate(self, task_id: str, contract_rev: str) -> dict:
        return await self._call("POST", R.R7_EVALUATE.format(task_id=task_id), json={"contract_rev": contract_rev},
                                idem=idempotency_key("evaluate", task_id, contract_rev))

    async def transition_batch(self, items: list[dict], key_parts: tuple) -> dict:
        """items: [{task_id, to_state, expected_record_version, reason}] — CAS on record_version."""
        return await self._call("POST", R.R8_TRANSITIONS.format(project=self.project_id), json={"items": items},
                                idem=idempotency_key("transition", *key_parts))

    async def reclaim_dead(self, task_id: str, attempt_no: int) -> dict:
        """Used on activity retry (roadmap R5) before re-claiming the same attempt."""
        return await self._call("POST", R.R10_RECLAIM_DEAD, json={"task_id": task_id},
                                idem=idempotency_key("reclaim-dead", task_id, attempt_no))

    # ---- helpers
    async def attach_evidence_exact(self, task_id: str, evidence_type: str, fence: str, payload: dict) -> str:
        """Attach then read back; succeed only if exactly one item with (type, fence) exists."""
        try:
            await self.attach_evidence(task_id, evidence_type, fence, payload)
        except RouvrError as e:
            if e.code is not Code.MOVED:  # 409 duplicate fence => already attached, fall through to readback
                raise
        matches = [i for i in await self.list_evidence(task_id)
                   if i.get("type") == evidence_type and i.get("fence") == fence]
        if len(matches) != 1:
            raise RouvrError(Code.EVIDENCE, f"expected 1 {evidence_type}/{fence}, found {len(matches)}")
        return str(matches[0]["id"])
```
## `rouvr/lease.py`
```python
"""rouvr/lease.py — lease + fencing. The epoch returned by claim is the fencing token; every write
after claim passes it, and WorkHub rejects stale epochs with 409 (=> E_MOVED, non-retryable)."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from rouvr.contracts import Lease
from rouvr.errors import Code, RouvrError
from rouvr.workhub.client import WorkHubClient


async def claim_assignment_and_lease(wh: WorkHubClient, task_id: str, principal: str,
                                     attempt_no: int, ttl_s: int) -> tuple[Lease, str]:
    body = await wh.claim(task_id, principal, attempt_no, ttl_s)
    if body.get("holder") not in (None, principal):
        raise RouvrError(Code.MOVED, f"lease held by {body.get('holder')}")
    lease = Lease(task_id=task_id, holder=principal, lease_epoch=int(body["lease_epoch"]),
                  attempt_no=attempt_no, attempt_id="", expires_at=str(body["expires_at"]))
    return lease, str(body["assignment_id"])


async def open_or_reuse_attempt(wh: WorkHubClient, lease: Lease) -> Lease:
    body = await wh.open_attempt(lease.task_id, lease.attempt_no, lease.lease_epoch)  # idempotent by key
    lease.attempt_id = str(body["attempt_id"])
    return lease


@dataclass
class LeaseKeeper:
    """Background renewer. Runs inside the assign_task activity alongside Temporal heartbeats.
    If renewal ever fails with E_MOVED, `lost` is set and the caller must stop the Muse process."""
    wh: WorkHubClient
    lease: Lease
    assignment_id: str
    ttl_s: int
    every_s: int
    on_beat: Callable[[int], None] = lambda n: None
    lost: asyncio.Event | None = None
    _task: asyncio.Task | None = None

    def start(self) -> None:
        self.lost = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        beat = 0
        assert self.lost is not None
        while True:
            await asyncio.sleep(self.every_s)
            beat += 1
            try:
                await self.wh.renew_lease(self.lease.task_id, self.assignment_id, self.lease.lease_epoch,
                                          self.ttl_s, beat)
                self.on_beat(beat)
            except RouvrError as e:
                if e.code in (Code.MOVED, Code.AUTHZ):
                    self.lost.set()
                    return
                # transient: try again next tick; lease TTL (>=3 beats) absorbs one miss

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


async def run_fenced(keeper: LeaseKeeper, work: Awaitable[object]) -> object:
    """Run `work` but abort as soon as the lease is lost."""
    keeper.start()
    assert keeper.lost is not None
    w = asyncio.ensure_future(work)
    lost = asyncio.ensure_future(keeper.lost.wait())
    try:
        done, _ = await asyncio.wait({w, lost}, return_when=asyncio.FIRST_COMPLETED)
        if lost in done:
            w.cancel()
            raise RouvrError(Code.MOVED, "lease lost during work")
        return w.result()
    finally:
        lost.cancel()
        await keeper.stop()
```
## `rouvr/personas.py`
```python
"""rouvr/personas.py — persona registry. Source: the AIX mapping (owner decision 3, 29 Oct 2026); YAML kept as fallback. Secrets resolved from files, never env/inputs.

tenants.yaml:
  tenants:
    default:
      personas:
        builder-1: {role: builder, principal: svc-rouvr-builder-1, muse_model: muse-wonder-1.0,
                    credential_file: /srv/rouvr/personas/builder-1.json, github_app: rouvr-builder}
        verifier-1: {role: verifier, principal: svc-rouvr-verifier-1, muse_model: muse-spark,
                     credential_file: /srv/rouvr/personas/verifier-1.json}
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from rouvr.errors import Code, RouvrError

Role = Literal["builder", "verifier"]


@dataclass(frozen=True)
class Persona:
    name: str
    role: Role
    principal: str
    muse_model: str
    credential_file: Path
    github_app: str | None = None

    def credentials(self) -> dict:
        try:
            return json.loads(self.credential_file.read_text())
        except FileNotFoundError as e:
            raise RouvrError(Code.AUTHZ, f"credential file missing for {self.name}") from e


class PersonaRegistry:
    def __init__(self, data: dict) -> None:
        self._t: dict[str, dict[str, Persona]] = {}
        for tenant, td in (data.get("tenants") or {}).items():
            self._t[tenant] = {n: Persona(name=n, role=p["role"], principal=p["principal"],
                                          muse_model=p["muse_model"], credential_file=Path(p["credential_file"]),
                                          github_app=p.get("github_app"))
                               for n, p in (td.get("personas") or {}).items()}

    @classmethod
    def load(cls, path: Path) -> "PersonaRegistry":
        return cls(yaml.safe_load(path.read_text()) or {})

    def get(self, tenant: str, name: str, role: Role) -> Persona:
        p = self._t.get(tenant, {}).get(name)
        if p is None:
            raise RouvrError(Code.SCOPE, f"persona {name} not registered for tenant {tenant}")
        if p.role != role:
            raise RouvrError(Code.INELIGIBLE, f"persona {name} is {p.role}, need {role}")
        return p

    def pair(self, tenant: str, builder: str, verifier: str) -> tuple[Persona, Persona]:
        b, v = self.get(tenant, builder, "builder"), self.get(tenant, verifier, "verifier")
        if b.principal == v.principal:
            raise RouvrError(Code.SELF_REVIEW, "verifier principal equals builder principal")
        return b, v


# --- Owner decision 3 (29 Oct 2026): the AIX mapping is the persona registry source. ---
# ASSUMPTION (UNVERIFIED): the AIX mapping format. The port map only shows a static role -> principal
# mapping (bridge_creds.py, LANE_WORKHUB_CREDS creds JSON). Assumed shape, to confirm against AIX in R6:
#   {"roles": {"builder":  {"principal": "svc-...", "creds_file": "/srv/...json", "model": "muse-wonder-1.0"},
#              "reviewer": {"principal": "svc-...", "creds_file": "/srv/...json", "model": "muse-spark"},
#              "closer":   {"principal": "svc-...", "creds_file": "/srv/...json"}}}
# AIX "reviewer" maps to the Rouvr "verifier" role. Persona name = AIX role name. Single tenant.
AIX_ROLE_MAP = {"builder": "builder", "reviewer": "verifier", "verifier": "verifier"}
DEFAULT_MODELS = {"builder": "muse-wonder-1.0", "verifier": "muse-spark"}


def registry_from_aix_mapping(data: dict, tenant: str) -> PersonaRegistry:
    roles = data.get("roles")
    if not isinstance(roles, dict):
        raise RouvrError(Code.CONTRACT, "AIX mapping has no 'roles' object (format is an ASSUMPTION; see personas.py)")
    personas: dict[str, dict] = {}
    for aix_role, r in roles.items():
        role = AIX_ROLE_MAP.get(aix_role)
        if role is None:
            continue  # closer etc.: not a Muse persona
        if "principal" not in r or "creds_file" not in r:
            raise RouvrError(Code.CONTRACT, f"AIX mapping role {aix_role} lacks principal/creds_file")
        personas[aix_role] = {"role": role, "principal": r["principal"],
                              "muse_model": r.get("model", DEFAULT_MODELS[role]),
                              "credential_file": r["creds_file"]}
    return PersonaRegistry({"tenants": {tenant: {"personas": personas}}})


def load_aix_mapping(path: Path, tenant: str) -> PersonaRegistry:
    return registry_from_aix_mapping(json.loads(path.read_text()), tenant)
```
## `rouvr/execspec.py`
```python
"""rouvr/execspec.py — execution spec provider: which repo/branch/commands a task runs with."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

from rouvr.errors import Code, RouvrError


@dataclass(frozen=True)
class ExecSpec:
    repo: str                     # "org/name"
    base_branch: str
    test_command: list[str]
    allowed_paths: list[str] = field(default_factory=list)
    timeout_s: int = 1500
    digest: str = ""              # sha256 of the canonical spec; recorded as execution_spec evidence


class ExecSpecProvider(Protocol):
    async def get(self, tenant: str, task: dict) -> ExecSpec: ...


class LocalExecSpecProvider:
    """Reads specs/<project>.yaml from a host directory. Phase 1 default (SPEC_PROVIDER=local)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    async def get(self, tenant: str, task: dict) -> ExecSpec:
        project = str(task.get("project_id") or task.get("project") or "")
        path = self.root / tenant / f"{project}.yaml"
        if not path.exists():
            raise RouvrError(Code.CONTRACT, f"no exec spec at {path}")
        raw = path.read_bytes()
        d = yaml.safe_load(raw) or {}
        for k in ("repo", "base_branch", "test_command"):
            if k not in d:
                raise RouvrError(Code.CONTRACT, f"exec spec missing {k}")
        return ExecSpec(repo=d["repo"], base_branch=d["base_branch"], test_command=list(d["test_command"]),
                        allowed_paths=list(d.get("allowed_paths") or []), timeout_s=int(d.get("timeout_s", 1500)),
                        digest=hashlib.sha256(raw).hexdigest())
```
## `rouvr/muse.py`
```python
"""rouvr/muse.py — Muse Code CLI headless wrapper (single launcher; ports hyeve-lane aix_muse_worker.py).
[V] argv used today by hyeve-lane `_task_loop_child_argv` (workflows.py:2033):
    muse exec --json --provider meta --session-id <sid> --prompt-file <f> --workspace <ws> --max-model-steps 40
Rouvr mints the sid (uuid4) per attempt and records it BEFORE launch (session-binding row).
UNVERIFIED (roadmap R2): whether re-running with the same --session-id resumes prior context. Until R2
says RESUME, RESUME_SUPPORTED=false and a retry respawns on the same attempt with a new sid."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rouvr.errors import Code, RouvrError
from rouvr.ids import sanitize_for_log

FLAGS = {"json": "--json", "provider": "--provider", "session": "--session-id", "prompt_file": "--prompt-file",
         "workspace": "--workspace", "max_steps": "--max-model-steps", "model": "--model"}  # --model UNVERIFIED
PROVIDER = "meta"
MAX_MODEL_STEPS = 40


@dataclass
class MuseTurn:
    session_id: str
    exit_code: int
    events: int
    last_message: str


class MuseLauncher:
    def __init__(self, bin_path: str = "muse", kill_grace_s: float = 10.0) -> None:
        self.bin, self.grace = bin_path, kill_grace_s

    def argv(self, prompt_file: Path, model: str, cwd: Path, session_id: str) -> list[str]:
        return [self.bin, "exec", FLAGS["json"], FLAGS["provider"], PROVIDER, FLAGS["session"], session_id,
                FLAGS["prompt_file"], str(prompt_file), FLAGS["workspace"], str(cwd),
                FLAGS["max_steps"], str(MAX_MODEL_STEPS), FLAGS["model"], model]

    async def run(self, *, prompt: str, model: str, cwd: Path, env: dict[str, str], timeout_s: int,
                  resume: str | None = None, on_event: Callable[[dict], None] = lambda e: None,
                  cancel: asyncio.Event | None = None) -> MuseTurn:
        sid = resume or str(uuid.uuid4())  # resume => same sid (R2); else fresh sid per leg
        prompt_file = cwd.parent / f".rouvr-prompt-{sid}.md"   # outside the repo worktree
        prompt_file.write_text(prompt)
        proc = await asyncio.create_subprocess_exec(
            *self.argv(prompt_file, model, cwd, sid), cwd=str(cwd),
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(cwd), **env},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        session_id, events, last = sid, 0, ""

        async def pump() -> None:
            nonlocal session_id, events, last
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                events += 1
                session_id = str(ev.get("session_id") or session_id)
                if ev.get("type") in ("message", "result"):
                    last = sanitize_for_log(str(ev.get("text") or ev.get("result") or ""))
                on_event(ev)

        pump_t = asyncio.ensure_future(pump())
        waiters = {pump_t}
        cancel_t = asyncio.ensure_future(cancel.wait()) if cancel else None
        if cancel_t:
            waiters.add(cancel_t)
        try:
            done, _ = await asyncio.wait(waiters, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED)
            if pump_t not in done:
                await self._kill(proc)
                raise RouvrError(Code.CANCELLED if cancel_t in done else Code.DOWNSTREAM,
                                 "muse turn cancelled" if cancel_t in done else "muse turn timed out",
                                 details={"session_id": session_id})
            rc = await proc.wait()
        finally:
            if cancel_t:
                cancel_t.cancel()
        if rc != 0:
            err = sanitize_for_log((await proc.stderr.read()).decode(errors="replace")) if proc.stderr else ""
            raise RouvrError(Code.DOWNSTREAM, f"muse exit {rc}: {err}", details={"session_id": session_id})
        if not session_id:
            raise RouvrError(Code.CONTRACT, "muse produced no session_id")
        return MuseTurn(session_id=session_id, exit_code=rc, events=events, last_message=last)

    async def _kill(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), self.grace)
        except asyncio.TimeoutError:
            os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()


def build_task_prompt(task: dict, spec_repo: str, branch: str, rework_notes: str) -> str:
    parts = [f"Task {task.get('number', task.get('id'))}: {task.get('title', '')}", "",
             str(task.get("description") or ""), "", "Acceptance criteria:"]
    parts += [f"- {c}" for c in task.get("acceptance_criteria") or []]
    parts += ["", f"Repository {spec_repo}, work on branch {branch}. Commit and push; do not merge.",
              "Run the project's tests before finishing."]
    if rework_notes:
        parts += ["", "Reviewer requested changes:", rework_notes]
    return "\n".join(parts)
```
## `rouvr/gitops.py`
```python
"""rouvr/gitops.py — workspace checkout + PR lookup/creation via `gh` CLI (idempotent by branch)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from rouvr.errors import Code, RouvrError


async def _run(*argv: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    p = await asyncio.create_subprocess_exec(*argv, cwd=str(cwd) if cwd else None, env=env,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate()
    if p.returncode != 0:
        raise RouvrError(Code.DOWNSTREAM, f"{argv[0]} {argv[1]} failed: {err.decode()[-500:]}")
    return out.decode().strip()


def branch_for(task_id: str, attempt_no: int) -> str:
    return f"rouvr/{task_id}/a{attempt_no}"


async def prepare_workspace(root: Path, repo: str, base: str, branch: str) -> Path:
    ws = root / branch.replace("/", "__")
    if not (ws / ".git").exists():
        ws.parent.mkdir(parents=True, exist_ok=True)
        await _run("git", "clone", "--branch", base, f"https://github.com/{repo}.git", str(ws))
    await _run("git", "fetch", "origin", cwd=ws)
    await _run("git", "checkout", "-B", branch, f"origin/{base}", cwd=ws)  # reset: attempt is a clean slate
    return ws


async def ensure_pr(ws: Path, repo: str, base: str, branch: str, title: str, env: dict[str, str]) -> tuple[str, str]:
    """Returns (pr_url, head_sha). Reuses an open PR for the branch if one exists."""
    head = await _run("git", "rev-parse", "HEAD", cwd=ws)
    existing = json.loads(await _run("gh", "pr", "list", "-R", repo, "--head", branch, "--state", "open",
                                     "--json", "url", env=env) or "[]")
    if existing:
        return existing[0]["url"], head
    url = await _run("gh", "pr", "create", "-R", repo, "--base", base, "--head", branch, "--title", title,
                     "--body", "Opened by Rouvr.", env=env)
    return url, head
```
## `rouvr/testrunner.py`
```python
"""rouvr/testrunner.py — run the spec's test command in the PR workspace (local) with a hard timeout.
A remote testrun-runner client can implement the same `TestRunner` protocol."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rouvr.errors import Code, RouvrError
from rouvr.ids import sanitize_for_log


@dataclass
class TestRun:
    run_id: str
    passed: bool
    exit_code: int
    output_tail: str


class TestRunner(Protocol):
    async def run(self, cwd: Path, command: list[str], timeout_s: int, run_key: str) -> TestRun: ...


class LocalTestRunner:
    async def run(self, cwd: Path, command: list[str], timeout_s: int, run_key: str) -> TestRun:
        proc = await asyncio.create_subprocess_exec(*command, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout_s)
        except asyncio.TimeoutError as e:
            proc.kill()
            await proc.wait()
            raise RouvrError(Code.DOWNSTREAM, "tests timed out") from e
        rc = proc.returncode or 0
        return TestRun(run_id=f"local-{hashlib.sha256(run_key.encode()).hexdigest()[:16]}", passed=rc == 0,
                       exit_code=rc, output_tail=sanitize_for_log(out.decode(errors="replace")[-4000:]))
```
## `rouvr/db.py`
```python
"""rouvr/db.py — asyncpg pool + repositories for phase-2 tables (migrations/001_phase2.sql)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import asyncpg


async def create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=10,
                                     init=lambda c: c.set_type_codec("jsonb", encoder=json.dumps,
                                                                     decoder=json.loads, schema="pg_catalog"))


@dataclass
class OutboxRow:
    id: int
    tenant_id: str
    event_id: str
    event_type: str
    payload: dict
    attempts: int


class StepLogRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def record(self, *, tenant_id: str, task_id: str, attempt_id: str, activity_id: str, name: str,
                     outcome: str, correlation_id: str, reason_code: str | None = None,
                     evidence_refs: list[str] | None = None, envelope: dict | None = None) -> bool:
        """Returns False if this activity_id was already logged (replay)."""
        r = await self.pool.execute(
            """INSERT INTO step_log (tenant_id,task_id,attempt_id,activity_id,activity_name,outcome,reason_code,
               evidence_refs,correlation_id,envelope) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
               ON CONFLICT (tenant_id, activity_id) DO NOTHING""",
            tenant_id, task_id, attempt_id, activity_id, name, outcome, reason_code, evidence_refs or [],
            correlation_id, envelope)
        return r.endswith("1")


class OutboxRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def enqueue(self, conn: asyncpg.Connection, *, tenant_id: str, aggregate: str, aggregate_id: str,
                      event_type: str, payload: dict) -> None:
        """Call inside the SAME transaction as the state change."""
        await conn.execute("INSERT INTO outbox (tenant_id,aggregate,aggregate_id,event_type,payload) "
                           "VALUES ($1,$2,$3,$4,$5)", tenant_id, aggregate, aggregate_id, event_type, payload)

    async def claim_batch(self, conn: asyncpg.Connection, limit: int = 50) -> list[OutboxRow]:
        rows = await conn.fetch("""SELECT id,tenant_id,event_id::text,event_type,payload,attempts FROM outbox
               WHERE published_at IS NULL AND attempts < 20 ORDER BY id LIMIT $1 FOR UPDATE SKIP LOCKED""", limit)
        return [OutboxRow(**dict(r)) for r in rows]

    async def mark_published(self, conn: asyncpg.Connection, ids: list[int]) -> None:
        await conn.execute("UPDATE outbox SET published_at=now() WHERE id = ANY($1::bigint[])", ids)

    async def mark_failed(self, conn: asyncpg.Connection, row_id: int, err: str) -> None:
        await conn.execute("UPDATE outbox SET attempts=attempts+1,last_error=$2 WHERE id=$1", row_id, err[:500])


class EscalationRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def insert_once(self, conn: asyncpg.Connection, *, tenant_id: str, task_id: str, dedupe_key: str,
                          reason: str, recipient: str) -> bool:
        r = await conn.execute("""INSERT INTO escalations (tenant_id,task_id,dedupe_key,reason,recipient,status)
               VALUES ($1,$2,$3,$4,$5,'pending') ON CONFLICT (tenant_id,dedupe_key) DO NOTHING""",
                               tenant_id, task_id, dedupe_key, reason, recipient)
        return r.endswith("1")


class ReceiptRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def save(self, *, tenant_id: str, task_id: str, contract_rev: str, attempt_id: str, workflow_run_id: str,
                   muse_session_id: str, pr_url: str, test_run_id: str, digest: str, signature: bytes,
                   key_id: str) -> None:
        await self.pool.execute("""INSERT INTO receipts (tenant_id,task_id,contract_rev,attempt_id,workflow_run_id,
               muse_session_id,pr_url,test_run_id,digest_sha256,signature,key_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) ON CONFLICT (tenant_id,task_id,attempt_id) DO NOTHING""",
                                tenant_id, task_id, contract_rev, attempt_id, workflow_run_id, muse_session_id,
                                pr_url, test_run_id, digest, signature, key_id)


class ScheduleRepo:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def get(self, schedule_id: str) -> asyncpg.Record | None:
        return await self.pool.fetchrow("SELECT * FROM recurring_schedules WHERE id=$1", schedule_id)

    async def mark_fired(self, schedule_id: str, at: datetime) -> None:
        await self.pool.execute("UPDATE recurring_schedules SET last_fired_at=$2 WHERE id=$1", schedule_id, at)
```
## `rouvr/escalation.py`
```python
"""rouvr/escalation.py — escalate_once: at most one escalation per (tenant, dedupe_key)."""
from __future__ import annotations

import asyncpg

from rouvr.db import EscalationRepo, OutboxRepo
from rouvr.ids import sha


async def escalate_once(pool: asyncpg.Pool, *, tenant_id: str, task_id: str, reason: str, recipient: str,
                        attempt_id: str) -> bool:
    """Insert escalation + outbox event atomically. Returns True only for the first caller.
    The relay (outbox.py) does the actual notification, so this is safe to retry."""
    key = sha([task_id, reason, attempt_id], 32)
    async with pool.acquire() as conn, conn.transaction():
        first = await EscalationRepo(pool).insert_once(conn, tenant_id=tenant_id, task_id=task_id,
                                                       dedupe_key=key, reason=reason, recipient=recipient)
        if first:
            await OutboxRepo(pool).enqueue(conn, tenant_id=tenant_id, aggregate="escalation", aggregate_id=key,
                                           event_type="escalation.created",
                                           payload={"task_id": task_id, "reason": reason, "recipient": recipient})
        return first
```
## `rouvr/outbox.py`
```python
"""rouvr/outbox.py — outbox relay: at-least-once publish; consumers dedupe on event_id."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import asyncpg
import httpx

from rouvr.config import Settings
from rouvr.db import OutboxRepo, OutboxRow, create_pool

log = logging.getLogger("rouvr.outbox")
Publisher = Callable[[OutboxRow], Awaitable[None]]


async def relay_once(pool: asyncpg.Pool, publish: Publisher, limit: int = 50) -> int:
    repo = OutboxRepo(pool)
    async with pool.acquire() as conn, conn.transaction():
        rows = await repo.claim_batch(conn, limit)
        ok: list[int] = []
        for row in rows:
            try:
                await publish(row)
                ok.append(row.id)
            except Exception as e:  # noqa: BLE001 - relay must never die on one bad row
                log.warning("publish failed id=%s: %s", row.id, e)
                await repo.mark_failed(conn, row.id, str(e))
        if ok:
            await repo.mark_published(conn, ok)
        return len(ok)


def webhook_publisher(url: str) -> Publisher:
    client = httpx.AsyncClient(timeout=10)

    async def pub(row: OutboxRow) -> None:
        r = await client.post(url, json={"event_id": row.event_id, "type": row.event_type, "payload": row.payload},
                              headers={"Idempotency-Key": row.event_id})
        r.raise_for_status()
    return pub


async def run(poll_s: float = 2.0) -> None:
    import os
    s = Settings.from_env()
    pool = await create_pool(s.database_url)
    pub = webhook_publisher(os.environ.get("ROUVR_EVENTS_WEBHOOK", "http://localhost:9000/events"))
    while True:
        n = await relay_once(pool, pub)
        if n == 0:
            await asyncio.sleep(poll_s)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
```
## `rouvr/receipts.py`
```python
"""rouvr/receipts.py — Ed25519-signed completion receipts over canonical JSON."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from rouvr.ids import canonical_json


@dataclass(frozen=True)
class Receipt:
    version: str
    tenant_id: str
    task_id: str
    contract_rev: str
    attempt_id: str
    workflow_run_id: str
    muse_session_id: str
    pr_url: str
    head_sha: str
    test_run_id: str
    verdict: str
    verifier_principal: str
    closed_at: str


def digest(r: Receipt) -> str:
    return hashlib.sha256(canonical_json(asdict(r))).hexdigest()


def load_key(path: Path) -> Ed25519PrivateKey:
    k = load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(k, Ed25519PrivateKey):
        raise ValueError("receipt key must be Ed25519")
    return k


def sign(r: Receipt, key: Ed25519PrivateKey) -> tuple[str, bytes]:
    d = digest(r)
    return d, key.sign(bytes.fromhex(d))


def verify(r: Receipt, signature: bytes, pub: Ed25519PublicKey) -> bool:
    try:
        pub.verify(signature, bytes.fromhex(digest(r)))
        return True
    except Exception:
        return False
```
## `rouvr/host.py`
```python
"""rouvr/host.py — dependency container built once per worker process; activities receive it."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from rouvr.config import Settings
from rouvr.execspec import ExecSpecProvider, LocalExecSpecProvider
from rouvr.muse import MuseLauncher
from rouvr.personas import Persona, PersonaRegistry, load_aix_mapping
from rouvr.testrunner import LocalTestRunner, TestRunner
from rouvr.workhub.client import WorkHubClient


@dataclass
class Host:
    settings: Settings
    personas: PersonaRegistry
    exec_specs: ExecSpecProvider
    muse: MuseLauncher
    tests: TestRunner
    workhub_for: Callable[[Persona | None], WorkHubClient]
    pool: asyncpg.Pool | None = None  # phase 2 only


def build_host(s: Settings, pool: asyncpg.Pool | None = None) -> Host:
    reg = (load_aix_mapping(s.aix_mapping_path, s.tenant_id) if s.persona_source == "aix"
           else PersonaRegistry.load(s.persona_registry_path))
    cache: dict[str, WorkHubClient] = {}

    def wh(p: Persona | None) -> WorkHubClient:
        key = p.name if p else "_orchestrator"
        if key not in cache:
            token = p.credentials()["workhub_token"] if p else Path("/srv/rouvr/keys/orchestrator.token").read_text().strip()
            cache[key] = WorkHubClient(s.workhub_base_url, s.tenant_id, s.project_id, token)
        return cache[key]

    return Host(settings=s, personas=reg, exec_specs=LocalExecSpecProvider(Path("/srv/rouvr/specs")),
                muse=MuseLauncher(s.muse_bin), tests=LocalTestRunner(), workhub_for=wh, pool=pool)
```
## `rouvr/activities.py`
```python
"""rouvr/activities.py — Temporal activities. All side effects live here; workflows stay deterministic."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from temporalio import activity

from rouvr.contracts import (AssignInput, AssignResult, Binding, CloseResult, TaskWorkflowInput, TestResult,
                             VerifyResult)
from rouvr.errors import Code, RouvrError
from rouvr.gitops import branch_for, ensure_pr, prepare_workspace
from rouvr.host import Host
from rouvr.ids import handoff_fence
from rouvr.lease import LeaseKeeper, claim_assignment_and_lease, open_or_reuse_attempt, run_fenced
from rouvr.muse import build_task_prompt


def _wrap(fn):
    """Convert RouvrError -> ApplicationError with the right non_retryable flag."""
    import functools

    @functools.wraps(fn)
    async def inner(*a, **kw):
        try:
            return await fn(*a, **kw)
        except RouvrError as e:
            raise e.to_temporal() from e
    return inner


class Activities:
    def __init__(self, host: Host) -> None:
        self.h = host

    # -- orchestrator queue ---------------------------------------------------------------
    @activity.defn(name="verify_binding")
    @_wrap
    async def verify_binding(self, wf: TaskWorkflowInput) -> Binding:
        wh = self.h.workhub_for(None)
        task, b = await wh.get_task(wf.task_id), await wh.get_binding(wf.task_id)
        if str(task.get("contract_rev")) != wf.contract_rev:
            raise RouvrError(Code.CONTRACT_MOVED, "contract revision changed since start")
        self.h.personas.pair(wf.tenant_id, b["builder_persona"], b["verifier_persona"])  # raises SELF_REVIEW/SCOPE
        return Binding(task_id=wf.task_id, builder_persona=b["builder_persona"],
                       verifier_persona=b["verifier_persona"], contract_rev=wf.contract_rev,
                       record_version=int(task["record_version"]))

    # -- builder queue ---------------------------------------------------------------------
    @activity.defn(name="assign_task")
    @_wrap
    async def assign_task(self, inp: AssignInput) -> AssignResult:
        s, wf = self.h.settings, inp.wf
        persona = self.h.personas.get(wf.tenant_id, inp.binding.builder_persona, "builder")
        wh = self.h.workhub_for(persona)
        task = await wh.get_task(wf.task_id)
        spec = await self.h.exec_specs.get(wf.tenant_id, task)
        if activity.info().attempt > 1:  # retry after worker death: reclaim on the same attempt (R5)
            await wh.reclaim_dead(wf.task_id, inp.attempt_no)
        lease, assignment_id = await claim_assignment_and_lease(wh, wf.task_id, persona.principal,
                                                                inp.attempt_no, s.lease_ttl_s)
        lease = await open_or_reuse_attempt(wh, lease)
        fence = handoff_fence(inp.attempt_no, lease.lease_epoch)
        ev = [await wh.attach_evidence_exact(wf.task_id, "temporal_handoff", fence,
                                             {"workflow_id": activity.info().workflow_id,
                                              "run_id": activity.info().workflow_run_id,
                                              "attempt_id": lease.attempt_id}),
              await wh.attach_evidence_exact(wf.task_id, "execution_spec", fence,
                                             {"digest": spec.digest, "repo": spec.repo})]

        # Resume: heartbeat details carry the Muse session id from a previous activity attempt.
        details = activity.info().heartbeat_details
        resume = inp.resume_session_id or (details[0].get("session_id") if details else None)
        if resume and not s.resume_supported:
            resume = None  # UNVERIFIED --resume: start a fresh turn instead
        state = {"session_id": resume or "", "beat": 0, "attempt_id": lease.attempt_id}

        def beat(n: int) -> None:
            state["beat"] = n
            activity.heartbeat(state)  # Temporal heartbeat (timeout 90 s) rides on lease renewals

        keeper = LeaseKeeper(wh, lease, assignment_id, s.lease_ttl_s, s.heartbeat_s, on_beat=beat)
        branch = branch_for(wf.task_id, inp.attempt_no)

        async def work() -> AssignResult:
            ws = await prepare_workspace(s.workspace_root, spec.repo, spec.base_branch, branch)
            creds = persona.credentials()
            env = {"MUSE_API_KEY": creds.get("muse_api_key", ""), "GH_TOKEN": creds.get("github_token", "")}

            def on_event(e: dict) -> None:
                if e.get("session_id"):
                    state["session_id"] = str(e["session_id"])

            turn = await self.h.muse.run(prompt=build_task_prompt(task, spec.repo, branch, inp.rework_notes),
                                         model=persona.muse_model, cwd=ws, env=env, timeout_s=spec.timeout_s,
                                         resume=resume, on_event=on_event)
            ev.append(await wh.attach_evidence_exact(wf.task_id, "muse_session", fence,
                                                     {"session_id": turn.session_id, "events": turn.events}))
            pr_url, head = await ensure_pr(ws, spec.repo, spec.base_branch, branch,
                                           f"[Rouvr] {task.get('title', wf.task_id)}", env)
            ev.append(await wh.attach_evidence_exact(wf.task_id, "pr_record", fence,
                                                     {"url": pr_url, "head_sha": head, "branch": branch}))
            await wh.record_result(wf.task_id, lease.attempt_id, lease.lease_epoch,
                                   {"status": "built", "pr_url": pr_url, "head_sha": head})
            return AssignResult(attempt_id=lease.attempt_id, lease_epoch=lease.lease_epoch,
                                muse_session_id=turn.session_id, branch=branch, pr_url=pr_url, head_sha=head,
                                evidence_ids=ev)

        activity.heartbeat(state)
        try:
            return await run_fenced(keeper, work())  # type: ignore[return-value]
        except asyncio.CancelledError:
            raise RouvrError(Code.CANCELLED, "activity cancelled")

    # -- test queue ------------------------------------------------------------------------
    @activity.defn(name="run_tests")
    @_wrap
    async def run_tests(self, wf: TaskWorkflowInput, built: AssignResult) -> TestResult:
        wh = self.h.workhub_for(None)
        spec = await self.h.exec_specs.get(wf.tenant_id, await wh.get_task(wf.task_id))
        ws = self.h.settings.workspace_root / built.branch.replace("/", "__")
        run = await self.h.tests.run(ws, spec.test_command, spec.timeout_s, f"{built.attempt_id}:{built.head_sha}")
        fence = handoff_fence(int(built.branch.rsplit("a", 1)[-1]), built.lease_epoch)
        eid = await wh.attach_evidence_exact(wf.task_id, "test_evidence", fence,
                                             {"run_id": run.run_id, "passed": run.passed, "exit_code": run.exit_code,
                                              "head_sha": built.head_sha, "tail": run.output_tail})
        return TestResult(run_id=run.run_id, passed=run.passed, summary=run.output_tail[-500:], evidence_id=eid)

    # -- verifier queue --------------------------------------------------------------------
    @activity.defn(name="verify")
    @_wrap
    async def verify(self, wf: TaskWorkflowInput, b: Binding, built: AssignResult, tests: TestResult) -> VerifyResult:
        builder, verifier = self.h.personas.pair(wf.tenant_id, b.builder_persona, b.verifier_persona)
        wh = self.h.workhub_for(verifier)
        ws = self.h.settings.workspace_root / built.branch.replace("/", "__")
        prompt = (f"Review PR {built.pr_url} at {built.head_sha} against the task acceptance criteria. "
                  f"Tests passed={tests.passed}. Reply with a final line 'VERDICT: approve|request_changes|reject' "
                  "followed by notes.")
        creds = verifier.credentials()
        turn = await self.h.muse.run(prompt=prompt, model=verifier.muse_model, cwd=ws,
                                     env={"MUSE_API_KEY": creds.get("muse_api_key", "")}, timeout_s=900)
        verdict = parse_verdict(turn.last_message)
        fence = handoff_fence(int(built.branch.rsplit("a", 1)[-1]), built.lease_epoch)
        eid = await wh.attach_evidence_exact(wf.task_id, "review_verdict", fence,
                                             {"verdict": verdict, "notes": turn.last_message[-2000:],
                                              "verifier": verifier.principal, "builder": builder.principal,
                                              "head_sha": built.head_sha})
        return VerifyResult(verdict=verdict, notes=turn.last_message[-2000:], verifier_principal=verifier.principal,
                            evidence_id=eid)

    # -- close (orchestrator) --------------------------------------------------------------
    @activity.defn(name="close_task")
    @_wrap
    async def close_task(self, wf: TaskWorkflowInput, b: Binding, attempt_id: str) -> CloseResult:
        wh = self.h.workhub_for(None)
        ev = await wh.evaluate(wf.task_id, wf.contract_rev)                      # R7
        if not ev.get("satisfied"):
            raise RouvrError(Code.EVIDENCE, f"evaluate not satisfied: {ev.get('missing')}")
        cur = await wh.get_task(wf.task_id)
        if cur.get("state") == "done":                                           # replay after success
            return CloseResult("done", int(cur["record_version"]))
        await wh.transition_batch([{"task_id": wf.task_id, "to_state": "done",
                                    "expected_record_version": int(cur["record_version"]),
                                    "reason": f"rouvr attempt {attempt_id}"}], (wf.task_id, attempt_id, "done"))
        after = await wh.get_task(wf.task_id)                                    # readback
        if after.get("state") != "done":
            raise RouvrError(Code.MOVED, "readback state != done")
        return CloseResult("done", int(after["record_version"]))

    @activity.defn(name="mark_blocked")
    @_wrap
    async def mark_blocked(self, wf: TaskWorkflowInput, reason: str) -> CloseResult:
        wh = self.h.workhub_for(None)
        cur = await wh.get_task(wf.task_id)
        if cur.get("state") != "blocked":
            await wh.transition_batch([{"task_id": wf.task_id, "to_state": "blocked",
                                        "expected_record_version": int(cur["record_version"]), "reason": reason}],
                                      (wf.task_id, "blocked", reason))
        cur = await wh.get_task(wf.task_id)
        return CloseResult(str(cur.get("state")), int(cur["record_version"]))

    @activity.defn(name="escalate")
    @_wrap
    async def escalate(self, wf: TaskWorkflowInput, reason: str, attempt_id: str) -> bool:
        if self.h.pool is None:
            activity.logger.warning("escalation (phase1 log only) task=%s reason=%s", wf.task_id, reason)
            return True
        from rouvr.escalation import escalate_once
        return await escalate_once(self.h.pool, tenant_id=wf.tenant_id, task_id=wf.task_id, reason=reason,
                                   recipient=wf.requested_by, attempt_id=attempt_id)

    @activity.defn(name="sign_receipt")
    @_wrap
    async def sign_receipt(self, wf: TaskWorkflowInput, built: AssignResult, tests: TestResult,
                           v: VerifyResult) -> str:
        from rouvr.db import ReceiptRepo
        from rouvr.receipts import Receipt, load_key, sign
        s = self.h.settings
        r = Receipt(version="rouvr-receipt-v1", tenant_id=wf.tenant_id, task_id=wf.task_id,
                    contract_rev=wf.contract_rev, attempt_id=built.attempt_id,
                    workflow_run_id=activity.info().workflow_run_id, muse_session_id=built.muse_session_id,
                    pr_url=built.pr_url, head_sha=built.head_sha, test_run_id=tests.run_id, verdict=v.verdict,
                    verifier_principal=v.verifier_principal, closed_at=datetime.now(timezone.utc).isoformat())
        d, sig = sign(r, load_key(s.receipt_key_path))
        if self.h.pool is not None:
            await ReceiptRepo(self.h.pool).save(tenant_id=wf.tenant_id, task_id=wf.task_id,
                                                contract_rev=wf.contract_rev, attempt_id=built.attempt_id,
                                                workflow_run_id=r.workflow_run_id, muse_session_id=r.muse_session_id,
                                                pr_url=r.pr_url, test_run_id=r.test_run_id, digest=d, signature=sig,
                                                key_id=s.receipt_key_id)
        return d


def parse_verdict(text: str) -> str:
    for line in reversed(text.strip().splitlines()):
        line = line.strip().lower()
        if line.startswith("verdict:"):
            v = line.split(":", 1)[1].strip().replace("changes_required", "request_changes")
            if v in ("approve", "request_changes", "reject"):  # roadmap R9 wording: APPROVE / CHANGES_REQUIRED
                return v
    return "request_changes"  # fail closed: unparseable review never approves
```
## `rouvr/workflows.py`
```python
"""rouvr/workflows.py — TaskWorkflow (build→test→verify→close with bounded rework) + ScheduledRunWorkflow."""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from rouvr.activities import Activities
    from rouvr.contracts import AssignInput, TaskWorkflowInput
    from rouvr.errors import retry_policy_for
    from rouvr.ids import workflow_id

Q_ORCH, Q_BUILD, Q_TEST, Q_VERIFY = "rouvr-orchestrator", "rouvr-builder", "rouvr-test", "rouvr-verifier"


def _code(err: BaseException) -> str:
    c = err.cause if isinstance(err, ActivityError) else err
    return c.type if isinstance(c, ApplicationError) and c.type else "E_DOWNSTREAM"


@workflow.defn(name="TaskWorkflow")
class TaskWorkflow:
    def __init__(self) -> None:
        self.phase = "init"
        self.attempt = 0
        self.cancel_requested = False

    @workflow.query
    def status(self) -> dict:
        return {"phase": self.phase, "attempt": self.attempt}

    @workflow.signal
    def request_cancel(self) -> None:
        self.cancel_requested = True

    @workflow.run
    async def run(self, wf: TaskWorkflowInput) -> dict:
        wh = retry_policy_for("workhub")
        try:
            self.phase = "binding"
            b = await workflow.execute_activity_method(Activities.verify_binding, wf, task_queue=Q_ORCH,
                                                       start_to_close_timeout=timedelta(seconds=60), retry_policy=wh)
            notes, resume = "", None
            while self.attempt <= wf.max_rework:
                if self.cancel_requested:
                    return await self._block(wf, "E_CANCELLED", "")
                self.attempt += 1
                self.phase = "build"
                built = await workflow.execute_activity_method(
                    Activities.assign_task,
                    AssignInput(wf=wf, binding=b, attempt_no=self.attempt, rework_notes=notes,
                                resume_session_id=resume),
                    task_queue=Q_BUILD, start_to_close_timeout=timedelta(seconds=1800),
                    heartbeat_timeout=timedelta(seconds=90),
                    retry_policy=RetryPolicy(initial_interval=timedelta(seconds=10), backoff_coefficient=2.0,
                                             maximum_attempts=3, non_retryable_error_types=wh.non_retryable_error_types))
                self.phase = "test"
                tests = await workflow.execute_activity_method(Activities.run_tests, args=[wf, built],
                                                               task_queue=Q_TEST,
                                                               start_to_close_timeout=timedelta(seconds=1800),
                                                               retry_policy=retry_policy_for("test"))
                if not tests.passed:
                    notes = f"Tests failed:\n{tests.summary}"
                    resume = built.muse_session_id
                    continue
                self.phase = "verify"
                v = await workflow.execute_activity_method(Activities.verify, args=[wf, b, built, tests],
                                                           task_queue=Q_VERIFY,
                                                           start_to_close_timeout=timedelta(seconds=1200),
                                                           heartbeat_timeout=None,
                                                           retry_policy=retry_policy_for("verify"))
                if v.verdict == "approve":
                    self.phase = "close"
                    closed = await workflow.execute_activity_method(Activities.close_task,
                                                                    args=[wf, b, built.attempt_id], task_queue=Q_ORCH,
                                                                    start_to_close_timeout=timedelta(seconds=120),
                                                                    retry_policy=wh)
                    digest = await workflow.execute_activity_method(Activities.sign_receipt,
                                                                    args=[wf, built, tests, v], task_queue=Q_ORCH,
                                                                    start_to_close_timeout=timedelta(seconds=60),
                                                                    retry_policy=wh)
                    self.phase = "done"
                    return {"state": closed.final_state, "pr_url": built.pr_url, "receipt": digest,
                            "attempts": self.attempt}
                if v.verdict == "reject":
                    return await self._block(wf, "rejected_by_verifier", built.attempt_id)
                notes, resume = v.notes, built.muse_session_id
            return await self._block(wf, "rework_exhausted", f"attempt-{self.attempt}")
        except ActivityError as e:
            return await self._block(wf, _code(e), f"attempt-{self.attempt}")

    async def _block(self, wf: TaskWorkflowInput, reason: str, attempt_id: str) -> dict:
        self.phase = "blocked"
        wh = retry_policy_for("workhub")
        await workflow.execute_activity_method(Activities.mark_blocked, args=[wf, reason], task_queue=Q_ORCH,
                                               start_to_close_timeout=timedelta(seconds=120), retry_policy=wh)
        await workflow.execute_activity_method(Activities.escalate, args=[wf, reason, attempt_id], task_queue=Q_ORCH,
                                               start_to_close_timeout=timedelta(seconds=60), retry_policy=wh)
        return {"state": "blocked", "reason": reason, "attempts": self.attempt}


@workflow.defn(name="ScheduledRunWorkflow")
class ScheduledRunWorkflow:
    """Started by a Temporal Schedule. Fires one TaskWorkflow child per tick for an approved template revision.
    Child id is deterministic per (task, revision, tick) so overlapping ticks cannot double-run."""

    @workflow.run
    async def run(self, wf: TaskWorkflowInput) -> dict:
        tick = workflow.now().strftime("%Y%m%dT%H%M")
        child_id = workflow_id(wf.tenant_id, wf.task_id, f"{wf.contract_rev}@{tick}")
        result = await workflow.execute_child_workflow(TaskWorkflow.run, wf, id=child_id, task_queue=Q_ORCH,
                                                       id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE)
        return {"tick": tick, "child": child_id, "result": result}
```
## `rouvr/workers.py`
```python
"""rouvr/workers.py — `rouvr-worker --queue <name>`: one process per queue (doc: concurrency per queue)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from temporalio.client import Client
from temporalio.worker import Worker

from rouvr.activities import Activities
from rouvr.config import Settings
from rouvr.db import create_pool
from rouvr.host import build_host
from rouvr.workflows import Q_BUILD, Q_ORCH, Q_TEST, Q_VERIFY, ScheduledRunWorkflow, TaskWorkflow

CONCURRENCY = {Q_ORCH: 10, Q_BUILD: 2, Q_TEST: 4, Q_VERIFY: 4}


def activities_for(queue: str, a: Activities) -> list:
    return {Q_ORCH: [a.verify_binding, a.close_task, a.mark_blocked, a.escalate, a.sign_receipt],
            Q_BUILD: [a.assign_task], Q_TEST: [a.run_tests], Q_VERIFY: [a.verify]}[queue]


async def run(queue: str) -> None:
    s = Settings.from_env()
    pool = None
    try:
        pool = await create_pool(s.database_url)
    except Exception:  # noqa: BLE001 - phase 1 runs without Postgres
        logging.warning("no Rouvr DB; phase-2 features disabled")
    acts = Activities(build_host(s, pool))
    client = await Client.connect(s.temporal_address, namespace=s.temporal_namespace)
    conc = s.builder_concurrency if queue == Q_BUILD else CONCURRENCY[queue]
    worker = Worker(client, task_queue=queue,
                    workflows=[TaskWorkflow, ScheduledRunWorkflow] if queue == Q_ORCH else [],
                    activities=activities_for(queue, acts), max_concurrent_activities=conc,
                    graceful_shutdown_timeout=__import__("datetime").timedelta(seconds=60))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    async with worker:
        await stop.wait()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--queue", required=True, choices=list(CONCURRENCY))
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(p.parse_args().queue))
```
## `rouvr/starter.py`
```python
"""rouvr/starter.py — idempotent start used by the thin endpoint (rouvr/api.py, owner decision 2) and the
`rouvr start` operator CLI. `rouvr schedule` (recurring work) is OUT OF SCOPE (owner decision 1: coding only); kept, unused."""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta

from temporalio.client import (Client, Schedule, ScheduleActionStartWorkflow, ScheduleOverlapPolicy, SchedulePolicy,
                               ScheduleSpec)
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from rouvr.config import Settings
from rouvr.contracts import TaskWorkflowInput
from rouvr.host import build_host
from rouvr.ids import workflow_id
from rouvr.workflows import Q_ORCH, ScheduledRunWorkflow, TaskWorkflow


async def start_task_workflow(client: Client, wf: TaskWorkflowInput, *, report: bool = False):
    wid = workflow_id(wf.tenant_id, wf.task_id, wf.contract_rev)
    started = True
    try:
        await client.start_workflow(TaskWorkflow.run, wf, id=wid, task_queue=Q_ORCH,
                                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                                    execution_timeout=timedelta(hours=6))
    except WorkflowAlreadyStartedError:
        started = False  # same task+revision already running/finished: return the same id
    return (wid, started) if report else wid


async def create_schedule(client: Client, wf: TaskWorkflowInput, schedule_id: str, cron: str, tz: str) -> None:
    await client.create_schedule(schedule_id, Schedule(
        action=ScheduleActionStartWorkflow(ScheduledRunWorkflow.run, wf, id=f"rouvr-sched-{schedule_id}",
                                           task_queue=Q_ORCH),
        spec=ScheduleSpec(cron_expressions=[cron], time_zone_name=tz),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP)))


async def _main(a: argparse.Namespace) -> None:
    s = Settings.from_env()
    host = build_host(s)
    wh = host.workhub_for(None)
    task = await wh.task_by_number(a.task_number)
    wf = TaskWorkflowInput(tenant_id=s.tenant_id, project_id=s.project_id, task_id=str(task["id"]),
                           contract_rev=str(task["contract_rev"]), requested_by=a.requested_by,
                           max_rework=s.max_rework)
    client = await Client.connect(s.temporal_address, namespace=s.temporal_namespace)
    if a.cmd == "start":
        print(await start_task_workflow(client, wf))
    else:
        await create_schedule(client, wf, a.schedule_id, a.cron, a.tz)
        print(a.schedule_id)


def main() -> None:
    p = argparse.ArgumentParser(prog="rouvr")
    sub = p.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("start"); st.add_argument("--task-number", type=int, required=True)
    st.add_argument("--requested-by", required=True)
    sc = sub.add_parser("schedule"); sc.add_argument("--task-number", type=int, required=True)
    sc.add_argument("--requested-by", required=True); sc.add_argument("--schedule-id", required=True)
    sc.add_argument("--cron", required=True); sc.add_argument("--tz", default="Asia/Kolkata")
    asyncio.run(_main(p.parse_args()))
```
## `rouvr/api.py`
```python
"""rouvr/api.py — thin start endpoint (owner decision 2, 29 Oct 2026).

Rouvr starts a workflow by calling this endpoint. It does only admission: validate, auth, start the
TaskWorkflow with a deterministic id, return. No business logic, no outbox (outbox-driven start is later/optional).

Contract (proposal):
  POST /workflows/start
    Headers: Authorization: Bearer <token>   (token from START_API_TOKEN_FILE)
             Idempotency-Key: <opaque>       (optional; the workflow id is the real dedupe key)
    Body:    {"task_id": "123", "contract_rev": "r1", "requested_by": "ankur"}
    200:     {"workflow_id": "...", "started": true|false}   # false = duplicate, same id returned
    400:     {"error": "E_BAD_REQUEST", "message": ...}
    401:     {"error": "E_AUTHZ", "message": ...}
  GET /healthz -> 200 {"ok": true}
Run: uvicorn-compatible ASGI app, e.g. `uvicorn rouvr.api:app` (uvicorn not a dependency; any ASGI server).
"""
from __future__ import annotations

import hmac
import json
from typing import Any, Awaitable, Callable

from rouvr.contracts import TaskWorkflowInput

StartFn = Callable[[TaskWorkflowInput], Awaitable[tuple[str, bool]]]
REQUIRED = ("task_id", "contract_rev", "requested_by")


def build_app(*, start: StartFn, token: str, tenant_id: str, project_id: str, max_rework: int = 2):
    async def send_json(send, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": raw})

    async def app(scope: dict, receive, send) -> None:
        if scope["type"] != "http":
            return
        method, path = scope["method"], scope["path"]
        if method == "GET" and path == "/healthz":
            return await send_json(send, 200, {"ok": True})
        if not (method == "POST" and path == "/workflows/start"):
            return await send_json(send, 404, {"error": "E_BAD_REQUEST", "message": "not found"})
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        if not token or not hmac.compare_digest(auth, f"Bearer {token}"):
            return await send_json(send, 401, {"error": "E_AUTHZ", "message": "bad token"})
        body = b""
        while True:
            m = await receive()
            body += m.get("body", b"")
            if not m.get("more_body"):
                break
        try:
            data: Any = json.loads(body or b"{}")
            missing = [k for k in REQUIRED if not isinstance(data.get(k), str) or not data[k]]
        except (ValueError, AttributeError):
            missing = list(REQUIRED)
        if missing:
            return await send_json(send, 400, {"error": "E_BAD_REQUEST", "message": f"missing: {missing}"})
        wf = TaskWorkflowInput(tenant_id=tenant_id, project_id=project_id, task_id=data["task_id"],
                               contract_rev=data["contract_rev"], requested_by=data["requested_by"],
                               max_rework=max_rework)
        wid, started = await start(wf)
        return await send_json(send, 200, {"workflow_id": wid, "started": started})

    return app


def _make_default_app():  # pragma: no cover - wiring
    from temporalio.client import Client
    from rouvr.config import Settings
    from rouvr.starter import start_task_workflow
    s = Settings.from_env()
    holder: dict = {}

    async def start(wf: TaskWorkflowInput) -> tuple[str, bool]:
        if "c" not in holder:
            holder["c"] = await Client.connect(s.temporal_address, namespace=s.temporal_namespace)
        return await start_task_workflow(holder["c"], wf, report=True)

    return build_app(start=start, token=s.start_api_token_file.read_text().strip(),
                     tenant_id=s.tenant_id, project_id=s.project_id, max_rework=s.max_rework)


def __getattr__(name: str):  # lazy `rouvr.api:app` so imports in tests need no env
    if name == "app":
        return _make_default_app()
    raise AttributeError(name)
```
## `tests/test_unit.py`
```python
"""Unit tests: pure logic, no network."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from rouvr.activities import parse_verdict
from rouvr.contracts import Lease
from rouvr.errors import Code, RouvrError, from_http
from rouvr.ids import activity_id, handoff_fence, sanitize_for_log, workflow_id
from rouvr.lease import LeaseKeeper, run_fenced
from rouvr.personas import PersonaRegistry
from rouvr.receipts import Receipt, sign, verify
from rouvr.workhub.client import WorkHubClient


def test_ids_deterministic():
    a = workflow_id("t1", "task/9", "rev3")
    assert a == workflow_id("t1", "task/9", "rev3") and a.startswith("rouvr-task-t1-task_9-")
    assert len(a.rsplit("-", 1)[1]) == 16
    assert len(activity_id("assign", 1).rsplit("-", 1)[1]) == 20
    assert handoff_fence(2, 7) == "temporal-handoff-2-e7"


@pytest.mark.parametrize("status,body,code", [
    (429, {}, Code.THROTTLE), (503, {}, Code.DOWNSTREAM), (403, {}, Code.AUTHZ),
    (409, {"code": "RECORD_VERSION"}, Code.MOVED), (409, {"code": "CONTRACT.MOVED"}, Code.CONTRACT_MOVED),
    (422, {"code": "EVIDENCE.MISSING"}, Code.EVIDENCE), (400, {"code": "KEY.REQUIRED"}, Code.CONTRACT),
    (400, {}, Code.BAD_REQUEST)])
def test_error_mapping(status, body, code):
    e = from_http(status, body)
    assert e.code is code
    assert e.to_temporal().non_retryable == (code not in {Code.THROTTLE, Code.DOWNSTREAM, Code.TRANSPORT})


def test_sanitize():
    assert "abc123" not in sanitize_for_log("Authorization: Bearer abc123 token=zzz")


def test_personas_self_review(tmp_path: Path):
    reg = PersonaRegistry({"tenants": {"t": {"personas": {
        "b": {"role": "builder", "principal": "same", "muse_model": "muse-wonder-1.0", "credential_file": "/x"},
        "v": {"role": "verifier", "principal": "same", "muse_model": "muse-spark", "credential_file": "/y"}}}}})
    with pytest.raises(RouvrError) as ei:
        reg.pair("t", "b", "v")
    assert ei.value.code is Code.SELF_REVIEW
    with pytest.raises(RouvrError) as ei:
        reg.get("t", "b", "verifier")
    assert ei.value.code is Code.INELIGIBLE


def test_receipt_sign_verify():
    k = Ed25519PrivateKey.generate()
    r = Receipt("rouvr-receipt-v1", "t", "task", "rev", "att", "run", "sess", "https://pr", "sha", "tr",
                "approve", "svc-v", "2026-10-29T00:00:00Z")
    d, sig = sign(r, k)
    assert len(d) == 64 and verify(r, sig, k.public_key())
    assert not verify(Receipt(**{**r.__dict__, "verdict": "reject"}), sig, k.public_key())


def test_parse_verdict_fails_closed():
    assert parse_verdict("looks good\nVERDICT: approve") == "approve"
    assert parse_verdict("no verdict here") == "request_changes"


async def test_client_headers_and_exact_evidence():
    seen: list[httpx.Request] = []
    store: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "POST":
            store.append({"id": "ev1", **json.loads(req.content)})
            return httpx.Response(201, json={"id": "ev1"})
        return httpx.Response(200, json={"items": store})

    wh = WorkHubClient("http://wh", "t", "p", "tok", transport=httpx.MockTransport(handler))
    eid = await wh.attach_evidence_exact("task", "pr_record", "temporal-handoff-1-e1", {"url": "u"})
    assert eid == "ev1"
    post = seen[0]
    assert post.headers["Idempotency-Key"].startswith("rouvr-evidence-")
    assert post.headers["X-Hyeve-Tenant-Id"] == "t" and post.headers["X-Project-ID"] == "p"


async def test_lease_lost_aborts_work():
    class FakeWH:
        async def renew_lease(self, *a):
            raise RouvrError(Code.MOVED, "stale epoch")

    keeper = LeaseKeeper(FakeWH(), Lease("t", "h", 1, 1, "a", ""), "asg", ttl_s=3, every_s=0)  # type: ignore[arg-type]
    with pytest.raises(RouvrError) as ei:
        await run_fenced(keeper, asyncio.sleep(5))
    assert ei.value.code is Code.MOVED
```
## `tests/test_workflow.py`
```python
"""Integration test: real TaskWorkflow on Temporal's time-skipping test server with fake activities."""
import uuid

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from rouvr.contracts import (AssignInput, AssignResult, Binding, CloseResult, TaskWorkflowInput, TestResult,
                             VerifyResult)
from rouvr.workflows import Q_BUILD, Q_ORCH, Q_TEST, Q_VERIFY, TaskWorkflow

calls: list[str] = []
verdicts: list[str] = []


@activity.defn(name="verify_binding")
async def verify_binding(wf: TaskWorkflowInput) -> Binding:
    return Binding(wf.task_id, "b", "v", wf.contract_rev, 1)


@activity.defn(name="assign_task")
async def assign_task(i: AssignInput) -> AssignResult:
    calls.append(f"assign:{i.attempt_no}:{i.resume_session_id}")
    return AssignResult(f"att{i.attempt_no}", 1, f"sess{i.attempt_no}", f"rouvr/x/a{i.attempt_no}", "https://pr", "sha")


@activity.defn(name="run_tests")
async def run_tests(wf: TaskWorkflowInput, b: AssignResult) -> TestResult:
    return TestResult("tr", True, "ok", "ev")


@activity.defn(name="verify")
async def verify(wf, b, built, t) -> VerifyResult:
    return VerifyResult(verdicts.pop(0), "fix x", "svc-v", "ev")


@activity.defn(name="close_task")
async def close_task(wf, b, attempt_id) -> CloseResult:
    calls.append("close")
    return CloseResult("done", 2)


@activity.defn(name="mark_blocked")
async def mark_blocked(wf, reason) -> CloseResult:
    calls.append(f"blocked:{reason}")
    return CloseResult("blocked", 2)


@activity.defn(name="escalate")
async def escalate(wf, reason, attempt_id) -> bool:
    return True


@activity.defn(name="sign_receipt")
async def sign_receipt(wf, built, tests, v) -> str:
    return "d" * 64


async def _run(env: WorkflowEnvironment, max_rework: int = 2) -> dict:
    workers = [Worker(env.client, task_queue=Q_ORCH, workflows=[TaskWorkflow],
                      activities=[verify_binding, close_task, mark_blocked, escalate, sign_receipt]),
               Worker(env.client, task_queue=Q_BUILD, activities=[assign_task]),
               Worker(env.client, task_queue=Q_TEST, activities=[run_tests]),
               Worker(env.client, task_queue=Q_VERIFY, activities=[verify])]
    for w in workers:
        await w.__aenter__()
    try:
        return await env.client.execute_workflow(
            TaskWorkflow.run, TaskWorkflowInput("t", "p", "task1", "rev1", "dev", max_rework),
            id=str(uuid.uuid4()), task_queue=Q_ORCH)
    finally:
        for w in workers:
            await w.__aexit__(None, None, None)


@pytest.fixture
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as e:
        yield e


async def test_rework_then_approve(env):
    calls.clear(); verdicts[:] = ["request_changes", "approve"]
    r = await _run(env)
    assert r["state"] == "done" and r["attempts"] == 2
    assert calls == ["assign:1:None", "assign:2:sess1", "close"]


async def test_rework_exhausted_blocks(env):
    calls.clear(); verdicts[:] = ["request_changes"] * 3
    r = await _run(env, max_rework=1)
    assert r == {"state": "blocked", "reason": "rework_exhausted", "attempts": 2}
```
## `tests/test_api_personas.py`
```python
import json
from pathlib import Path

import pytest

from rouvr.api import build_app
from rouvr.errors import Code, RouvrError
from rouvr.personas import registry_from_aix_mapping


async def _call(app, method, path, body=None, token="t0k"):
    sent = []
    raw = json.dumps(body).encode() if body is not None else b""
    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    async def send(m):
        sent.append(m)
    hdrs = [(b"authorization", f"Bearer {token}".encode())] if token else []
    await app({"type": "http", "method": method, "path": path, "headers": hdrs}, receive, send)
    return sent[0]["status"], json.loads(sent[1]["body"])


async def test_thin_start_endpoint_idempotent():
    seen: dict[str, int] = {}
    async def start(wf):
        wid = f"rouvr-task-{wf.task_id}"
        seen[wid] = seen.get(wid, 0) + 1
        return wid, seen[wid] == 1
    app = build_app(start=start, token="t0k", tenant_id="t", project_id="p")
    body = {"task_id": "7", "contract_rev": "r1", "requested_by": "ankur"}
    assert await _call(app, "POST", "/workflows/start", body) == (200, {"workflow_id": "rouvr-task-7", "started": True})
    assert await _call(app, "POST", "/workflows/start", body) == (200, {"workflow_id": "rouvr-task-7", "started": False})
    assert (await _call(app, "POST", "/workflows/start", body, token="bad"))[0] == 401
    assert (await _call(app, "POST", "/workflows/start", {"task_id": "7"}))[0] == 400


def test_aix_mapping_registry():
    reg = registry_from_aix_mapping({"roles": {
        "builder": {"principal": "svc-b", "creds_file": "/x.json"},
        "reviewer": {"principal": "svc-r", "creds_file": "/y.json"},
        "closer": {"principal": "svc-c", "creds_file": "/z.json"}}}, "t")
    b, v = reg.pair("t", "builder", "reviewer")
    assert (b.role, v.role, b.muse_model, v.muse_model) == ("builder", "verifier", "muse-wonder-1.0", "muse-spark")
    with pytest.raises(RouvrError) as ei:
        registry_from_aix_mapping({"roles": {"builder": {"principal": "p"}}}, "t")
    assert ei.value.code is Code.CONTRACT
    same = registry_from_aix_mapping({"roles": {
        "builder": {"principal": "s", "creds_file": "/x"}, "reviewer": {"principal": "s", "creds_file": "/y"}}}, "t")
    with pytest.raises(RouvrError) as ei:
        same.pair("t", "builder", "reviewer")
    assert ei.value.code is Code.SELF_REVIEW
```
## Build order with acceptance checks (keyed to roadmap)

| # | Roadmap | Step | Files | Acceptance check |
|---|---|---|---|---|
| 1 | R1 | Hygiene + skeleton | pyproject.toml, .env.example, CI hygiene job | `pip install -e .[dev]`; hygiene rg returns 0; gitleaks 0 new |
| 2 | R2 | Muse resume spike | scripts/spike_resume.sh, docs/spikes/muse-resume.md | doc states RESUME or RESPAWN with transcript hashes; set RESUME_SUPPORTED accordingly |
| 3 | A1 | Confirm WorkHub routes | rouvr/workhub/routes.py | every UNVERIFIED path replaced by a curl-confirmed one; claim→attempt→renew→result 2xx on staging |
| 4 | R3 | Config, errors, ids, WorkHubPort + client | config.py, errors.py, ids.py, workhub/* | `pytest tests/test_unit.py` green; contract suite green on ledger and on staging when WORKHUB_URL set |
| 5 | R4 | Admission via thin endpoint (OD2) | api.py, starter.py, workflows.py | `pytest -k thin_start` green; second POST /workflows/start returns started:false + same id; Temporal shows 1 run |
| 6 | R5 | Lease/fencing + reclaim | lease.py, activities.assign_task | `-k lease` green; kill worker → renewals stop → retry reclaims on same attempt |
| 7 | R6 | Personas from AIX mapping (OD3), exec spec, Muse launcher, gitops | personas.py (load_aix_mapping; format ASSUMED, confirm vs AIX), execspec.py, muse.py, gitops.py | one PR; binding row has session_id+run_id+pr_url; forced rerun opens 0 PRs |
| 8 | R7 | submit_result gate | (new) rouvr/gate.py — not in scaffold yet | exit without submit → failed; 3 rejects → blocked; fails closed on read error |
| 9 | R8a/R8 | Hidden tests + independent runner + cheat-scan | testrunner.py (+ cheat-scan, not in scaffold yet) | stub PR rejected, real PR passes; evidence author = runner principal |
| 10 | R9 | Verifier + close | activities.verify/close_task, workflows.py | `pytest tests/test_workflow.py` green; verifier≠builder enforced at claim; readback=done |
| 11 | R10 | Kill/resume rehearsal | workers.py, docker-compose.yml | script exits 0; retry on same attempt; 1 PR; task closed |
| 12 | P2 | Migrations, outbox (optional starter), escalate_once, receipts; schedules OUT OF SCOPE (OD1) | migrations/, db.py, outbox.py, escalation.py, receipts.py | migration applies twice; 2 concurrent escalations → 1 row; receipt verifies; SKIP overlap one child per tick |
