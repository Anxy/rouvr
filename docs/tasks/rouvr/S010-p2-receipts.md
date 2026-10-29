# Sprint S010 — P2 receipt signing

## Sprint ID

`S010`

## Sprint Title

Signed receipts (ed25519) replacing the unsigned digest

## 1. Objective

Replace the S006 unsigned receipt digest with ed25519 signing: task close
returns a signature over the same 11-link payload, verifiable against the
published public key, with key rotation and 0600 key-file enforcement.

## 2. Roadmap Mapping

```text
Roadmap sections: P2; Master §6; closeout shortcut in rouvr/activities.py
Related components: rouvr/activities.py (sign_receipt), rouvr/config.py,
  rouvr/starter.py, rouvr/workflows.py (result receipt)
```

## 3. In Scope

- ed25519 sign/verify (existing dependency only — no new packages)
- Key handling: `receipt_key_path`/`receipt_key_id`, 0600 enforcement, rotation
- `sign_receipt` returns `{digest, signature, key_id}`; workflow result carries it
- Verification helper + tests; shortcut comment removed

## 4. Out of Scope

- Stage promotion (S008); any change to the 11-link payload; pool work

## 5. Target Files

```text
rouvr/activities.py, rouvr/config.py, rouvr/workflows.py, rouvr/starter.py, rouvr/receipts.py
tests/test_r9_close.py (receipt tests), tests/test_receipts.py, tests/test_sprint_ledger.py untouched
Amendment 2026-10-29: rouvr/receipts.py + tests/test_receipts.py approved in scope (supervisor cycle 1 change_required).
```

## 6. Tasks

### Task 10.1 — Signing + verification (IMPLEMENTATION)
- Scope: files in §5 only.
- AC: receipt verifies against the public key; wrong key fails; digest
  coverage (11 links) unchanged from the unsigned version; key file with
  mode > 0600 is refused before use; unsigned mode is gone (no flag day).
- Tests-first: extend receipt tests (verify, wrong-key, loose-perms cases)
  before/alongside the implementation.
- Happy proof: deterministic digest + valid signature for fixed inputs.
- Non-happy proof: tampered payload fails verification; missing key file
  raises; loose-perms key file raises.
- Evidence: pytest output for the receipt tests.

### Task 10.2 — Rotation + operator path (IMPLEMENTATION)
- Scope: config + docs only (settings section already exists).
- AC: key rotation (new key_id) keeps old receipts verifiable; operator
  docs name the key path/rotation procedure; hygiene stays clean.
- Evidence: config docs diff; rotation test.

## 7. Acceptance Criteria

```text
AC-1: Given a receipt, verification against the public key passes.
AC-2: Given a corrupted receipt or wrong key, verification fails (no exception-swallowing).
AC-3: Given a key file readable beyond owner, signing refuses before use.
AC-4: Given a rotated key, receipts signed by the old key still verify.
```

## 8. Tests Required

- Unit: receipt sign/verify, wrong-key, tamper, loose-perms, rotation
- Regression: full public suite + hygiene + gitleaks before closeout
- Non-happy: tamper, missing key, loose perms (all fail closed)

## 9. Evidence Required

Pytest results, files-changed list, config/docs diff.

## 10. Definition of Done

ACs met; shortcut comment removed; no scope expansion; closeout produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S010: (open; first claimable sprint)
```

## 12. Task Completion Reports

- Task 10.1: Pending. Next: 10.2.
- Task 10.2: Pending. Next: none queued after this (S009 waits on S008).
