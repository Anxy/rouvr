# Code review: `b22b86d..8ad6d87` — `rouvr/`, `tests/`, `demo/RUNBOOK.md`

Read-only review of the diff. No secret values below (names only). Rehearsal JSON artifacts out of scope.

## 1. Correctness

### C1. Judge error path contradicts the fail-closed docstring and skips the fallback sink
`rouvr/ops.py:119-132` (`verify_with_judge`) docstring promises "a judge error
fails closed", but `rouvr/ops.py:130` calls `judge_ops_report` with no `try`,
so a `CONTRACT` raise (unparsable/off-schema judge output, `rouvr/ops.py:109-114`)
or a provider transport error propagates instead of becoming a `fail` verdict.
`rouvr/activities.py:505-508` (`ops_verify`) likewise has no catch, so the
activity raises → Temporal `ActivityError` → `OpsTaskWorkflow` parks the whole
tick as blocked (`rouvr/workflows.py:183-184`) and the fallback sink is never
tried. A transient judge outage (one non-JSON reply, one model 500) therefore
blocks a tick that the sink-fallback design (`t3-fake200` rehearsal) exists to
save. The rehearsal covers judge-*verdict* fail, not judge-*error*; no test
covers the error path through `verify_with_judge`/`ops_verify`. Fix: catch
`RouvrError` from `judge_ops_report` inside `verify_with_judge` and return
`verdict: fail` with a `judge.verdict: error` marker.

### C2. Governed contract merge still breaks on empty arrays
`rouvr/ops.py:81-84`: `all([])` is `True`, so a well-typed-but-empty model value
(`"steps": []`, `"stakeholders": []`) overwrites the safe default and then
fails schema `minItems` in `validate_ops_contract` (`rouvr/ops.py:37-43`) →
`E_CONTRACT` block. The stated goal ("alien shape leaves safe defaults
standing") holds for mistyped keys but not for empty lists. Fix: require
non-empty (`if isinstance(val, list) and val and all(...)`). `tests/test_ops.py`
alien-shape test does not cover this edge (see T4).

### Race conditions
None introduced by this diff (pure functions, static argv, env-dict merge;
the `ops_verify` double-read of the outbox file, `rouvr/activities.py:498-504`,
has an `OSError` fallback on the second read — not material).

## 2. Security

### S1. `_run` env merge defeats `clone_env` least-privilege — full worker env leaks to `gh`/`git` children (highest severity)
`rouvr/gitops.py:15-16` merges the *entire* `os.environ` under every caller
env. `clone_env()` (`rouvr/gitops.py:49-60`) documents "Minimal environment …
Nothing else from the worker process leaks into git/gh" — that guarantee is
now false for every `_run` caller: `prepare_workspace` clone/fetch
(`rouvr/gitops.py:80-82`), `ensure_pr` / `close_superseded_pr`
(`rouvr/gitops.py:104-109`, `113-124`), `clone_at`, `pr_facts`. Children now
inherit `ROUVR_LEDGER_TOKEN`, `META_API_KEY`/`META_MODEL_API_KEY`,
`WORKHUB_JWT`, host `GH_TOKEN`, and proxy URLs. The fix that motivated this
(keep `PATH` for `gh` resolution) needs an allowlist merge (`PATH`, `HOME`,
proxy vars + caller env), not all of `os.environ`. `_push_branch`
(`rouvr/activities.py:230-238`) is *not* affected — it uses
`create_subprocess_exec` directly with the minimal env. Unaffected paths:
coder/reviewer `muse` spawns (`rouvr/muse.py:237-240` replace env; container
path `rouvr/muse.py:160-173` passes explicit env only).

### S2. `--disable-sandbox` leaves a concrete self-grant path on default host turns
`rouvr/muse.py:184-195` adds `--disable-approval` + `--disable-sandbox` to
every headless turn; default is `sandbox="host"` (`rouvr/muse.py:149`), so the
unsandboxed path is the common one. The remaining boundary (workspace-as-`HOME`
+ prompt + 40-step cap) is advisory: same-uid filesystem reads are
unenforced. Concretely, the supervisor grant file
`<workspace_root>/.git-grant-<task>.json` (`rouvr/activities.py:200`) is a
sibling of the coder workspace (`rouvr/gitops.py:28`) readable by the worker
uid, so an unsandboxed turn can `cat ../.git-grant-*.json` and self-grant
push/PR, bypassing the `_await_git_grant` gate (`rouvr/activities.py:208-227`).
Related: the coder legitimately holds `META_API_KEY` in env
(`rouvr/activities.py:149`, by design) with unrestricted network egress in
host mode, and cloned repos are untrusted input (prompt injection can drive
tool use; `--disable-approval` removes the only checkpoint). Justification is
documented (`RUNBOOK.md`, no approver on workers; host bwrap broken), but the
grant-file placement assumes a sandbox that is now off by default. Fix
options: move grant files outside the workspace tree (or onto a separate uid /
mode `0600` + sandbox), or enforce push authorization server-side; until
then treat host-mode turns as sandbox-less in the threat model. Container mode
remains the stronger boundary (workspace-only mount).

### S3. Model-controlled judge `reason` lands in evidence unbounded
`rouvr/ops.py:115` stores `str(data.get("reason") or "")` with no length cap;
`rouvr/activities.py:510-515` posts it to ledger evidence. A hostile or
malfunctioning judge can bloat evidence/logs per tick. Cap (e.g. 500 chars).

### Secret values / grant-gating / permissions
- No secret *values* in the diff (env/file names only) — clean.
- Grant-gating: this diff does not touch `grant_scope` propagation; the
  per-tick active-grant check in `ops_prepare` (`rouvr/activities.py:439-443`)
  and scope stamping in `run_schedule_ops` (`rouvr/starter.py:86-98`) are
  intact. Residual (pre-existing, unchanged): `OpsInput.grant_scope` defaults
  to `""` (`rouvr/contracts.py:91`) and `ops_prepare` skips the check when
  empty, so any directly-started workflow without a scope runs grant-free;
  `run_schedule` (`rouvr/starter.py:105`) requires a grant by docstring only.
- Coder env (`rouvr/activities.py:149`, `GH_TOKEN: ""`) correctly denies git
  auth until grant; reviewer turn (`rouvr/activities.py:341`) carries no git
  credential — clean.

## 3. Tests that pass for the wrong reason

### T1. `_run` env test asserts presence, not absence — green under the S1 leak
`tests/test_git_grant.py:33-42` (`test_run_merges_env_over_os_environ`)
asserts the probe var and `PATH` survive, but never asserts worker secrets
(e.g. `ROUVR_LEDGER_TOKEN`) are *absent* from the child. It passes identically
for the correct allowlist merge and the current leaky merge. Meanwhile
`test_clone_env_minimal` (`tests/test_git_grant.py:57`) and
`test_clone_env_uses_only_stack_token` (`tests/test_muse_windows.py:170`) test
`clone_env()` in isolation, which still passes — so the suite is green while
the documented boundary is broken. Needs a negative assertion through `_run`
(e.g. seed `os.environ` with a canary secret, assert the child cannot see it).

### T2. Judge fakes ignore the prompt — cannot catch prompt-construction bugs
`tests/test_ops.py:551-556` (`_chat`) returns a canned verdict and asserts
only `messages[0]["role"] == "system"`. `test_judge_passes_matching_report`
(`:558`) / `test_judge_rejects_wrong_report` (`:565`) therefore pass even if
the user message dropped the figures, swapped report/figures, or cited the
wrong tick — they exercise JSON parsing + verdict extraction only, not that
the model is actually asked to compare the right values. The combination tests
(`:592-604`) are valid for AND-logic but share the same blind fake, and no
test covers the judge-error path (see C1), leaving the "fails closed" claim
untested. Needs fakes that assert the user payload contains the figures and
report, plus an error-path test.

### T3. Alien-shape test is weaker than its claim; flags test pins no boundary
`tests/test_ops.py` alien-shape test asserts mistyped keys keep defaults but
omits the empty-list case (see C2). `tests/test_unit.py:341`
(`test_exec_argv_carries_headless_flags`) asserts the flags exist but pins
none of the compensating boundary (cwd-`HOME`, minimal env, grant-file
placement), so a future env-broadening or path change stays green (see S2).
