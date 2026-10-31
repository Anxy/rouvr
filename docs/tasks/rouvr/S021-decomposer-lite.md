# Sprint S021 — Decomposer-lite (task creation calls the decomposer)

## Sprint ID

`S021`

## Sprint Title

Brief in, verifiable tasks out: mini-language, validator dogfood, run gate, parent check, demo

## 1. Objective

Give `workhub_create_task` a creation-time decomposer: a raw brief goes
in, enriched tasks come out (requirements with source refs, tasks with
allowed/pinned paths and mini-language verifiers, coverage ledger,
dropped list) — before any task is pickable. The machine catches bad or
fake work: hollow verifiers rejected, pinned edits fail, non-composing
splits flagged. Dogfood: `roadmap.json` is itself written in the v1.3
shape and must pass the validator.

## 2. Roadmap Mapping

```text
Roadmap sections: user direct order 2026-10-31 (Decomposer-lite brief);
  extends TaskWorkflow creation path (S017 T2, S018 T5).
Related components (new): rouvr/decompose/ (validator + mini-language
  runner), roadmap.json (S021 contract), demo/decompose-demo.sh,
  tests/test_decompose.py, rouvr/mcp_server.py (create-task hook),
  docs/tasks/rouvr/S021-decomposer-lite.md (this sheet)
```

## 3. In Scope

- STEP 1: `roadmap.json` (R1–R6 + tasks + coverage_ledger +
  dropped_content, §4.8 shape); `rouvr/decompose/validator.py`
  (parse 4 primitives; 5 rejection rules: unparsable, non-assertion
  fail, 3-run disagreement, path overlap, uncovered requirement);
  dogfood run on roadmap.json, fix static rejections now.
- STEP 2: run gate (`verifier` + `no_changes_outside`, pinned touch
  fails); parent check (`DECOMPOSITION_FAILURE` on green-children /
  red-parent); `decompose` model call (one candidate) + integration
  into `workhub_create_task` (enrich before pickable); 3-minute demo
  script (good split, hollow rejected, pinned edit fails,
  non-composing split).
- Mini-language: exactly `test_passes`, `no_changes_outside`,
  `path_absent`, `and`. Anything else is rejected.
- `bundle_sha` = sha256 of the test file at run time; mismatch is a
  stale bundle (ERROR class, never a pass).

## 4. Out of Scope (per brief, not skipped silently)

- N=3 search (one candidate only), adversarial sampling, http/schema
  primitives, leases/authority, UI beyond CLI or a single static page.

## 5. Target Files

```text
roadmap.json (repo root), rouvr/decompose/__init__.py,
rouvr/decompose/validator.py, rouvr/decompose/runner.py (STEP 2),
rouvr/decompose/gate.py (STEP 2), demo/decompose-demo.sh (STEP 2),
tests/test_decompose.py (STEP 2), rouvr/mcp_server.py (STEP 2 hook),
docs/tasks/rouvr/S021-decomposer-lite.md (this sheet)
```

## 6. Tasks

### Task 21.1 — roadmap.json contract (STEP 1)

- AC: R1–R6 map to scope 1–6 with source_refs; 6 half-day tasks with
  type/allowed/pinned/depends_on/covers/verifier/verifier_fails_on_head;
  coverage_ledger 100% both directions; dropped_content lists every cut
  with why; parses under the validator grammar.
- Tests-first: validator static checks are the test (parse/coverage/
  overlap); run-checks pending STEP 2 tests is expected, not hidden.

### Task 21.2 — Mini-language + validator (STEP 1)

- AC: parse accepts exactly the 4 primitives with right arity/types;
  anything else rejected; 5 rejection rules implemented; 3-run
  agreement with PASS/FAIL_ASSERT/ERROR classes (import error/timeout/
  collection never count as assertion); overlap incl. containment.
- Tests-first (STEP 2 test file; STEP 1 proven by dogfood run).

### Task 21.3 — Run gate (STEP 2)

- AC: after a build, verifier + `no_changes_outside` run; pinned touch
  fails automatically; receipt JSON with head SHA, verdicts, hashes.
- Tests-first: gate test with pinned edit → fail.

### Task 21.4 — Parent check (STEP 2)

- AC: parent test on combined revision; green children + red parent →
  `DECOMPOSITION_FAILURE` and send back to decompose.
- Tests-first: staged green/red fixture test.

### Task 21.5 — Decompose call + integration (STEP 2)

- AC: one model call → one candidate per §4.8; create-task path calls
  it so tasks are enriched before pickable; coder blind to hidden tests
  preserved (S017 invariant).
- Tests-first: fake-model candidate test; integration test on create.

### Task 21.6 — Demo script (STEP 2)

- AC: 3-minute scripted run shows good split, hollow rejected,
  pinned edit fails, non-composing split flagged.
- Tests-first: demo transcript checked into evidence.

## 7. Closeout Report

(Reviewer fills at closeout per guidelines.)
