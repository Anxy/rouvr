# Sprint S018 — Governed ops + story gaps (T3/T4/T5)

## Sprint ID

`S018`

## Sprint Title

OpsTaskWorkflow, Meta contract compiler + judge, standing grants

## 1. Objective

Implement the story gaps behind the loop-integrity batch (S017 preserved):
morning_report as OpsTaskWorkflow with run_key-idempotent activities (T3),
model output validated against a contract schema + separate-principal judge
evidence (T4), standing grants table + `rouvr_schedule` gate + exception-only
digest (T5).

## 2. Roadmap Mapping

```text
Roadmap sections: review G3/G4/G5; rouvr-roadmap P2; MASTER §§4,9
Related components: demo/morning_report.py, rouvr/workflows.py (new
  OpsTaskWorkflow), rouvr/contracts.py, ledger schema, rouvr/mcp_server.py
```

## 3. Acceptance tests (from the review)

- T3: kill mid-deliver → exactly 1 outbox message + done; silent_delivery →
  fallback id in receipt; revoked_key → blocked pre-claim + escalation row.
- T4: invalid model JSON → contract refused (not merged); judge evidence
  principal ≠ executor; one live Meta API run recorded by the owner.
- T5: schedule with no grant → refused; tick → one child, dup tick → no
  second child; clean run → digest "Done. No action needed."

## 4. Slice-entity decision (review G6)

Tasks are the slices. There is no separate slice entity in the ledger
schema and none is planned: each task carries acceptance criteria and its
rework state is visible in task state (`open → claimed → needs_review →
done/blocked`), not in a `changes_required` state (request_changes loops to
the coder). This sheet records the decision so G6 stays closed.
