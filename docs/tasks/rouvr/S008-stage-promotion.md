# Sprint S008 — Stage promotion (A2, human-gated)

## Sprint ID

`S008`

## Sprint Title

Promote R10 from the local ledger to real staging

## 1. Objective

Run the same rehearsal against staging (`WORKHUB_BACKEND=stage`) with a
scratch staging task and two real credentials, proving the claim →
attempt → verify → close loop on the served WorkHub.

## 2. Roadmap Mapping

```text
Roadmap sections: A2 gates 1–3 (docs/a2-gates.md); S007 A2
Related components: rouvr/workhub/client.py, rouvr/worker.py,
  scripts/r10_rehearsal.sh, rouvr/config.py, rouvr/host.py
```

## 3. In Scope

- `workhub:attempt:open` scope grant for the Rouvr service credential
- Second bot credential (verifier persona) + 0600 credential files
- Stage operands: scratch task, execspec, hidden suite on staging project
- Stage readbacks + claim→attempt→renew→result happy path
- Stage R10 rehearsal: done + 1 PR + same-attempt retry, transcript

## 4. Out of Scope

- Back-port (S009); WorkHub FIX items (human/WorkHub team); receipt signing (S010)
- Any new feature — promotion only, behavior must not change

## 5. Target Files

```text
docs/a2-gates.md, scripts/r10_rehearsal.sh, scripts/workhub_readbacks.sh
/srv/rouvr/personas/* (deploy target), /srv/rouvr/specs/* (deploy target)
docs/r10-runs/<stage-ts>/
```

## 6. Tasks

### Task 8.1 — Grants + credentials (PLANNING)
- Scope: this sheet + `docs/a2-gates.md` updates only. No code.
- AC: grant recorded (who, when, scope string); verifier credential minted
  into a 0600 file; builder principal ≠ verifier principal (sub-level).
- Evidence: grant reference; `test -f` + perms check (never file contents).
- Non-happy proof: `attempts` POST before the grant returns the captured 403
  (`attempt_not_permitted`) — documented, not silently retried.

### Task 8.2 — Stage operands + happy path (VERIFICATION)
- Scope: deploy config + scratch staging task; no code changes.
- AC: readbacks suite exits 0 on staging; ordered claim→attempt→lease
  renew→result all 2xx; binding read shows the builder persona as holder.
- Evidence: readback output; ordered curl transcript (status codes only).
- Non-happy proof: re-claim while held → 409 (correct); stale-renew → 422
  `lease_expired` mapped to MOVED.

### Task 8.3 — Stage R10 rehearsal (VERIFICATION)
- Scope: `scripts/r10_rehearsal.sh` run with `WORKHUB_BACKEND=stage`.
- AC: script exits 0; task done; exactly 1 open PR; assign_task completes
  on activity attempt ≥ 2 (kill/resume proven on stage); transcript recorded
  under `docs/r10-runs/` with zero unredacted tokens.
- Evidence: workflow result, Temporal history dump, redaction sweep output.
- Non-happy proof: the kill lands mid-turn (prompt-file trigger), not after.

## 7. Acceptance Criteria

```text
AC-1: Given the grant, POST attempts with the Rouvr credential returns 2xx.
AC-2: Given staging operands, rouvr start walks queued→running→needs_review→done.
AC-3: Given a mid-turn kill, the same attempt resumes (handoff rows stay 1).
AC-4: Given two credentials, verifier writes are never accepted from the builder principal.
```

## 8. Tests Required

- Live: readbacks suite; ordered happy-path curls; stage rehearsal script
- Regression: full public suite + hygiene + gitleaks before closeout
- Non-happy: pre-grant 403 capture; re-claim 409; stale-renew 422

## 9. Evidence Required

Grant reference, readback output, rehearsal transcript, pr_url, receipt.

## 10. Definition of Done

ACs met; transcript recorded; INDEX regenerated; no scope expansion.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S008: (open)
```

## 12. Task Completion Reports

- Task 8.1: Pending (human gate). Next: 8.2.
- Task 8.2: Pending. Next: 8.3.
- Task 8.3: Pending. Next: S009 unblocked.
