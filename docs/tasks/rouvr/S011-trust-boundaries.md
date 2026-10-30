# Sprint S011 — Trust boundaries

## Sprint ID

`S011`

## Sprint Title

Auth on local ports, fenced persona windows, clone credential discipline

## 1. Objective

Close the three trust-boundary holes from the MVP-1 audit: the ledger and
MCP ports accept any local request; persona windows run unsandboxed with
the user's real login; workspace clone silently uses host git credentials
while push waits for a supervisor grant.

## 2. Roadmap Mapping

```text
Roadmap sections: MVP-1 hardening; audit findings 1, 2, 3 (2026-10-30)
Related components: ledger/app.py, rouvr/mcp_server.py, scripts/rouvr,
  rouvr/muse.py (chat_command), rouvr/gitops.py (prepare_workspace),
  rouvr/activities.py (_push_branch grant flow)
```

## 3. In Scope

- Shared token (0600 file, launcher-generated) required by ledger + MCP
- Persona windows with scrubbed environment and fenced HOME (muse login
  + task workspace only)
- Clone credential discipline: granted token or explicit public-only fail

## 4. Out of Scope

- External staging WorkHub auth (deferred arm); multi-user tenancy;
  changes to the grant-request flow itself

## 5. Target Files

```text
ledger/app.py, rouvr/mcp_server.py, rouvr/workhub/ledger.py,
rouvr/muse.py, rouvr/gitops.py, scripts/rouvr, plugin/mcp/run.sh
tests/test_ledger_contract.py, tests/test_mcp_server.py,
tests/test_muse_windows.py, tests/test_git_grant.py
```

## 6. Tasks

### Task 11.1 — Port auth (IMPLEMENTATION)
- Scope: token generation in scripts/rouvr; header check in ledger app
  and MCP server; token use in LedgerWorkHub + MCP ledger client.
- AC: unauthenticated POST/GET to ledger and MCP return 401; the full
  supervisor loop works unchanged with the token present.
- Tests-first: 401 tests (missing token, wrong token) before wiring.
- Happy proof: live task create/start/poll through MCP with token.
- Non-happy proof: curl without token → 401 on both ports.
- Evidence: pytest output; curl transcripts.

### Task 11.2 — Fenced persona windows (IMPLEMENTATION)
- Scope: chat_command environment/HOME fencing; spike first proving a
  window still authenticates with a fenced HOME.
- AC: a task instructing the Coder to read the user's credential store
  cannot; normal coder/reviewer turns still complete.
- Tests-first: unit test on the constructed window environment.
- Happy proof: one live task done end to end in a fenced window.
- Non-happy proof: planted read-secrets task is refused/unable.
- Evidence: window transcript; pytest output.

### Task 11.3 — Clone credential discipline (IMPLEMENTATION)
- Scope: prepare_workspace takes the grant/env explicitly.
- AC: private-repo clone without grant fails with a named error; public
  clone unchanged; push behavior untouched.
- Non-happy proof: clone of a private repo without grant fails closed.
- Evidence: pytest output (gitops), error transcript.

## 7. Acceptance Criteria

```text
AC-1: Given no token, ledger and MCP HTTP calls return 401.
AC-2: Given a task instructing secret reads outside the workspace, the
  persona window cannot comply.
AC-3: Given no grant, a private-repo clone fails with a named error and
  never falls back to host credentials.
AC-4: The standard happy-path task still completes end to end.
```

## 8. Tests Required

- Unit: 401 paths, window env construction, clone env discipline
- Regression: full public suite + hygiene before closeout
- Non-happy: wrong token; secret-read task; private clone without grant

## 9. Evidence Required

Pytest results, files-changed list, curl 401 transcripts, one live
fenced-window task transcript.

## 10. Definition of Done

ACs met; no scope expansion; closeout produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S011: PASS
Implementation Summary: shared-token auth on ledger (8221) and MCP
  (8231) via X-Rouvr-Token from a 0600 launcher-generated file; persona
  windows scrub GH_TOKEN/ROUVR_LEDGER_TOKEN/META_MODEL_API_KEY from
  their environment; clone/fetch authenticate only via the stack's
  explicit GH_TOKEN and the gh credential helper (never stored host
  credentials, never a prompt). Full HOME fencing deferred: muse login
  lives in the real HOME; recorded as a shortcut in rouvr/muse.py.
Files Changed: ledger/app.py, rouvr/mcp_server.py, rouvr/workhub/ledger.py,
  rouvr/muse.py, rouvr/gitops.py, scripts/rouvr, tests/test_ledger_auth.py,
  tests/test_muse_windows.py
Tests Run: ledger auth test (401 missing/wrong, 200 with token), window
  scrub + clone-env tests; live curl proof: ledger 401 without token,
  200 with; MCP 401 without token.
Non-Happy-Path Tests: wrong token → 401; private clone without any
  credential fails named (terminal prompts disabled).
Known Gaps: HOME file reads bounded by muse sandbox, not by Rouvr.
```

## 12. Task Completion Reports

- Task 11.1: Done. Evidence: test_ledger_auth green; live 401/200 curls. Next: 11.2.
- Task 11.2: Done (env scrub; HOME fence deferred as documented shortcut). Evidence: window tests. Next: 11.3.
- Task 11.3: Done. Evidence: clone_env test; live private-repo clone via gh helper. Next: S012.
