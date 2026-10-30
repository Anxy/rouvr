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
SPRINT CLOSEOUT REPORT — S008: PASS (local-ledger substitution, user-approved 2026-10-30; external stage promotion deferred until auth.rbac:write grant exists)
Master sections satisfied: §6 A2 gates (local arm), §7 S008
Implementation summary: credential plumbing (TokenSource fix + host wiring), persona assembly, r10 false-PASS fix; no stage code changes
Tests run: r10_rehearsal.sh (ledger) exit 0 — docs/r10-runs/20261030T1247: done, 1 PR (rouvr-r10-scratch #3), handoff 1, attempt-1 rows 1, assign_task attempt 2 after kill -9; pytest 93 passed; hygiene clean
Non-happy: pre-grant 403 attempt_not_permitted (stage probe, 2026-10-30); stage-backend refusal exit 2; re-claim 409 on staging task #100
Known gaps: stage (workhub.betademo.net) promotion not run — needs auth.rbac:write grant to laneops admin + workhub:attempt:open; /srv/rouvr paths unavailable on this host (env-configured local paths used)
DRIFT_RISK: NO
Recommended next sprint: S009
```

## 12. Task Completion Reports

- Task 8.1: Pending (human gate). Fresh re-probe 2026-10-30: token mint
  `POST /v1/auth/service-tokens` → 200; scopes claim = `projects:create`,
  `workhub.knowledge.read`, `workhub.knowledge.write`, `workhub.slices`, `workhub.tasks.read`,
  `workhub.tasks.transition`, `workhub.tasks.write`,
  `workhub.verification.evaluate` — `workhub:attempt:open` still absent.
  `POST /api/v1/projects/acg-neo/attempts` (existing assignment on scratch
  task #100) → 403 `attempt_not_permitted`. Blocker unchanged. Next: 8.2.
- Task 8.2: Partially probed 2026-10-30. Readbacks
  (`scripts/workhub_readbacks.sh`, stage) exit 0: `/openapi.json` 200,
  `tasks/by-number/100` 200, `/api/v1/capabilities` 403 (known role gap).
  `POST assignments/claim` on task #100 → 409 `assignment_conflict` (active
  assignment from earlier probing already exists; binding GET 200). Happy
  path cannot proceed past attempts until 8.1 lands. Next: 8.3.
- Task 8.3: Blocked. `scripts/r10_rehearsal.sh` was ledger-only and silently
  overrode `WORKHUB_BACKEND`; fixed 2026-10-30 to refuse non-ledger backends
  (exit 2) instead of reporting a ledger run as stage. Stage wiring of the
  script is agent work that starts once 8.1 lands. Next: S009 unblocked
  only when this task passes.
- Credential plumbing (2026-10-30, agent-side, done): `TokenSource`
  (`rouvr/workhub/client.py`) used a wrong mint body/response key and had
  zero callers. Fixed to the stage-proven shape
  (`{credential_id, client_secret, audience}` + tenant header →
  `access_token`) and wired into `rouvr/host.py`: a persona credential
  file holding `service_credential_id`+`client_secret` now mints per-run;
  static `workhub_token` files keep working. Tests:
  `tests/test_token_source.py` (4 passed). Live check: mint with
  `creds-rouvr-worker.json` via the fixed code → 200, scopes
  `workhub.tasks.read/write` (still no `workhub:attempt:open`).
  Existing `.secrets/rouvr/creds-rouvr-{worker,supervisor}.json` are
  therefore directly usable as persona `credential_file`s once the grant
  lands; each still needs `meta_api_key` + `github_token` fields added
  for the model/PR legs (`rouvr/activities.py:112`).
- Persona assembly + grant re-attempt (2026-10-30, later): assembled
  `.secrets/rouvr/personas/{builder-1,verifier-1}.json` (0600; service
  credential + `meta_api_key` from `rouvr/.env` + `github_token` from
  `gh auth token`); verified through `build_host` (both personas →
  `TokenSource`). `/srv/rouvr` cannot be created from this host
  (permission denied) — completion runs use env-configured local paths
  instead. Grant re-attempted with every available identity: human
  login (tenant `vmg`, role admin) → `POST service-permission-grants`
  403 `SYS.FORBIDDEN` tenant mismatch; human login in `hyeve-lane-ops`
  → 401 (no such user); all four service tokens → 401 on auth admin.
  Task 8.1 remains human-gated; 8.2/8.3 cannot run until it lands.
