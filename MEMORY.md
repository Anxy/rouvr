# MEMORY.md — handoff (updated 2026-11-03 ~15:00 UTC; Steps 1–7 + demos + wrap-up DONE)

## 0. Right now (read first)
- Branch `rouvr-development` @ `19228d3`, tree CLEAN, all pushed as Anxy.
  `main` untouched — merges to base remain owner-only.
- DONE: Steps 1–7, Demo 0 (stub runs labeled regression tests, empty-stream
  diagnosis), Demo 1 (genuine version-endpoint slice), Demo 2 (genuine
  build-field slice incl. real fail → fix → pass), wrap-up (chat contracts
  fix, footage cleanup, demo branches merged, VIDEO-SCRIPT).
- Demo branches merged: `demo/version-endpoint` (`1265a65`),
  `demo/version-build` (`b5aea9c`). Worktrees still on disk at
  `rouvr-demo-wt`, `rouvr-demo2-wt` (branches on origin; safe to remove).
- Smoke demo stack (ledger :18234, web :13002, demo DB) left RUNNING.
  Supervisor ledger :8221 must stay untouched. Full suite 594 passed,
  7 skipped; hygiene clean.
- Workhub MCP tools were erroring (see §3). Session todos all complete.

## 1. Durable lessons (new)
- **Chat must attach contracts to every child task** (`ledger/app.py` kids
  loop; test `test_chat_plan_attaches_contract_to_every_child_task`). The
  approve guard needs a contract per task — the supervisor should never
  hand-attach them after the fact (that gap is what Demo 1 hit).
- **Hygiene scans `/home/anxy`**: use `$HOME`/expanduser in e2e harness
  files; footage stays untracked (`e2e/recordings/*.webm` in .gitignore,
  local only). Never `git add -A` a dir with recordings.
- **Empty streams**: reasoning model burns streaming budget thinking →
  zero content deltas. Chat uses non-streaming + one real token event
  (see `e2e/diag_empty.py`, app.py `_events` comment).
- **Reviewer honesty**: demo reviewers were Muse Code subagents, NOT Rouvr
  reviewer personas; probe author was the orchestrator. `demo/VIDEO-SCRIPT.md`
  captions say exactly this — keep them honest.
- **Workhub MCP auth**: plugin server gets `ROUVR_LEDGER_TOKEN` ONLY from
  `/tmp/rouvr-supervisor/env.sh` at (re)start. If that file is wiped, all
  `workhub_*` tools 401. Fix = restore token + restart the run.sh loop
  (kill loop PID then python child, relaunch via run.sh with token in env —
  never write the secret to a new file, never print it).

## 2. Evergreen gotchas (kept)
- /tmp is hostile (shared tmpfs): never stage anything big there; workers
  run `TMPDIR=.data/tmp`. Check `/proc/<pid>/environ` before assuming env.
- `make test-public` in full order is the gate (594 passed). `.venv` is
  3.12 (3.14 breaks tiktoken).
- Transitions: `open→claimed→needs_review→done`; `blocked→open→…`. Fresh
  `idempotency_key` per transition; `replayed=True` = key reuse.
- 3-round rule: same goal failing 3 rounds with no new finding → stop, report.
- Never print secrets (keys/tokens): HTTP status + last-4 only. Never
  rewrite history; never touch `main`; merges to base are owner's.
- `pkill` self-matches — use pidfiles (`scripts/smoke.sh` pattern).
- Nothing outside the rouvr repo gets deleted without explicit OK; even
  inside, only own session junk without asking.

## 3. Known open items
- Workhub tasks 138–144 VERIFIED done via direct 8221 API (2026-11-03
  ~15:00 UTC). The `workhub_*` MCP tools are DEAD for the rest of this
  session: the plugin server had lost ROUVR_LEDGER_TOKEN (env.sh wiped),
  and restarting it broke the runtime's stdio pipe (no on-demand respawn
  — two broken-pipe retries). `/tmp/rouvr-supervisor/env.sh` (600) is
  restored, so a fresh session will resurrect the tools. NEVER restart
  that server mid-session again — verify-then-report instead.
- Demo footage (`e2e/recordings/demo-*.webm`, ~4 MB) is local-only; back it
  up before any disk wipe. Receipt sidecars `demo-approve*.json` understate
  (in-script race) — authoritative ids are in `demo/VIDEO-SCRIPT.md`.
