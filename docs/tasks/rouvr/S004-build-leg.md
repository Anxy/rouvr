# Sprint S004 — R6 build leg (personas, launcher, gitops)

## Sprint ID

`S004`

## Sprint Title

Builder turn: personas, execspec, headless launcher, workspace + PR

## 1. Objective

Implement the build leg: resolve the builder persona, load the exec spec,
run a headless `muse` turn in a fenced workspace, open exactly one PR per
attempt, and record system evidence. Proven by a live launcher turn.

## 2. Roadmap Mapping

```text
Roadmap sections: Step 8 (R6)
Related components: rouvr/personas.py, rouvr/execspec.py, rouvr/muse.py,
  rouvr/gitops.py, rouvr/activities.py (_run_builder_turn, verify_binding)
```

## 3. In Scope

- Persona registry (AIX mapping source, YAML fallback, 0600 creds, SELF_REVIEW)
- ExecSpec provider (local YAML); builder/verifier prompt builders
- Headless launcher (real envelope, deltas, resume-by-sid, kill discipline)
- Workspace prepare + idempotent PR; `verify_binding` (contract-rev + role pair)

## 4. Out of Scope

- Submit gate, hidden tests, runner, verifier (S005–S006); rehearsal (S007)

## 5. Target Files

```text
rouvr/personas.py, rouvr/execspec.py, rouvr/muse.py, rouvr/gitops.py
rouvr/activities.py, tests/test_unit.py, tests/test_api_personas.py
```

## 6. Tasks

### Task 4.1 — Personas + execspec + binding (IMPLEMENTATION)
- Scope: personas.py, execspec.py, verify_binding.
- AC: AIX mapping loads roles; non-0600 creds refused; equal-principal pair
  raises SELF_REVIEW; moved contract raises CONTRACT_MOVED; personas come
  from tenant roles (bindings name none — live-confirmed).
- Non-happy proof: SELF_REVIEW, missing/perm/loose creds file, moved contract.
- Evidence: unit tests + live binding read.

### Task 4.2 — Launcher + gitops + builder turn (IMPLEMENTATION)
- Scope: muse.py, gitops.py, _run_builder_turn, prompt builders.
- AC: live headless turn completes (`launcher-ok`); resume reuses the sid;
  `META_API_KEY` honored; exactly one PR per branch (reuse if open);
  evidence rows attached (execution_spec, muse_session, pr_record).
- Non-happy proof: turn timeout kills the process group; no session id → CONTRACT.
- Evidence: live probe output, unit tests.

## 7. Acceptance Criteria

```text
AC-1: role_pair with equal principals raises SELF_REVIEW; by_role with none raises SCOPE.
AC-2: A credential file readable beyond owner is refused before any use.
AC-3: A live headless turn completes and its session id is recorded as evidence.
AC-4: ensure_pr on a branch with an open PR returns it (no second PR).
```

## 8. Tests Required

- Unit: personas registry/pair/creds; prompt builders; criterion lines
- Live: headless launcher turn; gh PR reuse probe
- Non-happy: SELF_REVIEW, AUTHZ creds, CONTRACT moved/no-session

## 9. Evidence Required

Pytest results, live probe outputs, files-changed list.

## 10. Definition of Done

ACs met; live turn proven; no scope expansion; closeout produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S004: PASS
Implementation Summary: build leg complete; live turn launcher-ok; PR handling
  idempotent; binding/persona model corrected to live shapes (roles, not fields).
Files Changed: rouvr/personas.py, rouvr/execspec.py, rouvr/muse.py, rouvr/gitops.py,
  rouvr/activities.py, tests/test_unit.py, tests/test_api_personas.py
Tests Run: unit suites green; live launcher + PR probes pass
Non-Happy-Path Tests: SELF_REVIEW / AUTHZ / CONTRACT_MOVED cases covered
Known Gaps: headless MCP unverified → file transports stand (accepted, in S005/S006).
```

## 12. Task Completion Reports

- Task 4.1: Done. Evidence: registry tests + live binding. Next: 4.2.
- Task 4.2: Done. Evidence: launcher-ok + PR reuse. Next: S005.
