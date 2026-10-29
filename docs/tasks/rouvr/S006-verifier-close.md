# Sprint S006 — R9 verifier + close

## Sprint ID

`S006`

## Sprint Title

Independent verification and system-only close with receipt

## 1. Objective

Close the loop with a different principal: gate the submission, run the
verifier turn on criteria + diff + R8 evidence (never the builder report),
then transition to done/blocked through legal hops with CAS + readback, and
return a receipt digest linking attempt, run, session, PR, and tests.

## 2. Roadmap Mapping

```text
Roadmap sections: Step 11 (R9)
Related components: rouvr/activities.py (verify, close_task, mark_blocked,
  sign_receipt), rouvr/gitops.py (pr_facts, ci_status), rouvr/muse.py
  (verifier prompt, verdict transport)
```

## 3. In Scope

- `verify` (SELF_REVIEW re-check, gate first, verifier turn, fail-closed verdict)
- `_close_to` state walker; `pr_facts`/`ci_status`; `verdict.json` transport
- `sign_receipt` unsigned digest (ed25519 stays P2, marked shortcut)

## 4. Out of Scope

- Rehearsal + recording (S007); ed25519 receipt signing (P2)

## 5. Target Files

```text
rouvr/activities.py, rouvr/gitops.py, rouvr/muse.py, rouvr/workhub/ledger.py
tests/test_r9_close.py
```

## 6. Tasks

### Task 6.1 — Verifier (IMPLEMENTATION)
- Scope: verify activity, pr_facts, ci_status, verifier prompt, read_verdict.
- AC: gate-blocked/failed → reject; gate-reject → request_changes with the
  one-line feedback; gate-approved → verifier turn; unreadable verdict →
  reject (never approve); report attached under verifier identity.
- Non-happy proof: malformed/missing verdict.json; empty-checks CI reads success.
- Evidence: `test_r9_close.py` (CI map, verdict transport, prompt).

### Task 6.2 — Close + receipt (IMPLEMENTATION)
- Scope: _close_to walker, ledger transition_batch, sign_receipt.
- AC: close walks legal hops with per-hop CAS (stage + ledger maps);
  already-there is success; no legal hop raises; receipt digest is
  deterministic and covers all eleven links.
- Non-happy proof: done→blocked refused; non-convergence raises.
- Evidence: `test_r9_close.py` (walker + receipt tests).

## 7. Acceptance Criteria

```text
AC-1: Gate-rejected submission yields request_changes carrying the gate feedback.
AC-2: Missing or malformed verdict.json yields reject, never approve.
AC-3: queued→done closes via running→needs_review→done with CAS per hop.
AC-4: Receipt digest changes when any link changes; stable otherwise.
```

## 8. Tests Required

- Unit: `tests/test_r9_close.py` (7 tests: CI, verdict, prompt, walker ×3, receipt)
- Non-happy: verdict garbage; illegal hop; non-convergence

## 9. Evidence Required

Pytest results, files-changed list.

## 10. Definition of Done

ACs met; verifier fail-closed proven; no scope expansion; closeout produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S006: PASS
Implementation Summary: verifier + walker-close + receipt digest complete;
  verifier identity separated at the claim; close proven on both state maps.
Files Changed: rouvr/activities.py, rouvr/gitops.py, rouvr/muse.py,
  rouvr/workhub/ledger.py, tests/test_r9_close.py
Tests Run: 7 R9 tests pass
Non-Happy-Path Tests: verdict garbage → reject; illegal hop; non-convergence
Known Gaps: ed25519 receipt signing deferred to P2 (shortcut marker in code).
```

## 12. Task Completion Reports

- Task 6.1: Done. Evidence: verifier tests green. Next: 6.2.
- Task 6.2: Done. Evidence: walker + receipt tests green. Next: S007.
