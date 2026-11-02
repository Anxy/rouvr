# MEMORY.md — handoff (2026-11-02, rouvr-development @ 02c285b, tree clean)

## 1. Current state
- Branch `rouvr-development`, pushed through `02c285b`, tree clean. Commits as Anxy, no force.
- Stack HEALTHY: Temporal (docker :7233) + ledger (:8221, new code: slug/search rewrite) + MCP (:8231) + 4/4 workers. Ledger holds **117 tasks** (51 legacy + 66 backfill, all done) and **1080 knowledge entries** (166 corpus + 914 backfill).
- **State lives in `<repo>/.data/`** (git-ignored): `ledger.db`, `backups/`, `workspaces/`, `specs/`, `evidence/`, `hidden-tests/`, `grants/`, `keys/`, `logs/`, `pids/`, `api-token`, `env.sh`. /tmp is NEVER state.
- Operate: `./scripts/rouvr --no-chat` (up; adopts live workers, restarts only what's down), `./scripts/rouvr status`, `./scripts/rouvr stop` (pidfile-scoped). Token: `cat .data/api-token`. Kill ledger only via its pidfile (`cat .data/pids/ledger.pid`, verify cwd+cmdline first).
- Suites: public cut green (**508 passed, 6 skipped**): `make test-public` (`.venv/bin/pytest -q ledger demo lane/tests workers cli tests core/backend/tests/test_rouvr_model_providers.py`; .venv is 3.12, 3.14 breaks tiktoken).

## 2. Canonical roadmap + status
- DONE: Knowledge Lite hardening + fuzzy search (porter FTS + trigram + RapidFuzz WRatio + RRF, shortlist, slug/tags/summary, verified-rank boost, D18 stub honest) + verify-list D1–D20 + rule 5; backfills (tasks 22 sheets→66; knowledge 50 files→914, all unverified `backfill-*`, curator 27 suggestions, nothing applied); 22 open tasks triaged (17 SATISFIED by scouts + S017 inline) and closed with live evidence; frontend-lite plan + 20 FL tasks + RUNBOOK paragraph.
- NEXT (in WorkHub, project `frontend-lite`, all open): slices A→F per `docs/plans/2026-11-02-frontend-lite.md` (A5/A6 first, C2 first demoable). Roadmap stored as knowledge:1186@v1. No building started — awaiting `go`.
- AWAITING OWNER: hyeve native-login shape + JWKS reachability (plan risk #1, needed before Slice B only). No Google/social (dropped), no registration in demo (IdP/DNS deferred, person provisioning skipped).
- FROZEN: request_changes hunt; rename/off-path drift; no proof rounds unless asked.

## 3. Key findings
- **Rework converges**: reviewer `request_changes` + feedback → evidence → `rework_notes` into coder's next prompt (`workflows.py:60-67`, `activities.py:156`, `muse.py:401-402`). No `changes_required` state exists (S018); board derives a badge from `review.note` events; supervisor reject = review-note evidence + `needs_review→claimed` (`done` is terminal).
- **Receipts are HMAC** (`rouvr/receipts.py`, server key): Verify button must call `POST /receipts/verify` server-side; in-browser WebCrypto impossible without leaking the key (asymmetric signing = LATER).
- **Board vocabulary**: ledger states only (open/claimed/needs_review/done/blocked/failed); lane "changes required/running" have no ledger state.
- **Knowledge ids**: 165/171–174 superseded, 267–271 current v2 verified; `_LATEST` filter excludes old ids (A6 regression test planned).
- **Backfill dedupe**: tasks match on `[SNNN X.Y]` title; knowledge on deterministic `backfill-*` slug + corpus-title guard; both re-runs create 0. History rows carry honesty markers, never presented as live runs.
- **Slow models**: `test_author` needs >60s under load (`timeout=300`). Transient Meta read timeouts fail closed.

## 4. Quirks / gotchas
- Shell is `sh`: use `bash -c` for process substitution; worker env via `set -a; source .data/env.sh; set +a`.
- SDK probes: `PYTHONPATH=<repo>`, Temporal `127.0.0.1:7233` ns `default`; ledger + `X-Rouvr-Token: $(cat .data/api-token)`.
- `GET /v1/tasks/N/evidence` is 405 (evidence embedded in task GET). Task search/list has no endpoint — list with `?limit=1000`.
- `test_ops.py` fails standalone (401s): it relies on `ledger/test_ledger.py` setting `ROUVR_INSECURE_DEV=1` process-wide earlier in suite order. Pre-existing; gate is `make test-public` in full order.
- Temporal matching once stalled 33 min (wedged partition, `docker restart rouvr-temporal-1`; never restart db). Duplicate worker sets poison runs (reap, keep one set).
- `scripts/rouvr stop` is pidfile-scoped (never pattern-kill). Restart workers after code/env changes. `status` "3/4 queues" is a naming quirk — ignore.
- Never print secrets (probes = HTTP status only); secret scans report paths/types only.
- RULE: same goal failing 3 rounds without a new product finding → stop and report.
