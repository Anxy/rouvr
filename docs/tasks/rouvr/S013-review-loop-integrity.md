# Sprint S013 — Review-loop integrity

## Sprint ID

`S013`

## Sprint Title

One verdict vocabulary, PR hygiene on rework, readable evidence

## 1. Objective

Make the coder–reviewer loop read honestly end to end: a single verdict
vocabulary, no abandoned PRs after rework, evidence the supervisor can
read without double-parsing, and a guard against instructions that live
only in task prose.

## 2. Roadmap Mapping

```text
Roadmap sections: MVP-1 hardening; audit findings 6, 11, 13 (2026-10-30);
  PM gaps: verdict vocabulary, optional prose instructions
Related components: rouvr/gate.py, rouvr/activities.py (verify, rework),
  rouvr/muse.py (verifier prompt), rouvr/gitops.py (ensure_pr),
  rouvr/mcp_server.py (workhub_create_task, workhub_show_task)
```

## 3. In Scope

- Verdict vocabulary: approve / request_changes / reject everywhere
  (gate, reviewer_report, prompts, MCP output)
- Rework closes the superseded attempt's PR
- workhub_show_task returns decoded evidence payloads
- workhub_create_task warns on instructions missing from criteria

## 4. Out of Scope

- Branch reuse across attempts (PR hygiene only in this sprint);
  changes to gate criteria or hidden-test mechanics

## 5. Target Files

```text
rouvr/gate.py, rouvr/activities.py, rouvr/muse.py, rouvr/gitops.py,
rouvr/mcp_server.py, rouvr/workflows.py (verdict handling)
tests/test_r9_close.py, tests/test_mcp_server.py, tests/test_git_grant.py
SUPERVISOR.md, personas/reviewer.md (vocabulary)
```

## 6. Tasks

### Task 13.1 — One verdict vocabulary (IMPLEMENTATION)
- Scope: rename gate outcomes to the verdict vocabulary; map at the
  reviewer_report boundary; update prompts and persona files.
- AC: no `gate: reject`-style mixed terms remain in new evidence;
  workflow routing (approve → close, request_changes → rework,
  reject → blocked) unchanged in behavior.
- Tests-first: gate/verdict mapping tests.
- Happy proof: reject→rework→approve live run with clean terms.
- Non-happy proof: true reject still blocks the task.
- Evidence: pytest output; evidence transcripts from the live run.

### Task 13.2 — PR hygiene on rework (IMPLEMENTATION)
- Scope: close previous attempt PR when a new attempt opens its PR
  (gh pr close with a superseded-by comment).
- AC: after a reject→rework→approve run, exactly one Rouvr PR is open
  for the task; the closed PR names its successor.
- Non-happy proof: failure to close the old PR is evidence, not silent.
- Evidence: `gh pr list` transcript for the run's repo.

### Task 13.3 — Readable evidence + prose guard (IMPLEMENTATION)
- Scope: decode evidence payloads in workhub_show_task; create-time
  warning for prose instructions absent from criteria.
- AC: supervisor reads a reviewer verdict with zero manual parsing;
  the task-21 description shape triggers the warning; a clean task
  does not.
- Tests-first: show_task shape test; warning trigger/non-trigger tests.
- Evidence: pytest output; MCP transcripts.

## 7. Acceptance Criteria

```text
AC-1: Given any new reviewer_report, its verdict is one of approve /
  request_changes / reject, consistently across gate, report, prompts.
AC-2: Given a rework run, exactly one open Rouvr PR remains per task.
AC-3: Given workhub_show_task output, evidence payloads are decoded
  objects, not JSON strings.
AC-4: Given a description instruction missing from criteria,
  workhub_create_task returns a visible warning.
```

## 8. Tests Required

- Unit: verdict mapping, PR-close call, payload decoding, prose warning
- Regression: full public suite + hygiene before closeout
- Non-happy: true-reject blocks; PR-close failure recorded as evidence

## 9. Evidence Required

Pytest results, files-changed list, live reject→rework transcripts,
`gh pr list` before/after.

## 10. Definition of Done

ACs met; no scope expansion; closeout produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S013: PASS
Implementation Summary: one verdict vocabulary in all new evidence
  (reviewer_report and submit_attempt carry verdict: approve /
  request_changes / reject; gate detail kept as gate_detail only);
  rework attempts close the superseded PR and record pr_superseded
  evidence (failures recorded, never silent); workhub_show_task decodes
  evidence payloads; workhub_create_task warns when an instruction
  lives only in prose.
Files Changed: rouvr/activities.py, rouvr/gitops.py, rouvr/mcp_server.py,
  tests/test_mcp_server.py
Tests Run: prose-warning and decoded-evidence tests; live task 25
  reviewer_report payload reads {verdict: approve, notes: ...} with no
  double parsing.
Non-Happy-Path Tests: warning non-trigger shapes; close-failure becomes
  pr_superseded evidence (code path, exercised on attempt>1 runs).
Known Gaps: branch reuse across attempts still open (PR hygiene only).
```

## 12. Task Completion Reports

- Task 13.1: Done. Evidence: live decoded verdict payload. Next: 13.2.
- Task 13.2: Done. Evidence: close_superseded_pr + pr_superseded evidence path. Next: 13.3.
- Task 13.3: Done. Evidence: test_show_task_decodes_evidence, prose tests. Next: S014.
