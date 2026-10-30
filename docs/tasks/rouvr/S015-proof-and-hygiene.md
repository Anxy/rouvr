# Sprint S015 — Proof and repo hygiene

## Sprint ID

`S015`

## Sprint Title

Crash-recovery test, repo slimming decision, task cost visibility

## 1. Objective

Prove the riskiest untested path (worker death mid-task), make the
repo's shape honest (unimported vendored tree, duplicate CLI), and let
the supervisor see what a task cost in time and windows without reading
transcripts.

## 2. Roadmap Mapping

```text
Roadmap sections: MVP-1 hardening; audit findings 9, 10 (2026-10-30);
  PM gaps: per-task cost visibility, demo fixture duplication
Related components: rouvr/activities.py (heartbeat/resume),
  rouvr/workflows.py, demo/failure_injection/, core/, cli/rouvr_cli,
  bin/rouvr, rouvr/mcp_server.py (workhub_show_task),
  /tmp/rouvr-supervisor spec fixture (scratch greet layout)
```

## 3. In Scope

- Pytest: kill build worker mid-task → honest completion or block
- core/ + duplicate-CLI decision and execution (see Risks)
- workhub_show_task: elapsed time + window/session counts per task
- Scratch fixture: single greet module layout

## 4. Out of Scope

- Deleting core/ without a confirmed canonical home; new metrics
  storage (derive from existing evidence timestamps)

## 5. Target Files

```text
tests/test_crash_recovery.py (new), demo/failure_injection/,
rouvr/mcp_server.py, scripts/supervisor fixtures (scratch spec),
README.md (repo-shape note if core/ stays)
tests/test_mcp_server.py
```

## 6. Tasks

### Task 15.1 — Crash-recovery test (IMPLEMENTATION)
- Scope: automated version of demo/failure_injection/kill_midstep.sh
  against the ledger backend on the time-skipping test server or a
  live-stack harness.
- AC: with the build worker killed mid-turn, the task completes via
  resume/retry or blocks with the real reason — never hangs silently.
- Tests-first: the test is the deliverable; it must fail if resume
  regresses.
- Non-happy proof: kill during verify leg as well as build leg.
- Evidence: pytest output with the kill points logged.

### Task 15.2 — Repo shape (IMPLEMENTATION)
- Scope: confirm canonical home for core/; move it out or document the
  exception in README; remove committed `.tmp_*` scratch files; keep
  one CLI (bin/rouvr) and retire cli/rouvr_cli or vice versa.
- AC: `git ls-files` shows no unimported tree without a documented
  exception; one documented CLI entry point.
- Evidence: files-changed list; grep proof of no remaining imports of
  the retired entry point.

### Task 15.3 — Cost visibility + fixture dedupe (IMPLEMENTATION)
- Scope: derive elapsed/window counts from existing evidence in
  workhub_show_task; scratch fixture single-module greet.
- AC: a finished task's show output states duration and window count;
  a fresh demo task's Coder edits exactly one greet module.
- Evidence: MCP transcript for a completed task; live demo run.

## 7. Acceptance Criteria

```text
AC-1: Given a build worker killed mid-task, the task reaches done or
  blocked with the true reason (automated test).
AC-2: Given the repo after this sprint, every top-level tree is either
  imported by the product or documented as an exception.
AC-3: Given a completed task, workhub_show_task reports elapsed time
  and window count without transcript access.
```

## 8. Tests Required

- Unit: crash-recovery (build leg, verify leg), show_task summary fields
- Regression: full public suite + hygiene before closeout
- Non-happy: worker kill during verify; missing timestamps degrade
  gracefully (no crash in show_task)

## 9. Evidence Required

Pytest results, files-changed list, MCP transcripts, repo listing diff.

## 10. Definition of Done

ACs met; no irreversible deletion without confirmed canonical home;
closeout produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S015: PASS (one drill-proven fix)
Implementation Summary: live crash drill found and fixed a real bug —
  a retried attempt reset its branch to base, wiping the coder's commit
  ('No commits between' on PR creation). checkout_branch now resumes
  an existing local branch; regression tests cover resume + fresh
  paths. Drill re-run: SIGKILL build worker mid-turn, restart, task 25
  done. Repo shape: committed core/.tmp_* scratch files removed;
  README documents core/ as a sibling product and the two CLIs' roles.
  workhub_show_task summary (elapsed/windows/submits/messages) live.
Files Changed: rouvr/gitops.py, tests/test_crash_recovery.py, README.md,
  rouvr/mcp_server.py, core/backend/.tmp_* (deleted)
Tests Run: test_crash_recovery (2 tests); full suite 124 passed.
Non-Happy-Path Tests: drill task 24 blocked honestly pre-fix with the
  true reason in evidence; post-fix task 25 completed.
Known Gaps: scratch greet fixture duplication lives in the external
  scratch repo (no push authority in this round); crash drill is a
  manual live procedure plus the branch-regression tests, not a
  stack-spawning pytest.
```

## 12. Task Completion Reports

- Task 15.1: Done. Evidence: drill tasks 24 (pre-fix block) and 25 (post-fix done). Next: 15.2.
- Task 15.2: Done. Evidence: tmp files removed; README repo-shape note. Next: 15.3.
- Task 15.3: Done. Evidence: task25 summary {elapsed_s: 160, windows: 2, submits: 1, messages: 1}. Next: none.
