# Knowledge Lite functional cold-follow

Date (UTC): 2026-11-02. Scope: read-only audit; no source, test, or config
file was modified. Only this report was written.

## Method

Throwaway stack under `/tmp` only (never `.data/`):

- Driver: `/tmp/klaudit/run.py` (uncommitted scratch, kept for re-run).
- Ledger: `ledger/app.py:132` `make_app("sqlite:////tmp/klaudit/audit.db")`
  on `127.0.0.1:18311`, `ROUVR_DATA_DIR=/tmp/klaudit/data`,
  `ROUVR_LEDGER_TOKEN=stack-secret`.
- Script: [demo rehearsal knowledge_demo](./demo/rehearsal/knowledge_demo.py),
  [onboard_demo](./demo/rehearsal/onboard_demo.py),
  [test_session_commands](./tests/test_session_commands.py),
  [test_workloop_knowledge](./tests/test_workloop_knowledge.py).
- Repo oracles re-run: `.venv/bin/python -m pytest
  tests/test_session_commands.py tests/test_workloop_knowledge.py -q`
  → **3 passed**.
- Pre-existing dirty tree (`git status` shows `M ledger/app.py`,
  `M rouvr/mcp_server.py`, etc.) was left alone; all test/runtime state
  landed in `/tmp/klaudit/` (`audit.db`, `data/knowledge-snapshots/`,
  `hidden/`). `.data/` was never written by this audit.

## Per-step outcome

| Step | Outcome | Exact call |
|---|---|---|
| onboard register | BLOCKED | `POST /v1/onboard/register` (per [onboard_demo](./demo/rehearsal/onboard_demo.py:50)) → `503 ADMIN_NOT_CONFIGURED:set HYEVE_ADMIN_USERNAME/_PASSWORD` |
| onboard complete | BLOCKED | no JWT (register blocked); gate itself verified: unauthenticated complete requires a Hyeve token ([app](./ledger/app.py:484)) |
| direct user setup (offline path) | PASS | `POST /v1/tenants` + `POST /v1/projects` + `POST /v1/users` (as in [test_session_commands](./tests/test_session_commands.py:15)) → tenant `acme`, project `site`, user `amy` |
| knowledge store (single) | PASS | `POST /v1/knowledge` ([app](./ledger/app.py:646)) → `knowledge:1@v1` decision (verified=0), `knowledge:2@v1` rule |
| knowledge search | PASS | `GET /v1/knowledge/search?q=<canary>` ([app](./ledger/app.py:670)) → both ids hit |
| knowledge get | PASS | `GET /v1/knowledge/1` ([app](./ledger/app.py:707)) → citation round-trips |
| knowledge version + history | PASS | `POST /v1/knowledge/1/versions` ([app](./ledger/app.py:734)) → `knowledge:3@v2`; `GET /v1/knowledge/3/history` ([app](./ledger/app.py:712)) → `[v1, v2]`; canary search returns only v2 (latest-only, `_LATEST` at [app](./ledger/app.py:573)) |
| knowledge confirm | PASS | `POST /v1/knowledge/{3,2}/confirm` ([app](./ledger/app.py:760)) → verified=1 on both |
| verified-rules bundle | PASS | `GET /v1/knowledge-rules?project=site` ([app](./ledger/app.py:877)) → citations `['knowledge:2@v1']` |
| session store (batch) | PASS | `POST /v1/knowledge/store` ([app](./ledger/app.py:768)) → 2 items, then confirmed |
| session compact | PASS | `POST /v1/knowledge/compact` ([app](./ledger/app.py:797)) → `snapshot knowledge:6@v1`, 4 `knowledge:` sources, body cites them |
| session snapshot | PASS | `POST /v1/knowledge/snapshot` ([app](./ledger/app.py:825)) → `count=4`, `.json` path under `/tmp/klaudit/data/knowledge-snapshots/`, file exists |
| store-and-close | PASS | `POST /v1/knowledge/store-and-close` ([app](./ledger/app.py:851)) → 1 stored + compact + snapshot |
| curate | PASS | `POST /v1/knowledge/curate` ([app](./ledger/app.py:905)) → duplicate suggestion; before/after search counts equal (no delete), all unverified (no verify) |
| work-loop cite → bundle → reviewer → summary | PASS | `ms.workhub_create_task` ([mcp_server](./rouvr/mcp_server.py:244)) carries `knowledge:2@v1`; `knowledge_context_bundle` ([mcp_server](./rouvr/mcp_server.py:287)) lists it; reviewer_report evidence posted; `workhub_show_task` summary ([mcp_server](./rouvr/mcp_server.py:394)) `knowledge_citations` contains it. Decompose/hidden-suite were test doubles per [test_workloop_knowledge](./tests/test_workloop_knowledge.py:41) (no live model in this loop) |

## Breakage found

None in product code. All green: 15 PASS + 2 BLOCKED (both BLOCKEDs are
the expected upstream dependency — no person service configured — and the
supported offline path covers the same ground).

Methodology notes (driver bugs, not product bugs, fixed and re-run green):

- History must be queried on the latest id: `GET /history` on a superseded
  v1 correctly returns only `[v1]` (append-only walk follows
  `supersedes_id` backwards). First driver run queried the old id.
- `rouvr/mcp_server.py:19` pins `LEDGER` at import time, so the work-loop leg
  must set `WORKHUB_BASE_URL` before import (or repoint `ms.LEDGER`). The
  first run hit the host's live `127.0.0.1:8221` and got `401` (wrong token,
  no state change). Corrected to the throwaway server; passes.
