# Rouvr Master Sprint

## 1. Purpose

This Master Sprint governs delivery of Rouvr: an agentic coding system that
takes WorkHub tasks, builds them with a headless model, and closes them with
independent, evidence-backed verification — end to end, never by
self-report.

It is the authority for child sprint generation, implementation, validation,
evidence, closeout, and demo/beta signoff. Child sprints may be generated
from this document by Muse, but they must not introduce behaviour, data
structures, APIs, or assumptions that conflict with it.

Design authority: `docs/rouvr-roadmap.md` (phases R1–R10, A1–A2, P2) and
`docs/rouvr-system-design-v2.md`. Process authority:
`docs/ROUVR-DEV-GUIDELINES.md`.

## 2. Source Review Summary

The executed build (`docs/implementation-plan.md`, steps 1–12) delivered:

- Tenant-neutral repo with hygiene + gitleaks gates (R1)
- Live-proven same-session resume for headless turns (R2)
- Idempotent workflow start (R4), fenced lease/attempt ownership with
  renew-first resume (R5), persona + execspec + launcher + PR handling (R6)
- Deterministic submit gate (R7); hidden acceptance tests written by a
  separate persona, locked + hashed, run in a clean container at the PR SHA
  with cheat-scan (R8a/R8)
- Independent verifier + CAS close with readback (R9)
- Scripted kill/resume rehearsal to a recorded PASS (R10)

Required tightening (folded into §4–§10):

1. Builder ≠ verifier is principal-level, enforced at claim — session-level
   separation does not count.
2. One result row per attempt (`tested`); build facts live in evidence.
3. Close walks legal state hops with CAS + readback on both backends.
4. Stage parity is probed, not assumed; refusal codes are evidence.

## 3. Target State Statement

Rouvr is not an MVP reduction. The system must support:

- One task → one workflow (`REJECT_DUPLICATE`); same-attempt resume after
  worker death (renew-first, then reclaim + claim)
- Headless model turns with session resume; PR per attempt (idempotent)
- Hidden acceptance tests outside any builder workspace (locked, hashed)
- Clean-room test execution at the exact PR head SHA (offline container)
- Cheat-scan of the diff (skip/mock/literal/stub/broad-except/deleted-test)
- Independent verifier on criteria + diff + R8 evidence (never builder's report)
- System-only close: CAS + readback; receipt digest linking attempt, run,
  session, PR, and test run

Where live behaviour is unknown, the child sprint marks it `UNKNOWN` and
must not guess silently.

## 4. Non-Negotiable Delivery Rules

1. No workflow start without a verified contract revision.
2. No build write without a live, unexpired lease at the current epoch.
3. No second result core for one attempt (409 = correct on both backends).
4. No hidden-test file inside any builder workspace.
5. No approval without a gate pass AND a verifier approve — never either alone.
6. No approval from an unreadable verifier verdict (fail closed → reject).
7. No close without CAS + status readback on the served state machine.
8. No secrets in prompts, logs, evidence bodies, or transcripts (0600 files
   and env only; transcripts redacted before commit).
9. No new behaviour, table, API, or criterion id scheme outside this
   document unless marked `ASSUMPTION` and reviewed.
10. No child sprint may mix implementation and validation-only work.
11. No commit of generated companions by hand (`CURRENT`/`SUMMARY` are built).
12. No task closes on its own report — supervisor verification only.
13. Retry of a fenced write with a stale epoch is refused (MOVED), never queued.
14. Every demo claim must trace to a transcript/log path under `docs/r10-runs/`
    or this document's evidence sections.

## 5. Core Domain Components

| Component | Responsibility | Canonical artefacts |
|---|---|---|
| TaskWorkflow | build→test→verify→close, bounded rework | `rouvr/workflows.py` |
| Starter/API/CLI | idempotent start (REJECT_DUPLICATE) | `rouvr/starter.py`, `rouvr/api.py`, `cli/` |
| Activities | all side effects; workflow stays deterministic | `rouvr/activities.py` |
| Lease/fencing | claim → renew → reclaim; epoch is the token | `rouvr/lease.py` |
| Personas | builder/verifier by tenant role; principal ≠ principal | `rouvr/personas.py` |
| ExecSpec | repo, base branch, test command, timeout (digest-pinned) | `rouvr/execspec.py` |
| Launcher | headless turns, resume-by-sid, kill discipline | `rouvr/muse.py` |
| GitOps | workspace checkout, PR ensure, diff range, PR facts/CI | `rouvr/gitops.py` |
| Submit gate | deterministic facts-only judgement, evidence-backed rounds | `rouvr/gate.py` |
| Hidden tests | author-written, locked, hashed, builder-invisible | `rouvr/hiddentests.py` |
| Runner | clean container at PR SHA, offline, pinned image | `rouvr/testrunner.py`, `testrunner.Dockerfile` |
| Cheat-scan | six rules over the diff, pure function | `rouvr/cheatscan.py` |
| Verifier/close | gate + review turn; CAS walk + readback | `rouvr/activities.py` |
| Receipt | digest over attempt/run/session/PR/test-run links | `rouvr/activities.py:sign_receipt` |
| WorkHub port | one interface, stage + ledger backends | `rouvr/workhub/{port,client,ledger}.py` |

Contract fields (canonical task): `id, task_number, title, description,
status|state, record_version, contract_rev (= record_version),
acceptance_criteria[{id, text}]`. Error surface: `RouvrError(Code)` with
codes in `rouvr/errors.py`; retryable = {THROTTLE, DOWNSTREAM, TRANSPORT}.

## 6. Product Scope

In scope (phase 1, delivered): R1–R10 on the ledger backend with live model
turns and a real GitHub repo; stage parity probed and realigned.
Queued: A2 (scope grant → verifier credential → stage R10 → hyeve-lane
back-port), P2 (ed25519 receipt signing, pool).

Out of scope forever (unless added here by review): multiple results per
attempt, builder-visible hidden tests, verifier reuse of builder reports,
unbounded rework, self-review.

## 7. Sprint Sequence

Executed (all PASS; sheets in `docs/tasks/rouvr/`, lifecycle JSON):

| ID | Title | Master gate |
|---|---|---|
| S001 | Bootstrap, hygiene gates, package base + ledger parity | Gates live; parity suite green |
| S002 | R2 resume spike + A1 staging confirmation | RESUME verdict; readbacks exit 0 |
| S003 | R4 TaskWorkflow + R5 lease/fencing | Double-start idempotent; renew-first proven |
| S004 | R6 build leg (personas, launcher, gitops) | Live turn; one PR per branch |
| S005 | R7 gate + R8 hidden tests, runner, cheat-scan | Fake PR rejected; real PR passes |
| S006 | R9 verifier + close | Rework loop test; CAS+readback close |
| S007 | R10 rehearsal + record + A2 gates | Script exit 0; transcript recorded |

Queued (human-gated): S008 stage promotion (grant + verifier credential +
stage R10), S009 hyeve-lane back-port, S010 P2 receipt signing.

## 8. Child Sprint Generation Rules

A generated child sprint must:

1. Map to Master Sprint section(s)/sprint ID(s) and state its numbering.
2. Have a narrow objective, explicit in/out of scope.
3. List exact target files/modules per task.
4. Declare each task's TaskKind, acceptance criteria (verbatim), tests-first
   plan, and evidence.
5. Include ≥1 non-happy-path acceptance criterion.
6. List contracts touched (task/evidence/result keys, error codes).
7. Include drift check (§9) and closeout (§14) formats.
8. Mark unknowns `UNKNOWN`, inferences `ASSUMPTION`.
9. Not introduce APIs/tables/rules outside this Master Sprint.
10. Not combine implementation with validation-only work.

## 9. Mandatory Drift Check

Every child sprint answers before implementation (risky → `DRIFT_RISK: YES`
+ mitigation, and implementation waits):

1. Which Master Sprint requirement does this satisfy?
2. Does it touch state machine, lease/epoch, contract rev, criteria ids,
   evidence keys, idempotency keys, or close semantics?
3. Does it add a table/model/API not listed in §5?
4. Does it duplicate logic owned by another component in §5?
5. Does it hardcode a rule that belongs in config/spec?
6. Does it risk duplicate PR/result/evidence or a double close?
7. Does it preserve principal separation and secret handling?
8. Are all assumptions explicitly labelled?

## 10. Validation Sprint Policy

Validation-only sprints: no feature work, no refactor, only tests, parity
comparison, evidence, defect reporting. PASS requires unsafe paths to fail
closed *and* evidence proving it. A validation sprint may recommend fixes
but implements them only if explicitly scoped.

## 11. Testing Strategy

- Unit first (pure logic: gate, scan, parse, state walker, mapping).
- Contract suite on the ledger arm everywhere; stage arm gated on
  `ROUVR_CONTRACT_LIVE` + JWT.
- Workflow behaviour on Temporal's time-skipping server with fake activities.
- Live probes are evidence: launcher turns, container runs, staging readers.
- Non-happy coverage is mandatory per component: stale epoch (MOVED),
  tamper/leak (EVIDENCE), malformed verdict (fail-closed reject),
  timeout/kill (resume proof), illegal transition (no legal hop).

## 12. Error Taxonomy

| Code | Meaning | Retryable |
|---|---|---|
| CONTRACT / CONTRACT_MOVED | bad input / task moved | no |
| MOVED | fencing/CAS lost (incl. lease_expired) | no |
| EVIDENCE | missing/tampered evidence | no |
| SELF_REVIEW | verifier == builder principal | no |
| AUTHZ / INELIGIBLE / SCOPE | credential or persona problem | no |
| CANCELLED | activity cancelled | no |
| THROTTLE / DOWNSTREAM / TRANSPORT | transient upstream | yes |

Unknown 4xx → BAD_REQUEST (non-retryable). Fail closed everywhere else.

## 13. Evidence Requirements

Closeout includes: files changed, tests run + results (happy + non-happy),
live probe outputs where applicable, API request/response samples for
WorkHub changes, known gaps, and the exact commands re-run by the
supervisor during verification. Transcripts under `docs/r10-runs/` with
secrets redacted (token-pattern sweep before commit).

## 14. Closeout Format

```text
SPRINT CLOSEOUT REPORT — S<NNN>: PASS / FAIL / PARTIAL / BLOCKED
Master sections satisfied:
Implementation summary / files changed / APIs + data changed
Tests run + results / non-happy-path tests / parity or probe evidence
Idempotency status / secret-handling status / principal-separation status
Evidence produced / assumptions / known gaps
DRIFT_RISK: YES / NO
INDEX regenerated: yes
Recommended next sprint:
```

## 15. Beta/Demo Signoff Checklist

- [ ] R10 rehearsal exits 0; transcript recorded and redacted
- [ ] ≥1 rework loop observed in tests; fake-PR rejection proven
- [ ] Close readback green on both state maps
- [ ] Hygiene + gitleaks clean; public suite green
- [ ] A2 gates filed; no in-scope item unverified

## 16. Dev Lane Protocol (semi-autonomous execution)

Execution runs as a closed Worker/Supervisor loop over the sprint ledger
(`docs/system/ROUVR-SPRINT-LIFECYCLE.json`, operated only via
`tools/sprint_ledger.py`):

```text
worker:     claim-next --by <worker> → implement → submit
            → nudge supervisor session (sprint id + evidence pointers)
supervisor: review <id> approved|change_required --by <supervisor> [--comment]
            → closeout <id> --ref <report> (on approval)
worker:     on change_required, resume the same claim and re-submit
```

Rules: claims are fenced (holder + `claimed_at`; another worker's claim
refuses; a crashed worker re-claims its own sprint idempotently; `resume`
releases). `next`/`claim-next` select the lowest queued sprint whose
`depends_on` are all `done` — roadmap order, not preference. The claimant
may never review (claimant ≠ reviewer, enforced in `review()`). Multiple
workers and multiple supervisors are first-class: every mutation names its
actor (`--by`). Messages (`muse.send_session_message`) are wakeups only;
the ledger is the system of record. Queued: S008 (stage promotion, blocked
on humans), S009 (back-port, depends on S008), S010 (P2 receipts).

## 17. Muse Operating Instruction

```text
Read docs/ROUVR-MASTER-SPRINT.md and docs/ROUVR-DEV-GUIDELINES.md.
Generate or implement only the requested child sprint.
Do not introduce behaviour outside the Master Sprint.
Mark unknowns UNKNOWN, inferences ASSUMPTION.
Include TaskKind, bounded scope, acceptance criteria, tests-first plan,
evidence, drift check, and closeout format.
Validation-only sprints contain no implementation tasks.
Move lifecycle state only through tools/sprint_ledger.py (never edit the
ledger JSON); the Worker stops at needs_review, and the Supervisor's
approved|change_required verdict decides what happens next.
```
