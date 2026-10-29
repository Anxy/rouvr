# Rouvr implementation plan (the `go` run, 29 Oct 2026)

Source of truth: `docs/rouvr-roadmap.md` (R1–R10, A1–A2, P2). Each step below
is the plan item plus its recorded outcome. Overall result: R10 rehearsal
PASS, transcript in `docs/r10-runs/20261029T175518/`; A2 gated on humans
(see `docs/a2-gates.md`).

## Step 1 — Refs + OpenAPI/Temporal confirmation
Fetch `hyeve-lane` and `hyeve-workhub-service` read-only; confirm router
paths and local Temporal reachability.
Outcome: done. Pins `d2e39d8` / `9798926`; local stack up.

## Step 2 — R1 hygiene scrub
Remove private-tenant strings without history rewrite; add
`scripts/hygiene.sh` + allowlist + CI `hygiene` job + gitleaks + history note.
Outcome: done. Follow-up: review-001 caught a hardcoded Llama endpoint/model
in the meta provider preset — fixed to env-only, gate widened with
`llama\.com|Maverick`, `.gitleaksignore` for 12 test-placeholder false
positives. Gitleaks clean on tree + history.

## Step 3 — R2 resume spike
`scripts/spike_resume.sh` (live model): kill a turn mid-run, resume by
session id. Verdict RESUME or RESPAWN.
Outcome: done. Verdict RESUME; `docs/spikes/muse-resume.md`; default
`RESUME_SUPPORTED=true`.

## Step 4 — A1 WorkHub fixes + staging happy path
Confirm §0.4 routes code-side; file fix list; run readbacks on staging.
Outcome: done. `docs/workhub-fixes.md` (3 FIX items); staging later proved
reachable — routes/bodies live-probed, readbacks green, attempt-scope 403
filed as the A2 gate.

## Step 5 — R3 package base + ledger parity
New `rouvr/` package (config/errors/ids/contracts, workhub routes/port/
client/ledger); ledger parity tables/endpoints; contract suite green on the
ledger arm.
Outcome: done. Public suite green at each step (65 → 126 by the end).

## Step 6 — R4 TaskWorkflow + endpoint + CLI
`workflows.py` + `starter.py` + `api.py` + thin activities; `rouvr start`
CLI; live double-start probe `(id, True)` then `(id, False)`.
Outcome: done. Fixed SDK hang via return annotations on the Activities
interface.

## Step 7 — R5 lease/fencing + reclaim
`lease.py`, `host.py`, `_wrap`, `assign_task` ownership skeleton; renew-first
retry (`resume_or_claim`); ledger `expires_at` handling.
Outcome: done. Re-claim 409s even for the holder (live-probed), so retry
renews when live, else reclaims + claims on the same attempt.

## Step 8 — R6 personas + execspec + launcher + gitops
`personas.py` (AIX mapping source, 0600 creds), `execspec.py`, `muse.py`
(real envelope), `gitops.py`, `_run_builder_turn`, `verify_binding`.
Outcome: done. Live launcher turn `launcher-ok`; `META_API_KEY` honored
(`MUSE_API_KEY` ignored); model `""` = CLI default.

## Step 9 — R7 submit gate
`gate.py` (`submit_result`, `evaluate()` → approved/reject/blocked/failed);
file transport `submit.json` (headless MCP unverified).
Outcome: done. Later fix: builder claims criteria only; R8 refs are
system-known (the builder submits before tests run).

## Step 10 — R8a hidden tests + R8 runner + cheat-scan
`hiddentests.py` (lock/hash/fetch/clean-check/literals/criterion parse),
`testrunner.py` (+ `ContainerTestRunner`), `cheatscan.py`, `run_tests`
activity (fresh clone at PR SHA, hidden + repo runs, system evidence).
Outcome: done. `testrunner.Dockerfile` (pytest baked into a pinned image;
stock slim has none and the container is offline); runner live-probed.

## Step 11 — R9 verifier + close
`verify` (gate + verifier turn under verifier identity, fail-closed),
`close_task`/`mark_blocked` (CAS + status-walk + readback), `pr_facts`,
`verdict.json` transport.
Outcome: done. Live bindings name no personas (single `persona` field), so
the pair comes from tenant roles with the same SELF_REVIEW check.

## Step 12 — R10 rehearsal + record, then A2
`rouvr/worker.py` (per-queue processes), `scripts/r10_rehearsal.sh`
(seed → start → kill -9 mid-turn → restart → assert → redacted
transcript): done + 1 PR + activity retry on attempt 1.
Outcome: PASS (see the run transcript). Rehearsal findings fixed in-tree:
one result per attempt, prompt-file kill trigger, `sign_receipt` minimal
digest (ed25519 stays P2), unique tenant per run. A2: scope grant +
verifier credential + stage R10 + back-port — human-gated.
