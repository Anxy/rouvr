# Concepts

Rouvr's flow (from [rouvr.ai](https://rouvr.ai)): **Think together → Agree what happens → Put your pack to work**, and a delivery journey of *Agree the goal → Do the work → Check and repair → Deliver → Keep it running*.

## Contract
A messy instruction is compiled (by a model, or the stub) into a typed contract: steps, success criteria, side effects, stakeholders, escalation and expiry. The ledger stores it with a content hash; the owner approves that exact hash once, as a bounded autonomy grant.

## Evidence
Closeout needs evidence the *system* records, not the agent's say-so: commit SHA and CI result, provider message id plus readback, health probes, figures re-queried from source. Evidence is append-only.

## Independent verification
The principal that produced a result can never accept it. A separate verifier (deterministic checks, and optionally a model as judge) reads outputs back and returns accept / changes_required / escalate.

## Receipts and idempotency
Every side effect carries an idempotency key and produces a receipt. Re-running a step with the same key returns the existing receipt instead of repeating the side effect; this is how the demo survives a mid-step kill without sending twice.

## Recovery and fail-closed
A missing or revoked key, stale lease or unverifiable side effect parks the task as `blocked` and escalates. Silent delivery failures are caught at readback and retried through a fallback. Nothing silently succeeds.

## "Done. No action needed."
Once a contract has run cleanly and is authorised to recur, the daily report becomes exception-only.
