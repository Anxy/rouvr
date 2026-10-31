# Sprint S017 — Loop-integrity fixes (review-004 item 4 + T1/T2/T6/T7-small)

## Sprint ID

`S017`

## Sprint Title

Block path, hidden-suite authoring, reviewer clone, scheduled ids, hygiene

## 1. Objective

Close the loop-integrity gaps that break the live demo: clean block with
escalation receipt (T1), hidden suite written at task creation by a distinct
test_author principal (T2), reviewer diff from a fresh clone at head_sha (T6),
per-tick scheduled child ids after `workflow_id` stopped hashing
contract_rev (review-004 item 4), and `mcp` in requirements (T7-small).
T3/T4/T5 (ops workflow, Meta judge, grants) are queued in S018, not here.

## 2. Roadmap Mapping

```text
Roadmap sections: review G1/G2/G7/G8; rouvr-roadmap R4/R8a/R9; MASTER §§4,9
Related components: rouvr/workflows.py (_block, ScheduledRunWorkflow),
  rouvr/activities.py (escalate, verify), rouvr/mcp_server.py
  (workhub_create_task, waiting_on_supervisor), rouvr/ids.py,
  rouvr/hiddentests.py, requirements.txt
```

## 3. In Scope

- `_block`: escalate on a single-attempt policy; swallow escalate failure,
  still return `blocked`.
- `waiting_on_supervisor`: blocked tasks list the escalation reason when an
  escalation evidence row exists.
- `workhub_create_task`: write the hidden suite with `created_by=test_author`
  under `HIDDEN_TESTS_ROOT/<id>/`; coder workspace never touched.
- `verify`: diff from `clone_at(head_sha)`, not the coder `ws`.
- Scheduled children: per-tick id distinct from the one-task-one-workflow id.
- `.env.example` comment fix (ROUVR_TENANT_ID keeps a default); `mcp` added
  to requirements.txt; slice-entity decision documented (tasks are the slices).

## 4. Out of Scope

- T3 OpsTaskWorkflow, T4 Meta judge, T5 grants/schedule tool (S018).
- Secrets handling, containerising the coder (risks, noted not fixed).

## 5. Target Files

```text
rouvr/rouvr/workflows.py, rouvr/rouvr/activities.py, rouvr/rouvr/mcp_server.py,
rouvr/rouvr/ids.py, rouvr/rouvr/starter.py, rouvr/requirements.txt,
rouvr/.env.example, rouvr/tests/test_unit.py, rouvr/tests/test_workflow.py,
rouvr/tests/test_r9_close.py, rouvr/tests/test_mcp_server.py
```

## 6. Tasks

### Task 17.1 — Single-attempt escalate + fail-closed block (T1)

- Scope: `workflows._block` escalate call only.
- AC: rework_exhausted with the REAL escalate returns `{"state":"blocked"}`;
  ledger shows state=blocked + escalation row + task in waiting_on_supervisor;
  escalate failure still returns blocked (no failed workflow).
- Tests-first: workflow test with failing-escalate stub returns blocked;
  `test_r9_close` real-escalate test kept green.

### Task 17.2 — Test-author step on create (T2)

- Scope: `mcp_server.workhub_create_task` + hidden-suite write only.
- AC: MCP create → `HIDDEN_TESTS_ROOT/<id>/MANIFEST.json` with
  `created_by=test_author`; `assert_coder_clean` passes; criteria-derived
  test names embed `test_c<n>_...`.
- Tests-first: `test_mcp_server` create asserts manifest + created_by.

### Task 17.3 — Reviewer diff from fresh clone (T6)

- Scope: `activities.verify` diff source only.
- AC: mutating the coder ws after submit does not change the reviewer diff
  (diff computed in `clone_at(head_sha)`).
- Tests-first: existing verify tests green; structural (clone then diff).

### Task 17.4 — Scheduled per-tick child ids (review-004 item 4)

- Scope: `ScheduledRunWorkflow.run` child-id construction + docstring.
- AC: two ticks (and tick vs direct start) yield distinct ids; one task +
  one contract rev still yields one TaskWorkflow id.
- Tests-first: regression test in `tests/test_unit.py`.

### Task 17.5 — Hygiene small (T7-small)

- Scope: requirements + `.env.example` comment + slice-entity note only.
- AC: `mcp` importable from requirements; comment no longer claims
  ROUVR_TENANT_ID has no default; tasks-are-slices documented in S018.
- Tests-first: n/a (config/text); validation = `test-public` subset green.
