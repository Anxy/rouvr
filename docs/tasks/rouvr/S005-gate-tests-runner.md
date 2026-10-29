# Sprint S005 — R7 submit gate + R8 hidden tests, runner, cheat-scan

## Sprint ID

`S005`

## Sprint Title

System-observed test evidence: gate, hidden suite, clean-room runner

## 1. Objective

Make test evidence builder-independent: the submit gate judges system facts,
hidden acceptance tests live outside the builder workspace, both suites run
in a clean container at the PR SHA, and the diff is cheat-scanned. One
result per attempt, written by `run_tests`.

## 2. Roadmap Mapping

```text
Roadmap sections: Step 9 (R7), Step 10 (R8a/R8)
Related components: rouvr/gate.py, rouvr/hiddentests.py, rouvr/testrunner.py,
  rouvr/cheatscan.py, rouvr/activities.py (run_tests), testrunner.Dockerfile
```

## 3. In Scope

- Gate: submit tool body, file transport, total evaluate(), evidence-backed rounds
- Hidden store: lock/hash/fetch, builder-clean check, literals, criterion parse
- Local + container runners; pinned pytest image; cheat-scan rules; `run_tests`

## 4. Out of Scope

- Verifier turn + close (S006); rehearsal (S007)

## 5. Target Files

```text
rouvr/gate.py, rouvr/hiddentests.py, rouvr/testrunner.py, rouvr/cheatscan.py
rouvr/activities.py, rouvr/host.py, rouvr/config.py, testrunner.Dockerfile
tests/test_gate.py, tests/test_r8_hidden.py
```

## 6. Tasks

### Task 5.1 — Submit gate (IMPLEMENTATION)
- Scope: gate.py + prompt line + tests.
- AC: builder claims criteria (refs are system-known); wrong branch / red CI /
  unclaimed criterion each reject with one line; 3rd round blocks; missing
  submit fails; garbage fails closed.
- Non-happy proof: BAD_SUBMIT, PR_NOT_ON_BRANCH, CI_NOT_GREEN,
  CRITERION_UNCLAIMED, NO_R8_EVIDENCE, blocked, failed.
- Evidence: `test_gate.py` (8 tests).

### Task 5.2 — Hidden tests + runner + cheat-scan (IMPLEMENTATION)
- Scope: hiddentests/testrunner/cheatscan/run_tests + Dockerfile + host wiring.
- AC: suite locks at creation and refuses overwrite; tamper detected; leaked
  builder copy refused; repo+hidden run green in the container at the PR SHA;
  all six cheat rules flag; clean diff passes; single `tested` result.
- Non-happy proof: tamper/leak EVIDENCE errors; cheat flags fail the run;
  empty outcomes fail; docker missing → DOWNSTREAM.
- Evidence: `test_r8_hidden.py` (8 tests); live container probe.

## 7. Acceptance Criteria

```text
AC-1: Overwriting a locked suite, or fetching a tampered one, raises.
AC-2: Builder workspace containing hidden bytes or names is refused.
AC-3: Container run of repo + hidden suites passes with parsed per-criterion outcomes.
AC-4: A diff with skip/mock/literal/stub/broad-except/deleted-test is flagged per rule.
AC-5: Two result cores for one attempt are impossible (single tested write).
```

## 8. Tests Required

- Unit: `tests/test_gate.py`, `tests/test_r8_hidden.py`
- Live: container runner probe (`passed=True, rc=0`)
- Non-happy: all gate rejects; tamper/leak; cheat rules; empty outcomes

## 9. Evidence Required

Pytest results, probe output, files-changed list.

## 10. Definition of Done

ACs met; runner live-probed; no scope expansion; closeout produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S005: PASS
Implementation Summary: gate + hidden store + clean-room runner + cheat-scan
  complete; runner image carries pinned pytest (stock slim has none, offline).
  Rehearsal fix folded in: single tested result per attempt (double-core 409s).
Files Changed: rouvr/gate.py, rouvr/hiddentests.py, rouvr/testrunner.py,
  rouvr/cheatscan.py, rouvr/activities.py, rouvr/host.py, rouvr/config.py,
  testrunner.Dockerfile, tests/test_gate.py, tests/test_r8_hidden.py
Tests Run: 8 gate + 8 hidden/runner/scan tests pass; container probe green
Non-Happy-Path Tests: every gate reject; tamper/leak; all six cheat rules
Known Gaps: none in sprint scope.
```

## 12. Task Completion Reports

- Task 5.1: Done. Evidence: gate tests green. Next: 5.2.
- Task 5.2: Done. Evidence: hidden/runner tests + live probe. Next: S006.
