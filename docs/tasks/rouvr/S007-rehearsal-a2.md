# Sprint S007 — R10 rehearsal + record + A2 gates

## Sprint ID

`S007`

## Sprint Title

Kill/resume rehearsal to a recorded PASS; file human-gated A2 work

## 1. Objective

Prove the whole system with a scripted, no-human-input run — assign →
build → kill -9 the builder mid-turn → recover → verify → close — assert
done + 1 PR + same-attempt retry, record a redacted transcript, and file
the remaining human-gated A2 items.

## 2. Roadmap Mapping

```text
Roadmap sections: Step 12 (R10, A2)
Related components: rouvr/worker.py, scripts/r10_rehearsal.sh,
  docs/r10-runs/, docs/a2-gates.md, rouvr-rouvr-r10-scratch (GitHub)
```

## 3. In Scope

- Per-queue worker entrypoint; rehearsal script (seed → kill → assert → record)
- Scratch repo + fixtures; redacted transcript; A2 gates doc
- Rehearsal findings fixed in-tree (same files, no new scope)

## 4. Out of Scope

- Stage R10 (needs the scope grant — human); hyeve-lane back-port (needs 3);
  ed25519 receipts (P2)

## 5. Target Files

```text
rouvr/worker.py, scripts/r10_rehearsal.sh, docs/r10-runs/20261029T175518/
docs/a2-gates.md, rouvr/activities.py, rouvr/config.py, rouvr/host.py,
rouvr/starter.py, rouvr/workhub/{client,ledger}.py, rouvr/lease.py
```

## 6. Tasks

### Task 7.1 — Rehearsal to PASS (VERIFICATION)
- Scope: worker entry, rehearsal script, scratch repo, transcript.
- AC: script exits 0 with ledger task done; exactly 1 open PR on the attempt
  branch; exactly 1 handoff row and 1 attempt-1 row; assign_task completed on
  activity attempt ≥ 2; transcript redacted with zero token leaks.
- Non-happy proof: failed runs diagnosed to root cause in-tree (double
  result core, pgrep trigger, id reuse, P2 receipt stub) — no rerun luck.
- Evidence: `docs/r10-runs/20261029T175518/`, workflow result, history dump.

### Task 7.2 — A2 gates (PLANNING)
- Scope: `docs/a2-gates.md` only. No code.
- AC: four ordered gates filed with owners: attempt-scope grant, verifier
  credential, stage R10, back-port. Non-gates named as done.
- Evidence: the gates doc.

## 7. Acceptance Criteria

```text
AC-1: r10_rehearsal.sh exits 0; ledger task state is done.
AC-2: Exactly 1 open PR exists on rouvr/1/a1; handoff rows = 1; attempt-1 rows = 1.
AC-3: History shows assign_task completing on activity attempt ≥ 2.
AC-4: Transcript contains the receipt + logs with no unredacted tokens.
AC-5: A2 gates doc lists grant → credential → stage R10 → back-port in order.
```

## 8. Tests Required

- E2E: the rehearsal script itself (live model turns + live GitHub + local ledger/Temporal)
- Regression: full public suite + hygiene + gitleaks after every in-tree fix
- Non-happy: each failed rehearsal run root-caused before re-run

## 9. Evidence Required

Transcript dir, workflow result, history event dump, suite/gate outputs.

## 10. Definition of Done

ACs met; transcript recorded; A2 filed; no scope expansion; closeout produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S007: PASS
Implementation Summary: R10 PASS — done in 1 workflow attempt, PR #3, receipt
  dd4cca06…; kill landed mid-turn via prompt-file trigger; resume kept epoch e1.
  Findings fixed in-tree: single tested result, kill trigger, receipt digest,
  unique tenant per run, hardened wait loop, corrected asserts.
Files Changed: rouvr/worker.py, scripts/r10_rehearsal.sh, docs/r10-runs/20261029T175518/*,
  docs/a2-gates.md + rehearsal fixes across activities/config/host/starter/client/ledger/lease
Tests Run: rehearsal PASS; 126 passed + 2 skipped; hygiene clean (1961); gitleaks clean
Non-Happy-Path Tests: 3 failed runs each root-caused (verdict 409, id reuse, receipt stub)
Known Gaps: A2 gates 1–4 need humans (grant, credential, stage run, back-port).
```

## 12. Task Completion Reports

- Task 7.1: Done. Evidence: transcript + workflow result + history. Next: 7.2.
- Task 7.2: Done. Evidence: a2-gates.md. Next: none (plan complete).
