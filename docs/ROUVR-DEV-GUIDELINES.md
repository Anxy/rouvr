# ROUVR-DEV-GUIDELINES.md

**Governing development contract for Rouvr work.**
Version: 1.0 (29 Oct 2026). Authority: `docs/ROUVR-MASTER-SPRINT.md` (scope),
this file (process). Child docs: sprint sheets in `docs/tasks/rouvr/`,
lifecycle in `docs/system/ROUVR-SPRINT-LIFECYCLE.json`.

## Roles

- **Worker** — claims a sprint, implements inside its bounded scope. Owns
  `queued → running → needs_review`. Never reviews, verifies, or closes its
  own work.
- **Supervisor** — reviews, verifies, closes. Owns
  `needs_review → closeout → done`. Never edits code to fix a failing sprint;
  it sends rework back (`needs_review → running`).

One principal may play both roles across *different* sprints, never both on
the same sprint (enforced: `review()` refuses the claimant).

### The semi-autonomous lane (closed loop)

```text
worker:  claim-next (--by <id>) → work → submit (+tests/evidence)
             → nudge supervisor session: "S0NN needs_review: <summary> <evidence refs>"
supervisor: review S0NN approved|change_required [--comment "..."]
             → nudge worker back on change_required
             → closeout S0NN --ref "..." (when approved)
```

1. `claim-next` picks the lowest queued sprint whose `depends_on` are done
   (dependencies are roadmap order, not preference).
2. Claims are fenced: claiming another worker's sprint refuses; a crashed
   worker re-claims its own sprint idempotently. `resume` (blocked→queued)
   releases the claim for another coder.
3. The nudge is a wakeup only — the ledger is the system of record. Use
   `muse.send_session_message` with the sprint id + evidence pointers.
4. Multiple coders and multiple supervisors are supported: holders are
   named on every call (`--by`), and the claimant≠reviewer rule keeps
   separation structural rather than conventional.

## Karpathy's Four Principles (mandatory first read on every session)

1. **Think Before Coding** – Never assume. Surface tradeoffs. State
   assumptions explicitly (`ASSUMPTION`). If uncertain → ask. Present options.
2. **Simplicity First** – Minimum code that solves the exact contract.
   No speculative abstractions. Deletion beats addition.
3. **Surgical Changes** – Touch only what the TaskKind + bounded scope
   requires. Never refactor unrelated code.
4. **Goal-Driven Execution** – Every slice has verifiable success criteria
   (tests-first, evidence, supervisor pass).

## Non-Negotiable Rules

- No sprint sheet → no execution.
- No bounded scope → no mutation.
- No evidence → no completion.
- No supervisor verification pass → no done.
- Secrets only via env / 0600 files; never logged, never in evidence text.
- Schema/state violation = **Stop the Line**.
- Stale or unknown behaviour is marked `ASSUMPTION` or `UNKNOWN` — never
  silently guessed.
- Tests-first for behaviour changes; run `hygiene.sh` + gitleaks before close.

## Development Flow (mandatory for every slice)

```text
SPEC → TASKKIND → CONTRACT → TESTS → EXECUTION → EVIDENCE → VERIFICATION → CLOSEOUT
```

## Lifecycle (managed in JSON, not markdown)

State lives in `docs/system/ROUVR-SPRINT-LIFECYCLE.json` — the canonical
ledger. **Never hand-edit it.** State changes are made only through the
first-class functions in `tools/sprint_ledger.py` (CLI below), which
validate, record history, and write atomically; invalid moves are refused
and leave the ledger untouched. Sprint sheets stay `.md`:

```bash
python tools/sprint_ledger.py next                       # next logical task
python tools/sprint_ledger.py claim    S011 --by w1 --pre-task "..."
python tools/sprint_ledger.py submit   S011 --tests "..." --evidence "..."
python tools/sprint_ledger.py review   S011 approved|change_required --by sup [--comment "..."]
python tools/sprint_ledger.py closeout S011 --ref "..." --by sup
python tools/sprint_ledger.py status   # and: validate (delegated checker)
```

```text
queued → running → needs_review → closeout → done
             ↑__________|             ↑_____|
                    rework loop (verdict: change_required)
any → blocked (reason required); blocked → queued (resume note required)
```

### needs_review is a review thread, not a gate

The Worker's mandate ends at `needs_review` — delivering work *for* review
is the deliverable. `needs_review` never resolves in one shot by default:
each round is one entry in the sprint's `cycles` array, and the Supervisor
closes the round with exactly one verdict:

| Cycle verdict | Meaning | Next state |
|---|---|---|
| `approved` | work accepted | → `closeout` |
| `change_required` | rework (comment mandatory) | → `running` (worker resumes) |

Cycles repeat without limit until an `approved` round; the thread (rounds,
verdicts, comments) is the sprint's review record. The Validator enforces
the thread: rounds are sequential, `change_required` carries a comment,
exiting `needs_review` consumes the matching cycle, `closeout`/`done`
require the last verdict to be `approved`, and `running` after a review
requires `change_required`.

Transition rules (machine-checked by `tools/validate_sprint_lifecycle.py`):

| From → To | By | Requires in JSON |
|---|---|---|
| queued → running | worker | `claim` with `--by <worker>` + `pre_task`; deps in `depends_on` all done |
| running → needs_review | worker | `tests`, `evidence` (commands + results) |
| needs_review → closeout | supervisor (≠ claimant) | cycle verdict `approved`, `verification` (commands re-run + pass) |
| needs_review → running | supervisor (≠ claimant) | cycle verdict `change_required` + comment |
| closeout → done | supervisor | `closeout` report; INDEX regenerated |
| any → blocked | either | `note` (reason) |

Every transition appends a `history` entry `{state, at, by, ref}`. The
validator rejects: unknown states, illegal transitions, missing required
fields, missing sprint files, verdict/thread mismatches, and a sprint
marked `done` without `review` + `verification` + `closeout`.

## Sprint Files

Layout: `docs/tasks/rouvr/S<NNN>-<slug>.md`. Every sheet declares, per the
Master Sprint template (§Child Sprint Template):

- Objective + roadmap/master mapping
- In scope / out of scope
- Tasks with TaskKind, bounded scope (exact files), acceptance criteria,
  tests-first plan, evidence required
- Sprint-level acceptance criteria (incl. ≥1 non-happy-path), tests, evidence
- Closeout report (filled by the supervisor at closeout)

TaskKinds: `PLANNING` / `IMPLEMENTATION` / `VERIFICATION`. A sprint mixes at
most two; validation-only sprints contain no implementation tasks.

## Pre-Task Checklist (worker, before `queued → running`)

```text
TaskKind: …
Bounded scope (exact files): …
Acceptance criteria (copied verbatim from sprint sheet): …
Tests-first plan: …
Guardrails: will run hygiene.sh + gitleaks before needs_review
```

Recorded in the JSON `pre_task` field (not a separate file).

## Post-Task Report (supervisor, at closeout)

```text
SPRINT CLOSEOUT REPORT — S<NNN>: PASS / FAIL / PARTIAL / BLOCKED
Implementation summary / files changed / APIs + data changed
Tests run + results (happy + non-happy)
Evidence produced (commands, outputs, links)
Deviations / assumptions / known gaps
Stop-the-Line check: no guideline violations
INDEX regenerated: yes
```

## Sprint Index & Lifecycle Protocol

- `docs/system/ROUVR-SPRINT-LIFECYCLE.json` — full index + lifecycle state.
- `docs/system/ROUVR-MASTER-SPRINT-INDEX.json` — management index
  (generated companion view: `ROUVR-SPRINT-INDEX.json` stays the brief one).
- Regenerate companions after every closeout:
  `python tools/generate_sprint_index_companions.py`
- Validate before closeout:
  `python tools/validate_sprint_lifecycle.py` and `python -m json.tool`
  on every JSON under `docs/system/`.
- Do not hand-edit generated companions (`*-CURRENT.json`, `*-SUMMARY.md`).

## Persona & Model Rules

- Worker = mutation only inside the sprint's files; no prompt may carry
  validation logic that belongs in code.
- Supervisor = read + re-run only; verdicts cite command output, not prose.
- Every model call is attributed (session/run id) in evidence.

**Final directive:** anything that cannot be enforced by these guidelines +
the lifecycle validator + the test gates must not be implemented.
