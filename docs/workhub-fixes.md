# A1: WorkHub small-fix list (code-verified 29 Oct 2026, live-probed same day)

Sources: `hyeve-workhub-service @ 9798926`, `hyeve-lane @ d2e39d8`
(fetched read-only; line numbers are at those pins).
Staging IS reachable from this host (`https://workhub.betademo.net`, service
JWT via `lane/tools/wh_direct.py:service_login`). OpenAPI snapshot:
289 paths, `Hyeve_workhub_service 1.0.0`.

## LIVE-CONFIRMED (29 Oct 2026, tenant hyeve-lane-ops / project acg-neo)

- §0.4 routes (OpenAPI + probes): `tasks/by-number` GET,
  `tasks/by-number/{n}/assignment-binding` GET, `tasks/{id}/evidence`
  GET+POST, `tasks/transitions/batch` POST, `capabilities` GET,
  `assignments/claim`, `attempts`, `results`, `leases/claim`,
  `leases/renew`, `reclaim-dead-assignments` POST — all exist.
- Bodies that differ from first guesses (client realigned):
  claim `{task_id, persona, plan_step}` (holder is auth-bound, not a field);
  lease claim/renew `{task_id, ttl_seconds}` / `{task_id, expected_epoch,
  ttl_seconds}` (key is `epoch`, not `lease_epoch`); attempts
  `{task_id, assignment_id}`; results `{task_id, attempt_id,
  artifact_digest, evidence_id?}`; evidence `{evidence_type,
  evidence_ref, evidence_json}` with the ref echoed inside `evidence_json`;
  batch items `{task_id, status, expected_record_version, reason}` (key is
  `status`, not `to_state`); reclaim `{executor_sub,
  heartbeat_stale_after_s}` (`executor_sub` required).
- Task reads: v1 by-number and v2 `tasks/{id}` both carry `record_version`;
  neither has `contract_rev` — canonical `contract_rev` is the
  `record_version` (claims/evidence don't bump it; verified on task #100).
  Criteria are top-level `acceptance_criteria` (free strings; ids positional).
- Close walks legal hops (`/api/v1/tasks/transitions/help`):
  queued→running→needs_review→done; every non-terminal state →blocked.
- Binding has ONE `persona` field (no builder/verifier pair) plus
  `expected_lease_epoch`/`holding_principal` (null when the lease is dead).
  Builder/verifier come from the tenant persona registry, not the binding.
- Re-claim while an assignment is active → 409 even for the holder: retry
  must renew-first (`resume_or_claim`); expired lease → 422 `lease_expired`
  (mapped to E_MOVED).
- Scope gap (A2 gate): this credential's scopes are `tasks.read/write/
  transition`, `verification.evaluate`, knowledge, slices — `attempts` needs
  `workhub:attempt:open` → 403 `attempt_not_permitted`. `capabilities` GET →
  403 `forbidden` (role). R10 rehearses on the local ledger until the grant.

## CONFIRMED (code; live check pending)

- §0.4 routes, all present in `workhub_contract_router.py`, prefix `/api/v1`
  (`APIRouter(prefix="/api/v1")`, router:83; resolves C5 code-side):
  `assignments/claim` :5297, `attempts` :5341, `results` :5376,
  `assignments/reclaim` :5993, `leases/claim` :6910, `leases/renew` :6941,
  `reclaim-dead-assignments` :7090, `assignment-liveness/renew` :7351.
  Service methods at `claim_assignment` :17875, `submit_result` :20104,
  `reclaim_assignment` :23720, `renew_workspace_lease` :29738,
  `reclaim_dead_assignments` :30047, `renew_assignment_liveness` :31464.
- Error → retryable map. The service raises one `ServiceError` (1,176 raises),
  mapped at the router. Status spread: 400/401/403/404/409/410/422 =
  non-retryable; 429/500/502/503 = retryable with backoff. Matches §Retries.

## FIX (small, in the current service)

1. **Atomic create+bind.** `POST .../assignments/bind-route` (:5568, svc
   :19194) takes an existing `assignment_id` + `expected_assignment_revision`
   — it binds a route to a claimed assignment, it does not create one. Need a
   single idempotent create-and-assign (or written confirmation that
   claim → bind with idempotency keys is acceptable for the demo).
2. **Attempt session/run/PR fields.** The `attempts` DDL
   (`database/scripts/create_schema.sql`) has no `session_id`,
   `workflow_run_id`, `pr_url`, or metadata column, and no `session_*` tables
   exist. Either add the columns (preferred) or bless session-binding evidence
   rows as the store — decide on staging.
3. **Builder ≠ verifier at claim.** `claim_assignment` (:17875–18030) records
   `builder_sub` but compares nothing; the only separation check is
   session-based in hyeve-lane `route_task` (workflows.py:2680:
   `builder_session == reviewer_session` → `self-review-refused` park). Need
   claim-time principal comparison returning 403/409.

## DIAGNOSED-PARTIAL (needs live repro)

- **`preclaim-task-read-refused`.** The canary fails in `execute_task`
  (`aix-lane/tools/canary_lane.py`, `WH.get_task` → `refusal_reason`
  → fail at :8358). `refusal_reason` (`wh_direct.py:167`) fires on any HTTP
  ≥300 or `success:false` envelope. The by-number read
  (`:9579`) requires `require_task_read(principal)` plus
  `require_task_project_access(...)`. So the canary principal fails a grant
  check on a plain task read. Next step on staging: replay the exact GET with
  the canary JWT, capture the refusal code, then either grant the principal
  (config) or fix the check (code). Temporal will park identically until this
  is resolved.

## Pending-staging checklist

- [x] OpenAPI snapshot (289 paths) + task/binding/evidence/transition probes 2xx
- [ ] Ordered happy path: claim → attempt → lease renew → result, all 2xx
      (attempts blocked on the `workhub:attempt:open` grant — A2 gate)
- [x] Flip CONFIRMED items to LIVE-CONFIRMED; file FIX items 1–3
