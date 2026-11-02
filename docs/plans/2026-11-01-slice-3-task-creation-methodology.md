# Roadmap Slice 3 — Task Creation Methodology Lite (Decomposer Lite + Loop Discipline)

- **Status: WORK IN PROGRESS** — proposal stored 2026-11-01, awaiting owner review. Not approved for implementation; no code started.
- Provenance: planning session 2026-11-01 (Chunk 3 brief B1–B5; research R4–R6; owner success criteria S6–S10).
- Predecessors: `2026-11-01-slice-1-identity-tenancy-projects.md`, `2026-11-01-slice-2-knowledge-lite.md` (identities, profiles, knowledge/rules assumed landed where referenced).

## Goal

Make task creation a control-plane function, not a prompt: mechanical, judgeable decomposition (fundamentals → closed-DSL predicates → baseline-proven verifiers), RCA after every failure, and software-factory loop discipline in the execution loop — so a reasonable coding task that historically took ~30 revisions takes ≤4, measured in a defined unit (S6).

## Where we actually are (evidence, 2026-11-01 — this changes the plan)

- **S021 already built half of B3, and production never used it.** `rouvr/decompose/` has a strict JSON verifier mini-language (`test_passes`, `no_changes_outside`, `path_absent`, `and`) with PASS/FAIL_ASSERT/ERROR classes, 3-run unanimity, hollow-verifier rejection (`rouvr/decompose/validator.py`), a run gate (pinned touch fails), and `check_parent` returning `DECOMPOSITION_FAILURE` on green-children/red-parent (`rouvr/decompose/runner.py:56`). But in the live ledger, decomposition evidence is `skipped` on **21 of 21 tasks**: the model emits string-form verifiers (`test_passes('tests/…')`) and the strict parser refuses them; `workhub_create_task` catches the error and proceeds anyway (`rouvr/mcp_server.py:126` — "best-effort"). `validate_doc` (baseline triple-run) is never called on the create path, and `check_parent` is only exercised by tests.
- **The execution loop is single-task.** `TaskWorkflow` (`rouvr/workflows.py:53`) runs build→test→verify→close for one task with `max_rework=2`. There is no parent workflow dispatching decomposed children as a DAG. Today's real verifier is the hidden acceptance suite + repo tests + cheat-scan (`rouvr/activities.py:251`), not the DSL.
- **Loop discipline partially exists.** The deterministic submit gate caps judged submits at 3 (`rouvr/gate.py`: `MAX_SUBMIT_ROUNDS=3`) and checks branch+CI, but not `HEAD == reviewed_sha`; the reviewer does clone at `head_sha` (good), each verify is a fresh muse session but always the same reviewer persona/model, and the review diff is full base→head every round, not delta.
- **R6 preliminary classification (live ledger, 17 coding tasks with test runs, 29 test_run rows):** 10 first-pass passes; 2 converged after rework (34, 39 — impl_bug); failures dominated by verifier/harness causes, not code — task 23 failed 3× with all tests passing but cheat-scan literal flags (weak_verifier; fixed since via allowed_literals), tasks 12/33 show `hidden=0/0` (unrunnable_verifier), tasks 8/11/20/32 blocked on infra/harness errors. Zero `request_changes` verdicts from model reviewers (all 12 reviewer reports approve).
- **The "~30 revisions" unit cannot be observed in the current loop** (attempts cap at 3). In our data, revisions leak out as *task re-creation*: the same `parse_duration` requirement recurs across ~15 task ids. Any honest S6 measurement must count successor tasks for the same parent requirement, not only in-workflow attempts.

## Views (co-founder hat)

- **Slice 3's first job is not more methodology — it is making S021 mandatory and observable.** A decomposer that silently skips 21/21 times is worse than none: it teaches us to trust machinery that isn't running. Fix the schema-emission failure, remove best-effort, gate dispatch on baseline-proven verifiers. That alone may buy more than N=3 search.
- **Measurement before mechanism (R6 first).** B1's hypothesis (integration + spec misunderstanding cause 80% of revisions) is *not* what Rouvr's own ledger shows — verifier/harness causes dominate here. Both can be true (different corpora: single-function demo tasks vs the owner's reasonable tasks), which is exactly why the benchmark corpus and revision unit must be frozen before we optimise anything.
- **Keep the DSL boring.** Six predicates, container-run, frozen bundles. Every added predicate is a new way to be gamed and a new runner to secure. The 80/20 filter applies to us, not just the model.
- **N=3 is a cost centre, not a default.** Three candidates × baseline triple-runs is real money and latency per brief. Default N=1 with validator-guided repair; spend N=3 only when the first candidate is ineligible or the risk tier is high.
- **The commercial claim is the receipt:** "admitted only after its verifier failed on baseline as an assertion, 3/3 deterministic; closed only at the reviewed SHA." That is sellable to anyone burned by agent chaos. OODA/hats language stays inside the control plane, never in customer copy.

## Part B — Research

### R4: Tessl registry/eval model + software-factory discipline — what to borrow

Tessl today (official docs, Sources) is a platform for managing agent context as versioned, governed packages: a registry/package manager for skills/plugins (install, version, roll back), governance (scanning/scoring, RBAC, audit), **evals** that run real tasks *with vs without* a piece of context and score the difference (scenarios are saved with the artefact as a regression test), observability over session logs, and **verifiers**: LLM-judge checks over committed files for binary invariants a linter cannot express — with Tessl's own guidance to prefer a linter/test whenever a deterministic check exists.

Borrow for Rouvr Lite:
- **The eval method as our S6 instrument:** frozen task scenarios run with/without Decomposer Lite, scoring revision counts — and scenario sets saved, versioned, and re-run whenever decomposer prompts, DSL, or verifier templates change.
- **A versioned registry *pattern* for verifier templates and task-type context** — implemented as Knowledge Lite entries (Slice 2), not a new platform. A template is publishable only after calibration (fails known-bad, passes known-good fixtures).
- **Verifier calibration discipline:** binary, observable invariants only; LLM-judge only for the residual that deterministic predicates cannot express, never as a substitute for DSL predicates.
- **Session-log mining for recurring mistakes** maps to our RCA aggregation over evidence (Slice 2 curator input).

Do not borrow: the Tessl platform itself (cloud registry, skills estate management, its own governance/RBAC duplicating Slices 1–2). The software-factory loop disciplines in B2/B4 (3-round cap, `HEAD == reviewed`, size gate, risk tier only goes up, finding needs a triggering input) have no source in this repo and are treated as **binding owner spec**, adopted on their merits — each is cheap, mechanical, and already half-present in `rouvr/gate.py`.

### R5: Closed DSL v1 (definition proposed)

Extends the existing three primitives to the B2 six; anything else is rejected at parse. Typed signatures (all args JSON, bundles SHA-frozen at intake):

- `test_passes{node_id: str, bundle_sha: sha256}` — pytest node from the sealed bundle.
- `no_changes_outside{paths: [str]}` — diff confined to paths (negative predicate).
- `path_absent{path: str}` / `path_present{path: str, sha?: sha256}`.
- `build_succeeds{command_id: str}` — command resolved from an ExecSpec allowlist by id, never free-form argv from the model.
- `http{method, path, expect_status, body_contains?: str}` — restricted to a per-project allowlisted local base (ops-style checks); no arbitrary URLs, no network in the sandbox except that base.
- `manifest_resolves{kind: execspec|profile|rule_manifest, ref: str, sha: sha256}` — Slice 1/2 artefacts resolve by hash.
- Combinators: `and[…]` (≤5 legs per Occam rule), `not[…]` only over `path_absent`/`path_present`/`http` status predicates.

Result types align to B2 and split today's ERROR honestly: `PASS | FAIL` (assertion evaluated false) `| BLOCKED` (input/bundle/spec unresolved) `| INFRA_ERROR` (timeout, collection/import inside the runner, runner crash) `| NON_DETERMINISTIC` (3 runs disagree → quarantine bundle, block dispatch, RCA event). Execution environment: **container test runner only** (`rouvr/testrunner.py` `ContainerTestRunner`, read-only clone at a pinned SHA, no network) — the validator's host-subprocess path is retired for task verifiers. Every run records runner identity `{dsl_version, runner_version, image_digest, bundle_sha, baseline_sha, head_sha}` as evidence and into the receipt. Bundles are sealed at intake into the hidden-tests-style store (`rouvr/hiddentests.py` manifest pattern), author = test_author/decomposer principal, never the coder; bundle paths are pinned for the builder.

### R6: Measurement

- **Revision unit (proposed, owner to confirm):** one revision = one build attempt within a workflow *or* one successor task created for the same `parent_requirement` before acceptance *or* one human bounce after `done`. Reported as three separate counters plus a total, so the "30" baseline and S6's "≤4" use the same arithmetic.
- **Classification:** every failed attempt gets an `rca` evidence row using B2's categories; a report (SQL over ledger, later a Knowledge commentary entry) gives the rolling last-20-loops breakdown. Preliminary in-repo classification is in "Where we actually are" above: weak/unrunnable verifier + infra dominate Rouvr's demo corpus — the benchmark corpus below decides whether that holds for reasonable tasks.
- **Benchmark corpus:** ≥5 frozen tasks (baseline repo + pinned SHA + brief), including the specific task behind the owner's "~30 revisions" — owner to name it; without it S6 is unmeasurable. Each runs A/B (decomposer off/on) under identical attempt definitions.

## Key decisions (builder hat)

- **Methodology functions as typed control-plane code**, each writing evidence: `extract_fundamentals` (First Principles: 3–5 truths + forbidden behaviours; a truth that cannot map to ≥1 DSL predicate yields a clarification, never a task), `decompose` (B3 pipeline: constraint extraction, intent chunking tagged claim/proposal/question/requirement with hard constraints first, assumption routing to check-tasks or clarifications recorded on the task and surfaced via waiting-on-supervisor), `score_candidates` (deterministic lexicographic: eligibility → coverage 100% → fewest clarifications → fewest edges → smallest write-set; no model judge), `classify_failure` (RCA: deterministic rules over test_run payloads, gate feedback and error codes first; model only for the residual, always citing `evidence_sha`; `weak_verifier`/`unrunnable_verifier` also blocks sibling tasks sharing the bundle/template).
- **Emission fix before anything else:** schema-constrained decomposer output (JSON schema + a worked example in the prompt; structured-output mode where the provider supports it), then a bounded repair loop (≤2) that feeds validator errors back verbatim. Still failing → task creation is refused with the validator's reason. Best-effort enrichment is deleted.
- **Intake gates (before any task is pickable):** DSL parses; inputs/bundles/fixtures resolve by SHA; dependencies acyclic; allowed∩pinned = ∅ (existing); each truth has ≥1 predicate; verifier triple-run on the baseline clone gives FAIL-as-assertion 3/3 where `expected: assertion` (change tasks) or PASS 3/3 where `expected: pass` (check/explore); size gate — >3 logical areas (distinct top-level modules in `allowed_paths`) or estimated >400 changed lines (model estimate recorded, hard-checked post-build against the actual diff) forces split pre-build (S8); risk tier computed deterministically (touches security/data paths, pinned-path count, dependency fan-out) and may only be raised by RCA/reviewer findings, never lowered automatically.
- **Parent/child execution (new, Lite-bounded):** a parent task holds the decomposition; children are ordinary ledger tasks (Slice 1 fields + `parent_requirement`, `depends_on`, `produces`, `consumes`, all hash-bound) dispatched sequentially in dependency order (max 4 children for demo). The existing single-task `TaskWorkflow` is reused per child. Parent acceptance runs `check_parent` wired for real: children green + parent red ⇒ `DECOMPOSITION_FAILURE` evidence + RCA `children_green_parent_red`, re-routed to the decomposer with the parent failure output, never counted as worker failure (S10).
- **Loop discipline deltas:** unify counting — one counter for review rounds (submit attempts + reviewer rounds), hard cap 3 → blocked to human with open findings attached. Reviewer records `reviewed_sha` (+ findings, each finding requiring a triggering input or it is commentary, non-blocking); re-review diffs `reviewed_sha..head` only; reviewer persona/model family rotates when >1 is registered (fresh session already holds). Close requires re-read PR facts (extend `pr_facts` with head OID): `head == reviewed_sha && CI green`, else refused — no admin override in code (S7; merging itself stays human on GitHub, and the signed receipt carries `reviewed_sha` so a post-review push is visible and the receipt stale). Any new commit invalidates the prior review.
- **Review modes as prompt policy on the reviewer persona:** change/refactor → Black Hat (how can this verifier be gamed?) + Blue Hat (does it compose into the parent?); explore/check → White+Green. Adversarial sampling: always at parent acceptance and on security/data paths (pinned-path or rule-manifest touching), fixed sampled % otherwise, bounded token budget, ≤2 counter-example retries into the decomposer. Second model family only when a non-Meta provider is configured; otherwise adversarial = distinct persona + Black Hat, recorded honestly as same-family.
- **Authority at dispatch:** child dispatch issues the existing scoped, expiring artefacts (lease + per-task git grant) bound to `{allowed_paths, bundle_sha, baseline_sha}`; expired or out-of-scope authority fails closed pre-build.

## Steps (sprint sheets S039+, tests-first, each shippable)

1. **S039 — Measurement & RCA taxonomy (R6)** (~3–4d). Revision-unit counters + `parent_requirement` lineage in the ledger, last-20-loops classifier over existing evidence, benchmark corpus freeze (owner names the "~30 revisions" task). No behaviour change. Gate: owner confirms revision definition + corpus.
2. **S040 — DSL v1 hardening (R5)** (~5–6d). Typed signatures for the six predicates + combinators, PASS/FAIL/BLOCKED/INFRA_ERROR/NON_DETERMINISTIC results, container-only runner with recorded runner identity, bundle sealing at intake, verifier-template registry as Knowledge entries with calibration fixtures (known-bad must fail, known-good must pass).
3. **S041 — Decomposer intake gate** (~6–7d, core sprint). Schema-constrained emission + bounded repair; fundamentals→predicate mapping with clarification routing; assumption routing (check task vs clarification, no dead output); eligibility (acyclic deps, resolved SHAs, type-vs-baseline expectation), baseline triple-run gate, size gate, risk tiering; decomposition becomes mandatory — create refuses without an admitted plan (max 4 tasks in Lite).
4. **S042 — Candidate scoring (N=3 trial mode)** (~3d). Generate ≤3 candidates only when N=1 is ineligible or risk is high; deterministic lexicographic selection; per-candidate evidence (why losers lost) for audit.
5. **S043 — Parent/child execution** (~5–6d). Parent task + sequential child dispatch on the existing TaskWorkflow, produces/consumes hash binding, `check_parent` wired at acceptance, decomposition-failure re-routing with parent output (S10).
6. **S044 — Loop discipline** (~4d). Unified 3-round counter, `reviewed_sha` binding + close refusal (S7), delta re-review, reviewer rotation, findings-require-triggering-input schema (unverifiable findings are commentary), post-build hard size check (S8 second half).
7. **S045 — RCA in the loop + adversarial** (~4d). `classify_failure` after every failed attempt (and a nightly aggregate feeding the Slice-2 curator), NON_DETERMINISTIC quarantine, Black/Blue/White/Green review modes by task type, adversarial sampling with bounded budget and ≤2 decomposer retries.
8. **S046 — Benchmark & live proof (S6–S10)** (~4–5d). A/B runs on the frozen corpus (decomposer off/on, Tessl-style), revision accounting in the confirmed unit, public report; demo script extended (good split, hollow rejected, pinned edit fails, non-composing split, size split).

Total roughly 34–43 builder-days. Critical path S039→S040→S041→S043→S044; S042/S045 parallelise after S041.

## Validation plan (owner criteria as gates)

- **S6 (≤4 revisions):** A/B on the frozen corpus with identical revision accounting; report attempts / successor-task creations / human bounces separately and totalled; mechanical verifiers only. Highest-risk validation in the slice: if the corpus task doesn't improve, the RCA breakdown of its remaining revisions is the deliverable, not a tuned prompt.
- **S7:** test that moves PR head after approval → close refuses; CI red → close refuses; receipt carries `reviewed_sha`.
- **S8:** fixture brief engineered to exceed 400 lines / 3 areas → split at intake, zero build attempts recorded.
- **S9:** intake test-suite over admitted tasks: every verifier FAIL-as-assertion 3/3 on baseline, PASS 3/3 post-build; import-error and timeout fixtures must classify INFRA_ERROR/BLOCKED, never FAIL; seeded flaky test → NON_DETERMINISTIC quarantine.
- **S10:** staged green-children/red-parent fixture → `DECOMPOSITION_FAILURE` + RCA row + re-decomposition invoked with parent output; worker attempt counters unchanged.
- Standing: `tests/test_decompose.py` extended per sprint; S021's dogfood `roadmap.json` re-validated under DSL v1; public test cut stays green (`.venv/bin/pytest -q ledger demo lane/tests workers cli tests …` per MEMORY.md).

## Risks / open questions

- **Owner inputs required:** (a) the named "~30 revisions" task + repo for the benchmark corpus; (b) confirm the revision unit (proposed above); (c) the Decomposer v1.3 handover doc referenced in B3 is not in this repo (only `roadmap.json`'s §4.8 shape) — supply it or accept the schema already in `rouvr/decompose/` as canonical.
- **Cost/latency:** baseline triple-runs at intake run real pytest 3× per child in a container; budgeted via bundle-level caching keyed by `(bundle_sha, baseline_sha, runner identity)` — identical verifier+baseline never re-runs.
- **Risk:** mandatory decomposition raises creation friction for trivial tasks. Mitigation: `check`/`explore` fast path (single task, `expected: pass` verifier) — still gated, never skipped silently.
- **Risk:** model emission may still fail schema under repair on hard briefs; refusal with validator reasons + clarification is the designed outcome, but the refusal rate is itself an S039 metric to watch.
- **Assumption:** `http` predicates stay local-allowlisted in Lite; external-service verification belongs to ops packs, not the coding DSL.

## Sources

- Tessl — what it is (registry/governance/evals/observability) — https://docs.tessl.io/
- Tessl evals (with/without scenarios saved as regression tests) — https://docs.tessl.io/improving-your-skills/evaluate-skill-quality-using-scenarios.md
- Tessl verifiers (prefer deterministic checks; LLM-judge for binary invariants only) — https://docs.tessl.io/codifying-and-enforcing-your-skill-standards/verifiers-overview.md
- Tessl spec registry in practice — https://tessl.io/blog/making-claude-good-at-go-using-context-engineering-with-tessl
- In-repo: `rouvr/decompose/{validator,runner,decompose}.py`, `rouvr/gate.py`, `rouvr/workflows.py`, `docs/tasks/rouvr/S021-decomposer-lite.md`, live ledger `.data/ledger.db` (R6 counts, queried read-only 2026-11-01).


