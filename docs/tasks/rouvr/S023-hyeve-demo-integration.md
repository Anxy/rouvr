# Sprint S023 — Hyeve demo integration (auth + comms seams)

## Sprint ID

`S023`

## Sprint Title

Replicate the Hawks Hyeve consumption pattern for the demo: auth identity
seam plus thin comms client with file fallback; live legs blocked on creds

## 1. Objective

Give the demo a Hyeve-shaped delivery path without touching live services.
Auth reuses the existing service-token shape against the VPS1 auth origin
once credentials arrive. Comms goes through a thin client that mirrors the
Hawks header merge plus idempotency, with the local file outbox kept as the
offline fallback. Provider status readback replaces file readback on the
live leg. Everything fails closed: missing scope, missing identity, or a
missing message id parks the leg instead of succeeding silently.

Mint is validated live with the provisioned service credentials.
Send/readback and all comms wiring stay parked until the demo WorkHub
project exists (deployment gate).

## 2. Roadmap Mapping

```text
Roadmap sections: user direct order 2026-11-01 (Hyeve demo integration);
  extends the v1.3 decomposer contract (roadmap.json R7-R10/T7-T10).
Related components: rouvr/hyeve.py (new: header merge, scope-required,
  delivery interface plus file backend), tests/test_hyeve_demo.py (new),
  roadmap.json (R7-R10 + tasks + coverage_ledger + dropped_content),
  rouvr/workhub/client.py TokenSource (reused, unchanged),
  demo/morning_report.py (unchanged; consumes the seam later),
  docs/tasks/rouvr/S023-hyeve-demo-integration.md (this sheet)
```

## 3. In Scope

- `merge_headers`: strip incoming identity headers case-insensitively,
  require tenant, set fresh bearer plus tenant id, keep or mint an
  idempotency key.
- `require_scope`: tenant plus project mandatory, no defaults anywhere.
- `FileBackend`: deterministic message ids, write-once delivery, readback
  returns text or `None` (absent is never success).
- Live tests for mint plus parked send, gated on explicit env plus a
  credential file; without them they skip.
- Contract extension: R7-R10, T7-T10, coverage both directions, dropped
  list entries for every cut.

## 4. Out of Scope

- Live calls beyond mint/validate (comms parked until the demo project
  exists on WorkHub).
- Full Hyeve parity: demo needs auth plus comms only.
- Frontend auth reuse: backend demo path only.
- Per-service URL knobs: single-origin derivation only.
- Secrets in the repo: credential shapes only, never values.

## 5. Target Files

```text
rouvr/hyeve.py (new), tests/test_hyeve_demo.py (new), roadmap.json,
docs/tasks/rouvr/S023-hyeve-demo-integration.md (this sheet)
```

## 6. Tasks

### Task 23.1 — Pure seam plus passing tests

- AC: header merge, scope-required, and file-backend roundtrip pass
  offline; live tests skip without env and run with it.
- Tests-first: `tests/test_hyeve_demo.py` is the test.

### Task 23.2 — Contract extension (R7-R10/T7-T10)

- AC: roadmap validates structurally (parse, overlap, coverage); the
  completion gate stays red only on the live tasks until creds land.
- Tests-first: validator runs in both modes are the evidence.

### Task 23.3 — Live legs

- AC: mint validated live (both demo identities, HTTP 200) with
  provisioned credentials; send/readback parked until the demo project
  exists on WorkHub (deployment gate); live legs gated on explicit env,
  never defaults.
- Status 2026-11-01: mint plus token validation green for both
  identities through the existing lite client. Credential files are
  installed locally at mode 0600, git-ignored, never logged. The
  tenant sees zero projects, so send/readback stays parked on the
  deployment gate with an asserting test.
