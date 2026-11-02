# Knowledge Lite v2 functional audit (cold-follow, read-only)

- HEAD: `bc735abda101cb52d9a52b92619d6582228744` ("Knowledge Lite last mile part 1")
- Date (UTC): 2026-11-02
- Method: read-only cold-follow against current HEAD. No source, test, or config file was modified (`git status` clean apart from pre-existing untracked `demo/rehearsal/2026-11-02/`).
- Throwaway state only: all probe DBs, snapshots, and `ROUVR_DATA_DIR` lived under `/tmp/kl-audit-*`. `.data/` was never touched.
- Probe script (not committed, kept out of the repo): `/tmp/kl_audit_probe.py` — FastAPI `TestClient` over `ledger.app:make_app("sqlite:///<tmp>/probe.db")`.
- Repo test re-runs: read-only `pytest` invocations; the suites use `tmp_path` fixtures, so no repo state was written.

## Verdict: all-green, no breakage found

34/34 throwaway-probe checks PASS; 14/14 runnable repo tests PASS; 1 test SKIPPED-by-design (needs live upstream creds, see step 11).

## Per-step results

| # | Step | Outcome | Evidence |
|---|------|---------|----------|
| 1 | User setup (tenants, projects, users) | PASS | Probe: `POST /v1/tenants`, `POST /v1/projects`, `POST /v1/users` (sha256 token_hash) for `acme/amy` + `globex/gus`, all HTTP 200. Impl: [ledger/app.py](./ledger/app.py). Repo contract: [tests/test_knowledge_lite.py:12-25](./tests/test_knowledge_lite.py) |
| 2 | Knowledge store + citation shape | PASS | Probe: `POST /v1/knowledge` decision → `id=1`, `version=1`, `verified=0`, `author=amy`, `citation=knowledge:1@v1`. `user_profile` and `project_profile` kinds also accepted. Impl: [ledger/app.py:646-668](./ledger/app.py). Repo: [tests/test_knowledge_lite.py:35-44](./tests/test_knowledge_lite.py) |
| 3 | Search (own tenant) | PASS | Probe: `GET /v1/knowledge/search?q=SQLite` → `[1]`. Impl FTS5 + latest-only filter: [ledger/app.py:670-705](./ledger/app.py) |
| 4 | Get by id | PASS | Probe: `GET /v1/knowledge/1` → 200, body starts `We chose`. Impl: [ledger/app.py:707-710](./ledger/app.py) |
| 5 | Version (append-only, old kept, search latest-only) | PASS | Probe: `POST /v1/knowledge/1/versions` → `id=4`, `version=2`, `supersedes_id=1`; old body intact; `q=FTS5` → `[4]` only. Impl: [ledger/app.py:734-765](./ledger/app.py). Repo: [tests/test_knowledge_lite.py:48-59](./tests/test_knowledge_lite.py) |
| 6 | History chain | PASS | Probe: `GET /v1/knowledge/4/history` → `[(1,1),(4,2)]`. Impl: [ledger/app.py:712-732](./ledger/app.py). Repo: [tests/test_knowledge_lite.py:62-69](./tests/test_knowledge_lite.py) |
| 7 | Confirm (verified flag) | PASS | Probe: rule created `verified=0`, `POST /v1/knowledge/5/confirm` → `verified=1`. Creator-only rule enforced in impl: [ledger/app.py:767-775](./ledger/app.py). Repo: [tests/test_knowledge_lite.py:72-77](./tests/test_knowledge_lite.py) |
| 8 | Verified-rules bundle | PASS | Probe: `GET /v1/knowledge-rules` → `citations=['knowledge:5@v1']`; `?project=site` filter also 200 and contains the citation. Impl: [ledger/app.py:900-926](./ledger/app.py). Work-loop consumer: `knowledge_context_bundle` in [rouvr/mcp_server.py:292](./rouvr/mcp_server.py) |
| 9 | Batch store | PASS | Probe: `POST /v1/knowledge/store` with 2 items → 2 rows. Impl: [ledger/app.py:778-805](./ledger/app.py). Repo: [tests/test_session_commands.py:27-54](./tests/test_session_commands.py) |
| 10 | Compact | PASS | Probe: `POST /v1/knowledge/compact` → `snapshot.kind=snapshot`, `sources=['knowledge:5@v1','knowledge:6@v1','knowledge:7@v1']` (verified-only). Impl: [ledger/app.py:807-846](./ledger/app.py) |
| 11 | Snapshot to disk | PASS | Probe: `POST /v1/knowledge/snapshot` → `count=3`, path `/tmp/kl-audit-*/data/knowledge-snapshots/acme-site-*.json` (under `/tmp`, ends `.json`). Impl: [ledger/app.py:848-872](./ledger/app.py) |
| 12 | Store-and-close | PASS | Probe: `POST /v1/knowledge/store-and-close` with 1 rule → `stored` len 1, plus compact+sources+snapshot keys. Impl: [ledger/app.py:874-898](./ledger/app.py) |
| 13 | Curate: duplicates, suggest-only | PASS | Probe: two `Same title`/`Same Title` entries → 1 `duplicate` suggestion; search count unchanged and all `verified=0` (never deletes/verifies). Impl: [ledger/app.py:928-953](./ledger/app.py). Repo: [tests/test_session_commands.py:56-73](./tests/test_session_commands.py) |
| 14 | Curate: high-confidence contradiction + `contradictions_mode` | PASS | Probe: D14 pair (`D14: rules are binding once approved.` vs `D14 is rejected: rules are never binding.`) → exactly 1 contradiction, `confidence=high`, reason anchor `'d14'`, `contradictions_mode=flags`. Duplicate-only case also reported `flags`. Impl: [ledger/app.py:954-1030](./ledger/app.py). Repo: [tests/test_curator_precision.py:31-42](./tests/test_curator_precision.py) |
| 15 | Visibility spot-checks (restricted/company/public) | PASS | Probe: public note `id=15` visible to `gus` in search and `GET` 200; restricted note (allowed `["amy"]`) hidden from `gus` search and `GET` 404; company entry `GET` as `gus` 404. Impl `_can_see` / `_knowledge_or_404`: [ledger/app.py:598-629](./ledger/app.py) |
| 16 | Cross-tenant canary | PASS | Probe: `CANARY-SECRET-TITLE` / `xyzzy` planted in acme; `gus` search for `xyzzy` → `[]`. Impl search filter + 404-no-oracle: [ledger/app.py:670-710](./ledger/app.py). Repo: [tests/test_knowledge_lite.py:80-93](./tests/test_knowledge_lite.py) |
| 17 | Hostile search input | PASS | Probe: 11 hostile queries (`"`, `'`, `*`, parens, `:`, `^`, `~`, `****`, SQL-injection string, `a OR 1==1`) → all 200/422, none 500; store intact afterwards. Impl tokenised `MATCH` builder: [ledger/app.py:673-676](./ledger/app.py). Repo: [tests/test_knowledge_lite.py:96-104](./tests/test_knowledge_lite.py) |
| 18 | Scope/kind validation | PASS (repo test) | `tests/test_knowledge_lite.py::test_scope_and_kind_validation` PASS — no-scope body 422, bogus kind 422, ghost project 404, blank title 422. [tests/test_knowledge_lite.py:107-112](./tests/test_knowledge_lite.py) |

## Repo test re-runs (read-only)

Command: `.venv/bin/python -m pytest tests/test_session_commands.py tests/test_curator_precision.py tests/test_onboard_live_deps.py tests/test_knowledge_lite.py tests/test_workloop_knowledge.py -v`

Result: **14 passed, 1 skipped** (same process, one run):

- `tests/test_session_commands.py:27` `test_store_compact_snapshot_and_close` — PASS
- `tests/test_session_commands.py:56` `test_curate_suggests_only` — PASS
- `tests/test_curator_precision.py:31` `test_true_contradiction_flagged_with_confidence` — PASS
- `tests/test_curator_precision.py:45` `test_agreement_and_unrelated_negation_not_flagged` — PASS
- `tests/test_curator_precision.py:57` `test_medium_confidence_shown_as_review_hints` — PASS
- `tests/test_curator_precision.py:69` `test_contradiction_overflow_becomes_ranked_hints` — PASS
- `tests/test_onboard_live_deps.py:19` `test_live_onboard_register_reaches_upstream` — **SKIPPED (by design)**. Named reason: needs live upstream creds (`ROUVR_LIVE_ONBOARD=1` + `HYEVE_ADMIN_USERNAME`); module-level `skipif` documents the contract. Not a failure.
- `tests/test_knowledge_lite.py:35` `test_create_search_get_cite` — PASS
- `tests/test_knowledge_lite.py:48` `test_update_is_new_version_old_kept` — PASS
- `tests/test_knowledge_lite.py:62` `test_history_replays_chain` — PASS
- `tests/test_knowledge_lite.py:72` `test_confirm_marks_verified` — PASS
- `tests/test_knowledge_lite.py:80` `test_cross_tenant_canary_never_leaks` — PASS
- `tests/test_knowledge_lite.py:96` `test_hostile_search_input_never_500s` — PASS
- `tests/test_knowledge_lite.py:107` `test_scope_and_kind_validation` — PASS
- `tests/test_workloop_knowledge.py:34` `test_rule_changes_outcome_with_citation` — PASS (spins a local uvicorn on 127.0.0.1:18233 with a `tmp_path` DB; verifies supervisor auto-cite of the verified rule, contract `description` carrying `knowledge:<id>@v` + `Knowledge in force`, `knowledge_context_bundle` citations, and reviewer-report receipt citations via `workhub_show_task`; exercises [rouvr/mcp_server.py:219-269](./rouvr/mcp_server.py) and [rouvr/mcp_server.py:292-439](./rouvr/mcp_server.py))

No BLOCKED steps: every scripted check ran locally without credentials. The single skip is the suite's own documented live-dep gate, not an audit blocker.

## Breakage found

None. All-green: every cold-followed call behaved per the repo contracts (append-only versions, latest-only search, verified flag on reads, tenant+visibility filtering on every query, suggest-only curator, snapshot under the configured data dir).

## Notes / limits

- Snapshot path asserted to start with the throwaway `/tmp` prefix — confirms `ROUVR_DATA_DIR` was honoured and `.data/` untouched.
- Probe `TestClient` coverage is same-process; the work-loop leg additionally passed over real HTTP (uvicorn fixture in `test_workloop_knowledge.py`).
- Quoted D-number anchors (`D14`) classify `high` confidence; shared quoted-phrase anchors with asymmetric negation classify `medium` and flip `contradictions_mode` to `review_hints`; >15 contradictions truncate to 10 ranked hints — all per [tests/test_curator_precision.py:45-80](./tests/test_curator_precision.py), re-run green.
- Schema reference: [ledger/schema.sql:102-121](./ledger/schema.sql) (`knowledge_entries` + `knowledge_fts`).
