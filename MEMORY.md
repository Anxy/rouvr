# MEMORY.md — handoff (2026-11-01, rouvr-development @ 52de485, tree clean)

## 1. Current state
- Branch `rouvr-development`, pushed through `52de485`, tree clean. `gh auth` = Anxy.
- Stack HEALTHY on repo-local state: Temporal (docker, :7233) + ledger (:8221) + MCP (:8231) + 4/4 workers. Ledger holds 42 tasks (21-22, 30-31, 34, 38-41 done; 23/32/33 blocked on harness faults since fixed; 42 blocked superseded).
- **State lives in `<repo>/.data/`** (0700, secrets 0600, git-ignored): `ledger.db`, `workspaces/`, `specs/`, `evidence/`, `hidden-tests/`, `grants/`, `keys/`, `logs/`, `pids/`, `api-token`, `env.sh`. All launchers default `$ROOT/.data` unless `ROUVR_OPS_DIR` set. /tmp is NEVER state (quota incident — see below).
- Operate: `./scripts/rouvr --no-chat` (up), `./scripts/rouvr status`, `./scripts/rouvr stop` (pidfile-scoped). Token: `cat .data/api-token`. Liveproof: `.venv/bin/python demo/rehearsal/live_coding.py create|start|watch|judge|usage`. Ops legs: `demo/rehearsal/run_tick.py [t1|t2|t3|all]` (only with no coding task active).
- Suites: public cut green (452 passed, 5 skipped): `.venv/bin/pytest -q ledger demo lane/tests workers cli tests core/backend/tests/test_rouvr_model_providers.py` with `PY=python3.12` (.venv is 3.12; 3.14 breaks tiktoken). Full-tree `core/backend` failures are pre-existing env issues (identical on clean base). Flake `test_scheduled_duplicate_tick_deduped` stays skipped (`docs/test-quarantine.md`).

## 2. Roadmap status
- DONE (committed): rev3 audit fixes; tasks 21-23, 30-34, 38-42 rounds with summaries in `demo/rehearsal/2026-11-01/liveproof/`; t2 profile 486s→29s (20s heartbeat on ops activities); scanner/leak/naming fixes + regression tests; author timeout 300s; storage migration; RUNBOOK (CI scope, timings, data location).
- NEXT (awaiting Ankur): frontend + API brief, Knowledge Lite planning.
- PLANNING (2026-11-01, WIP, not approved for build): roadmap slices stored in `docs/plans/` — Slice 1 Identity & Tenancy + Projects (D1–D8, sprints S024–S030), Slice 2 Knowledge Lite (D9–D20, S031–S038, R1 owner decision pending at S031 gate), Slice 3 Task Creation Methodology Lite (B1–B5, S039–S046; key finding: S021 decomposer skipped 21/21 live tasks — string-form verifiers refused, best-effort catch; benchmark corpus + revision-unit confirmation owed by owner).
- CLOSED/FROZEN: request_changes hunt (see findings); no more proof rounds unless Ankur asks; rename/off-path drift.

## 3. Key findings
- **Rework converges live (×2)**: tasks 34, 39 — test-gated rework → approve + signed receipts (PRs #24/#28). Six first-pass approves (22/30/31/38/40/41). Reviewer-`request_changes` verdict unobserved in 9 rounds (coder meets fair criteria first-pass; gate levers need anomalies that never occur; emission path unit-covered in `tests/test_r9_close.py`). Demo talking point, not a gap — see RUNBOOK.
- **Harness bugs found by live rounds, all fixed**: (1) LITERAL flag vs criteria-mandated literals → `allowed_literals` allowlist from spec text + test_author behavior-only guard; (2) leak-check name-only FP (task 32) → same-name needs identical bytes; (3) `hidden_*.py` breaks pytest collection → reserved `test_rouvr_hidden_*.py` + fail-closed validation (note: shared function-topic prefixes collapse outcome buckets to `hidden=1/1` — verdicts stay sound, steer distinct topics).
- **Slow models**: `test_author` needs >60s under load → `timeout=300` in `_author_hidden_suite` (3 straight timeouts before; 105–186s generations succeed). Transient Meta read timeouts fail closed; probe (HTTP status only) + retry works.
- **Storage**: /tmp quota (others' 13G scratch, left alone) made ledger sqlite unwritable (HTTP 500s). Migrated via sqlite `.backup` (integrity ok, 12 tables identical) + tree copy (450M); old root deleted post-verify. Task-42 `blocked` mark applied post-migration (receipt 335).
- Ops legs final: t1 5s, t2 29s, t3 10s, LEGS GREEN; judge good-pass/wrong-fail. Judge re-verifies ledger figures live (model can reject wrong figures).

## 4. Quirks / gotchas
- Shell is `sh`: use `bash -c` for process substitution; worker env via `set -a; source .data/env.sh; set +a`.
- SDK probes: `PYTHONPATH=/home/anxy/dev/rouvr`, Temporal `127.0.0.1:7233` ns `default`; ledger + `X-Rouvr-Token: $(cat .data/api-token)`.
- `live_coding.py` STATE (`liveproof/state.json`) is overwritten per `create` (backups `stateNN.json` kept). `GET /v1/tasks/N/evidence` is 405 (evidence embedded in task GET).
- Temporal matching once stalled 33 min (wedged partition, fixed by `docker restart rouvr-temporal-1`; never restart `rouvr-db-1`). `DescribeTaskQueue` lies; duplicate worker sets poison runs (reap, keep one set).
- `scripts/rouvr stop` is pidfile-scoped only (never pattern-kill). Restart workers after code/env changes (they load at startup). `status` "3/4 queues" is a naming quirk — ignore.
- Grants `<ws-root>/../grants` (0600), revoked at close; empty dir is steady state. `gh auth token` rewrites `.data/gh-token` on every `up` (same value).
- Never print META_API_KEY (probes = HTTP status only); no secret values in repo/report. Secrets scan = file names + match types only.
- RULE: same goal failing 3 rounds without a new product finding → stop and report, don't start another round.
