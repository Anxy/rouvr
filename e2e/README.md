# e2e — web-UI regression runs (stubbed model + scripted worker)

These runs are **UI regression tests, not demo footage**. The model is the
deterministic ledger stub (plan-shaped messages pass through; anything else
echoes) and the worker steps (claim → attempt → pass verdict) are scripted API
calls. They prove the workspace path — login, chat streaming, task cards,
board, live events, approve/retry — without spending model calls or depending
on model behavior.

- `run.mjs <1|2|3>` — Playwright (Chromium headless) drives the real UI and
  records video to `recordings/`:
  - run 1: happy path — chat slice, scripted verify, UI approve → done.
  - run 2: failure + recovery — UI approve before any verify hits the 409
    guard on camera, then the verdict lands and approve succeeds.
  - run 3: restraint — a clarifying-question turn creates nothing, then a plan
    turn creates a slice.
- `runN.json` — timing + receipt ids per run (authoritative ids re-checked
  against the ledger; the in-script snapshot can race the done receipt).

Run them with the smoke stack up (`scripts/smoke.sh start-ledger …` then
`start-web …`) and `E2E_BASE/E2E_LEDGER/E2E_TOKEN/E2E_ADMIN_PW/E2E_OUT` set.
Videos are local artifacts (not committed).

## Genuine demo runs (live model + headless coder)

- `demo_chat.mjs [tag] [message]` — one live-model chat turn in the UI,
  recorded to `$E2E_OUT/<tag>.webm`. Prints plan slice id + receipts.
- `demo_approve.mjs` / `demo_approve2.mjs` — owner approves `E2E_TIDS` in the
  UI (`Approve → done` under the 409 guard), recorded; writes
  `demo-approveN.json` with states + transition receipts.
- `demo2_accept.py` — hidden acceptance for the build-field slice, written
  before the coder ran (both prefixes, build == HEAD sha or `dev`,
  snapshot regen). `demo2_probe_nosubprocess.py` — reviewer probe: no
  per-request git subprocess (caught a real miss; fixed, now green).
- `demo_*_brief.md` — the exact coder briefs used (prompt-file for
  `muse exec`).
- `recordings/demo-chat1.webm`, `demo-approve1.webm` (slice: version
  endpoint) and `demo-chat2.webm`, `demo-approve2.webm` (slice: build
  field incl. fail → fix → pass) are the demo footage (local, not
  committed).
