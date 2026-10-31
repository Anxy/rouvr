# Sprint S019 — Launch-secret scoping + grant lifecycle (risks + G8)

## Sprint ID

`S019`

## Sprint Title

Scoped stack secrets, grant revocation, spec path config

## 1. Objective

Close the remaining review risks on top of S017/S018 (both preserved):
launch script no longer exports `GH_TOKEN` / `META_MODEL_API_KEY`
stack-wide; clone/fetch/push run on minimal env with a 0600 scoped token
file; per-task git grants are revoked at terminal close; window agents lose
`WORKHUB_JWT` too; spec repo/branch are env-configurable.

## 2. Roadmap Mapping

```text
Roadmap sections: review Risks + G8; MASTER §§4,9
Related components: scripts/rouvr, rouvr/gitops.py (gh_token, clone_env,
  revoke_git_grant), rouvr/activities.py (_push_branch, _close_to),
  rouvr/muse.py (chat fence), rouvr/worker.py (ops registration)
```

## 3. In Scope

- `scripts/rouvr`: no stack-wide secret exports; `$OPS/gh-token` (0600)
  written from `gh auth token` when available; `ROUVR_GH_TOKEN_FILE`,
  `ROUVR_SPEC_REPO`, `ROUVR_SPEC_BASE` env.
- `clone_env` minimal (PATH/HOME/proxies/token); `gh_token` 0600-enforced.
- `_push_branch` on `clone_env` + grant token (no worker-env inheritance).
- `_close_to` revokes the grant file on done/blocked/failed; keeps it on
  needs_review. `chat_command` also strips `WORKHUB_JWT`.
- `worker.py` registers `OpsTaskWorkflow` + ops activities (missed in S018).

## 4. Out of Scope

- Containerising the coder: host process with minimal env + HOME=cwd stands,
  marked `shortcut` in `muse.py`; upgrade to a testrunner-style container
  when the coder gets untrusted input or a broader workspace.
- Ledger api-token file: already 0600 (umask 077), consistent with receipt
  keys and persona creds; residual noted, not changed.
- Persona creds default path: functional and already overridable via
  `ROUVR_PERSONA_CREDS_DIR`; default kept so the demo launch keeps working.

## 5. Tasks

### Task 19.1 — Scoped launch secrets (risks)

- AC: no `export GH_TOKEN=` / `export META_MODEL_API_KEY=` in
  `scripts/rouvr`; gh-token file 0600-gated; clone env leaks no worker secrets.
- Tests-first: script static test; `gh_token` perm test; `clone_env` test.

### Task 19.2 — Grant revocation on close

- AC: grant file gone after done/blocked/failed close; kept on needs_review.
- Tests-first: `test_close_terminal_revokes_grant_needs_review_keeps`.

### Task 19.3 — Spec path config (G8)

- AC: `ROUVR_SPEC_REPO`/`ROUVR_SPEC_BASE` override the scratch defaults.
- Tests-first: script static test.

## 6. Closeout Report

- Review: APPROVED by reviewer (rouvr-lane review-006, AGENTS.md rule 15).
- Verification: gates at 2bec7fd — tests/ 289 passed; broad set 383 passed,
  2 skipped (1 temporalio flake cleared on isolation + re-run); hygiene
  clean; lifecycle valid; diff lifecycle-JSON-only.
- Ledger: needs_review → closeout (review) → done (closeout), 2026-10-31;
  INDEX companions regenerated.
- Evidence: 68ba6e0 (scope) + b6a50cf (sheet landed) + 2bec7fd (submit);
  lane VERDICT ACCEPT + CLOSEOUT in tasks/review-006/.
