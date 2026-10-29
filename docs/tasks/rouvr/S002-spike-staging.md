# Sprint S002 — R2 resume spike + A1 staging confirmation

## Sprint ID

`S002`

## Sprint Title

Prove same-session resume; confirm WorkHub routes/bodies against staging

## 1. Objective

De-risk the two integration unknowns with live evidence: (a) whether a
killed `muse` turn resumes by session id, (b) the true staging route/body
shapes. No product code except the spike script.

## 2. Roadmap Mapping

```text
Roadmap sections: Step 3 (R2), Step 4 (A1)
Related components: scripts/spike_resume.sh, scripts/workhub_readbacks.sh, docs/spikes/, docs/workhub-fixes.md
```

## 3. In Scope

- R2 spike script + verdict doc (RESUME or RESPAWN, transcript hashes)
- A1 fix list from workhub source; live OpenAPI snapshot + readback suite
- Client realignment to probed shapes (bodies only, no new features)

## 4. Out of Scope

- Workflow/lease/launcher work (S003–S004); scope-grant procurement (human, → S007)

## 5. Target Files

```text
scripts/spike_resume.sh, scripts/workhub_readbacks.sh
docs/spikes/muse-resume.md, docs/workhub-fixes.md, rouvr/workhub/client.py
```

## 6. Tasks

### Task 2.1 — R2 resume spike (VERIFICATION)
- Scope: spike script + doc only.
- AC: script exits 0 with RESULT=RESUME|RESPAWN; transcript hashes recorded.
- Happy proof: resume by same session id works. Non-happy proof: inconclusive
  run exits 2/3 (not a false verdict).
- Evidence: `docs/spikes/muse-resume.md`, exit code.

### Task 2.2 — A1 confirm + readbacks (IMPLEMENTATION + VERIFICATION)
- Scope: fix list doc, readback script expectations, client body map.
- AC: all §0.4 routes confirmed; readbacks suite exits 0; bodies that differ
  from guesses realigned in the client with tests.
- Non-happy proof: capabilities 403 and attempts 403 captured as explicit
  refusal codes (not silent skips).
- Evidence: `docs/workhub-fixes.md`, readback output, `test_stage_shape.py`.

## 7. Acceptance Criteria

```text
AC-1: spike_resume.sh exits 0 with a recorded RESUME verdict + hashes.
AC-2: workhub_readbacks.sh exits 0 (by-number 200; capabilities 403-known).
AC-3: Given a stage-shaped task payload, canonical_task maps id/rev/criteria.
AC-4: Given missing binding, get_binding_by_number returns {} (not raise).
```

## 8. Tests Required

- Unit: `tests/test_stage_shape.py` (canonical task, envelope, evidence norm)
- Live: spike script; readbacks suite; mutation probes on scratch task #100
- Non-happy: 403 refusal codes asserted; inconclusive-spike exit codes

## 9. Evidence Required

Verdict doc, readback output, pytest results, probed shape notes.

## 10. Definition of Done

ACs met; verdict + fix list filed; no scope expansion; closeout produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S002: PASS
Implementation Summary: RESUME proven (same-session-id); staging reachable —
  289-path OpenAPI snapshot, all §0.4 routes confirmed, bodies realigned
  (envelope, epoch keys, evidence_ref, status key, executor_sub, record_version rev).
Files Changed: scripts/spike_resume.sh, scripts/workhub_readbacks.sh,
  docs/spikes/muse-resume.md, docs/workhub-fixes.md, rouvr/workhub/client.py,
  tests/test_stage_shape.py
Tests Run: spike (RESUME), readbacks exit 0, 8 shape tests pass
Non-Happy-Path Tests: capabilities/attempts 403s asserted; binding-miss → {}
Known Gaps: workhub:attempt:open grant missing (→ S007/A2, human gate).
```

## 12. Task Completion Reports

- Task 2.1: Done. Evidence: RESUME verdict + hashes. Next: 2.2.
- Task 2.2: Done. Evidence: readbacks exit 0; shape tests green. Next: S003.
