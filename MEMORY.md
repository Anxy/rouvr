# MEMORY.md — handoff (fresh instance resumes here; updated 2026-11-03 ~15:30 UTC)

## 0. Right now (read first)
- Branch `rouvr-development` @ `601c472`, tree CLEAN, all pushed as Anxy.
  `main` untouched — merges to base are owner-only.
- Steps 1–7 DONE, Demo 0/1/2 DONE, wrap-up DONE (contracts fix, footage
  cleanup, demo branches merged, video script, knowledge updated).
- Demo branches merged: `demo/version-endpoint` (`1265a65`),
  `demo/version-build` (`b5aea9c`). No worktrees remain. Smoke demo stack
  (:18234 ledger, :13002 web) left RUNNING; supervisor ledger :8221 untouched.
- Full suite 594 passed / 7 skipped; hygiene clean. Footage local-only:
  `e2e/recordings/demo-*.webm` (gitignored, ~4 MB — back up before disk wipes).
- **Workhub/knowledge MCP tools are DEAD this session** (broken stdio pipe,
  no respawn). `/tmp/rouvr-supervisor/env.sh` (600) restored — a fresh
  session resurrects them. Until then use direct 8221 HTTP with the
  repo-local stack token (verified working).

## 1. Key findings
- **Chat must attach acceptance contracts to every child task**
  (`ledger/app.py` kids loop; test
  `test_chat_plan_attaches_contract_to_every_child_task`). Approve guard
  needs per-task contract + evidence + pass verdict; fixed after Demo 1 hit
  the 409. Never hand-attach contracts post-hoc again.
- **Empty streams fixed**: reasoning model burns streaming budget thinking
  → zero content deltas. Chat uses non-streaming + retry×2 + one real
  `token` event (`e2e/diag_empty.py` proof; see app.py `_events` comment).
- **Demo provenance (keep honest)**: headless `muse exec` coders ran on the
  Muse Code subscription; reviewers were Muse Code subagents, NOT Rouvr
  reviewer personas; hidden acceptance/probe written blind before coder ran.
  `demo/VIDEO-SCRIPT.md` captions say exactly this. Receipts: tasks 2,3,4 →
  17,18,19; tasks 6,7,8 → 39,40,41 (all `web-`-keyed).
- **Demo 2 genuine fail→fix**: coder v1 spawned `git rev-parse` per request;
  reviewer probe caught it, fix moved BUILD to import time + regression test.
- **MCP transport fragility**: plugin server reads `ROUVR_LEDGER_TOKEN`
  only from `/tmp/rouvr-supervisor/env.sh` at start; wipe = all `workhub_*`
  + `knowledge_*` tools 401. NEVER restart it mid-session — runtime holds
  the stdio pipe and won't respawn (3× broken pipe). Repair = restore env.sh,
  fresh session brings tools back.
- **Knowledge Lite updated 2026-11-03**: entries 1187–1193 under
  `supervisor/liveproof` (contracts rule, footage/hygiene rule,
  non-streaming decision, demo provenance, MCP fragility rule, demo-done
  note, API-key bot proof). Searchable via direct API; MCP search is down
  with the pipe.
- **API-key bot path proven**: headless turn via
  `core/backend/rouvr_models/providers.py` `get_provider("meta")` with key
  only from env file returned exactly `PONG`. Demo coders used subscription
  instead; full worker-loop-off-key run NOT yet exercised.
- **Hygiene**: no `/home/anxy` in tracked files (use `$HOME`/expanduser);
  never `git add -A` dirs with recordings. `.venv` is 3.12.

## 2. Canonical roadmap / status
- DONE, all pushed: Steps 1–7 (workhub tasks 138–144, all `done` —
  live-verified via direct 8221 API, receipts 1752–1764 on file).
- DONE: e2e regression runs (stub, labeled in `e2e/README.md`), Demo 1
  (version endpoint), Demo 2 (build field + real recovery), wrap-up
  (6 items), `demo/VIDEO-SCRIPT.md` (3-min beats, verified receipt ids).
- OPEN, needs owner decisions: (a) **knowledge organizer bot** — pieces
  exist (`knowledge_curate/compact/store/confirm`, `morning_report.py`,
  `rouvr_schedule_ops`) but no runner, no cron, no cadence/scope/verify
  policy; awaiting: cadence, tenant/project scope, verify policy.
  (b) worker-loop-off-API-key end-to-end run. (c) merges to `main` (owner).
- FROZEN: request_changes hunts, renames/off-path drift, proof rounds
  unless asked.

## 3. Quirks / gotchas
- /tmp is hostile (shared tmpfs): never stage anything big there; workers
  use `TMPDIR=.data/tmp`. Anything the stack needs at restart must NOT live
  in /tmp (lesson paid for with the MCP outage).
- `make test-public` in full order is the gate. `pkill` self-matches — use
  pidfiles (`scripts/smoke.sh`). Never print secrets (status + last-4 only).
- Transitions: `open→claimed→needs_review→done`; `blocked→open→…`. Fresh
  `idempotency_key` per transition; `replayed=True` = key reuse.
- 3-round rule: same goal failing 3 rounds with no new finding → stop, report.
- Never rewrite history; never touch `main`; merges to base are owner's.
- Nothing outside the rouvr repo gets deleted without explicit OK; even
  inside, only own session scratch without asking.
- `workhub_*`/`knowledge_*` MCP tools: dead until session restart (see §0).
  Workaround that works now: direct `curl` to 8221 with repo-local token.
- Billing: no spend meter — wall-clock only for model costs.
