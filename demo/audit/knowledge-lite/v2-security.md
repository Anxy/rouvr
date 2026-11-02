# Knowledge Lite security re-audit (v2)

- HEAD: `bc735abda101cb7995b52d9a92b619265822874`
- Date (UTC): 2026-11-02
- Mode: read-only. No source, test, or config file was modified.
  All live probes ran against throwaway SQLite DBs under `/tmp/kl_audit/`
  via `TestClient`, never `.data/`.
- Scope: `ledger/app.py` knowledge endpoints (visibility, store/compact/
  snapshot/store-and-close, curate with `contradictions_mode`,
  `/v1/knowledge-rules` verified-rules bundle), auth middleware
  (fail-closed, `ROUVR_INSECURE_DEV`), `rouvr/mcp_server.py` knowledge
  tools + spec citations, `ledger/schema.sql`, `rouvr/activities.py`
  `close_task` note, and the test files `test_knowledge_lite.py`,
  `test_visibility_fuzz.py`, `test_fail_closed.py`, `test_audit_fixes.py`,
  `test_curator_precision.py`, `test_session_commands.py`.
- Repo suite corroboration: the six files above pass unmodified,
  21 passed (`pytest` run during this audit).

## Verdict: NOT clean — 1 finding (medium-low)

One real privilege-widening bug: **`store-and-close` silently drops
per-item `visibility`/`allowed_users`**, storing every item as `company`.
All three prior findings (F1–F3) stay fixed, and every other scoped
check passed. Details below.

## Prior findings — all still fixed

- **F1, cross-tenant version write refused: PASS.** Probed: user in
  `globex` POSTing `/v1/knowledge/{acme-id}/versions` returns 403 on a
  public entry and 403/404 on a restricted entry; same-tenant version
  still returns 200. Enforced at `ledger/app.py:745-751`
  (`caller_tenant != old["tenant"]` → 403 after the `_knowledge_or_404`
  visibility check at `ledger/app.py:742`). Covered by
  `tests/test_audit_fixes.py:29-41`.
- **F2, compact snapshot inherits restrictive visibility: PASS.**
  Probed: compacting a project containing a confirmed restricted entry
  yields `snapshot.visibility == "restricted"`; a same-tenant non-member
  gets 404 on the snapshot id and zero search hits for its title; a
  compact run as the non-member excludes restricted titles/bodies from
  the snapshot body. Enforced at `ledger/app.py:831-842` (any restricted
  input → restricted snapshot with author + `allowed_users` union, plus
  the compactor). Covered by `tests/test_audit_fixes.py:44-60`.
- **F3, confirm gated to creator-or-admin: PASS.** Probed: non-creator
  same-tenant confirm → 403; cross-tenant confirm on a public entry →
  403/404; stack (`supervisor`) confirm → 200. Enforced at
  `ledger/app.py:772-773`. Covered by `tests/test_audit_fixes.py:63-77`.

## Live-probe results (throwaway DBs, `TestClient`)

| # | Check | Result |
|---|-------|--------|
| 1 | Anonymous (no token) on all 12 knowledge routes (create, search, get, history, versions, confirm, store, compact, snapshot, store-and-close, curate, `/v1/knowledge-rules`) | PASS, all 401 (`ledger/app.py:245-281`) |
| 2 | Wrong `X-Rouvr-Token` on GET search and POST create | PASS, 401 (`ledger/app.py:270-277`) |
| 3 | No-token server: `/v1/*` 401, `/health` 200 | PASS (repo test `test_fail_closed.py:15-21`) |
| 4 | `ROUVR_INSECURE_DEV=1` warns on stderr and serves locally | PASS (repo test `test_fail_closed.py:36-40`, `ledger/app.py:231-234`) |
| 5 | `ROUVR_INSECURE_DEV=1` with public bind refuses startup | PASS, `RuntimeError` (`ledger/app.py:228-230`, repo test `test_fail_closed.py:43-49`) |
| 6 | Cross-tenant search/get/history of company entries | PASS, `[]` / 404, no existence oracle (`ledger/app.py:625-628`) |
| 7 | Cross-tenant read of public entries | PASS, 200 + search hit (intended: public = any signed-in user, `ledger/app.py:601-603`) |
| 8 | Cross-project same-tenant read of company entries | PASS, 200 (intended: `company` is tenant-wide, `ledger/app.py:604-605`) |
| 9 | Restricted: author 200, non-member 404, search hidden, `allowed_users` member 200 | PASS (`ledger/app.py:606-610`) |
| 10 | F1 cross-tenant version write | PASS (see above) |
| 11 | F2 compact snapshot visibility | PASS (see above) |
| 12 | F3 confirm gate | PASS (see above) |
| 13 | Visibility-change gate (non-creator → 403, creator → 200, new version `verified == 0`) | PASS (`ledger/app.py:757-759`; reset verified via `_knowledge_insert` default `0`, `ledger/app.py:631-644`) |
| 14 | `allowed_users` with non-`restricted` visibility on create → 422 | PASS (`ledger/app.py:661-662`) |
| 15 | FTS hostile input (16 payloads incl. `"`, `*`, `(`, `:`, `^`, `~`, `'; DROP TABLE…`, `a OR 1=1`, bare `AND`/`OR`/`NOT`, backslash) | PASS, all 200/422, table intact after (`ledger/app.py:673-676` tokenises with `\w+` and quotes each token) |
| 16 | `/v1/knowledge-rules` returns only latest verified rules, tenant-filtered, anon 401 | PASS (`ledger/app.py:900-926`) |
| 17 | Snapshot export file contains only caller-visible rows | PASS (`ledger/app.py:856-864` filters with `_can_see` before writing) |
| 18 | Curate cross-tenant → 403; `contradictions_mode` is `flags`/`review_hints`; curate never verifies/deletes | PASS (`ledger/app.py:935-936`, `1003-1007`; repo tests `test_session_commands.py:56-73`, `test_curator_precision.py`) |
| 19 | Old-version history stays restricted after supersede (non-member → 404) | PASS (`ledger/app.py:727-731` re-checks each ancestor) |
| 20 | **Store-and-close preserves per-item `visibility`** | **FAIL — see Finding 1** |

Checks 1–19 and F1–F3 pass. Check 20 fails.

## Finding 1 (medium-low): `store-and-close` drops per-item visibility

- **What happens (observed, twice, fresh DBs):** POSTing
  `/v1/knowledge/store-and-close` with
  `items: [{kind: "note", title: "SC", body: "sc secret",
  visibility: "restricted", allowed_users: []}]` returns 200 with
  `stored[0].visibility == "company"` and `allowed_users == []`.
  A same-tenant non-member then reads the entry (GET → 200) and finds
  it via search. The entry the caller marked restricted is visible to
  the whole tenant.
- **Root cause:** `knowledge_store_and_close` at
  `ledger/app.py:874-898` inserts each item via `_knowledge_insert(c,
  tenant, project, kind, ititle, kbody, ident)` (`ledger/app.py:892-893`)
  without reading `it.get("visibility")` / `it.get("allowed_users")`,
  unlike the sibling paths `create_knowledge` (`ledger/app.py:653-668`)
  and `knowledge_store` (`ledger/app.py:790-804`), which both honour
  them. It also skips the `allowed_users`-only-with-`restricted` 422
  check the other two paths enforce. The MCP wrapper
  `knowledge_store_and_close` (`rouvr/mcp_server.py:200-206`) forwards
  items opaquely, so the drop is invisible to the caller.
- **Blast radius:** same-tenant company-wide readability of entries the
  author asked to restrict. Cross-tenant reads stay blocked (company ≠
  public), and the derived compact snapshot is consistently `company`,
  so this is privilege-widening within the tenant, not a cross-tenant
  leak.
- **Suggested fix (not applied — read-only audit):** parse and validate
  `visibility`/`allowed_users` per item in `knowledge_store_and_close`
  exactly as `knowledge_store` does.

## Hardening notes (low, no live leak demonstrated)

1. **Snapshot filename interpolates unvalidated scope.**
   `create_tenant` (`ledger/app.py:369-380`) and `create_project`
   (`ledger/app.py:382-394`) accept any non-blank string, and
   `knowledge_snapshot` builds the export path as
   `root / f"{tenant}-{project}-{ts}.json"` (`ledger/app.py:865-870`).
   Probed: tenant `a/b` + project `../../evil` is creatable (stack-only)
   but the snapshot then fails closed with a 500 (missing intermediate
   dirs, nothing written). Only a stack token can create such names,
   and stack already reads everything, so this is robustness, not a
   vuln. Consider slug-validating tenant/project names or sanitising
   the filename.
2. **`knowledge_store` keeps (inert) `allowed_users` on non-restricted
   entries.** Unlike create (`ledger/app.py:661-662`), `/v1/knowledge/store`
   (`ledger/app.py:790-804`) has no 422 for `allowed_users` combined
   with `company`/`public`; the list is stored but ignored by `_can_see`
   (observed: stored verbatim, no access effect). Cosmetic inconsistency;
   consider rejecting it there too for a uniform contract.
3. **Sentinel (valid upstream JWT, not yet onboarded) asymmetry:**
   `/v1/knowledge/search` returns 403 "complete Rouvr onboarding first"
   (`ledger/app.py:681-686`) while `/v1/knowledge/{id}` and
   `/v1/knowledge-rules` serve that identity public entries/rules
   (`ledger/app.py:618-624`, `900-926`). Public-only in both serving
   paths, so no leak — just an uneven fail-closed posture worth
   aligning deliberately.

## Out-of-scope observations (correct by design, recorded for precision)

- Same-tenant cross-project reads of `company` entries are allowed;
  `company` means tenant-wide (`ledger/schema.sql:68`, `ledger/app.py:604-605`).
- Public entries are readable/searchable by any signed-in user of any
  tenant (`ledger/app.py:601-603`, `698-699`); anonymous users still get
  401 on every knowledge route.
- Cross-tenant misses return 404, never 403, so no id oracle
  (`ledger/app.py:626`).
- MCP tool docstrings state the enforced contract accurately:
  unverified-until-confirmed (`rouvr/mcp_server.py:104-116`), citations
  + never-bind-unverified (`rouvr/mcp_server.py:119-128`), creator/admin
  visibility changes (`rouvr/mcp_server.py:146-160`), human-confirmed
  only for `knowledge_confirm` (`rouvr/mcp_server.py:163-169`),
  suggestions-only curator (`rouvr/mcp_server.py:209-215`).
- Spec citations attach only verified rules with citations and persist
  them as `knowledge_citations` evidence (`rouvr/mcp_server.py:244-298`);
  `close_task` re-lists those citations best-effort and never fails the
  close on them (`rouvr/activities.py:405-425`).
- Curator contradiction precision (`contradictions_mode`, anchor +
  opposing-claim gating, top-10 cap) matches the checked-in behaviour
  tests in `tests/test_curator_precision.py` (all pass).

## Addendum 2026-11-02 — check 20 resolved (20/20)

The store-and-close finding was real and is fixed: the loop now carries
per-item `visibility`/`allowed_users` through (`ledger/app.py`, same
validation as `/store`), covered by
`tests/test_audit_fixes.py::test_store_and_close_keeps_visibility`.
Re-probed live after restarting the ledger onto the fixed head: a
`restricted` item stored via store-and-close comes back `restricted`,
author reads 200, same-tenant non-member reads 404. Check 20 now passes.
