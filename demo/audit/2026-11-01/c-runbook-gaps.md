# Runbook cold-follow audit (c-runner) — demo/RUNBOOK.md @ 8ad6d87

Date: 2026-11-01. Role: fresh read-only validator, no prior build notes.
Repo: <checkout>, branch rouvr-development, base 8ad6d87
(`git rev-parse HEAD` = 8ad6d87c42ddff5dc41ce2e2165bc0e6a04f21f5, tree clean).
Cold checkout: `git clone <checkout> /tmp/audit-runner` then
`git checkout -q 8ad6d87` — HEAD matches, `demo/RUNBOOK.md` byte-identical
(`diff -q` → IDENTICAL). No commits, no pushes, no service commands run.
No secret values are recorded in this report (env-file names only, never values).

Live stack was left untouched. The only live contact was read-only
`curl http://127.0.0.1:8221/health` → `{"status":"ok","service":"rouvr-ledger"}`.
No POSTs. No `up`/`down`/`stop`/worker-start, no `docker` writes, no image build.

## 1. Bring-up, step by step (stopped before any service start)

`git clone <repo> rouvr-demo && cd rouvr-demo` — DONE with local path.
Gap: `<repo>` is a placeholder, not a URL. A cold follower has no actual
clone URL (no SSH/HTTPS remote is named anywhere in the runbook).

`git checkout rouvr-development` — DONE in /tmp/audit-runner. Branch exists
at 8ad6d87. OK.

`make setup PY=python3.12` — ATTEMPTED, NOT COMPLETED (environmental, not a
repo defect). `make -n setup` expands to `python3.12 -m venv .venv &&
.venv/bin/pip install -r requirements.txt`. `<python3.12-on-PATH>`
exists (3.12.15) and `python3.12 -m venv /tmp/audit-runner/.venv` succeeded
in seconds, so the runbook's Python pin is satisfiable. The `pip install -r`
half (33 lines in requirements.txt) failed:

```
ERROR: Could not install packages due to an OSError: [Errno 122] Disk quota exceeded
```

(`/tmp` is a 16G tmpfs at 80% used; the partial venv reached 348M before the
failure. It was removed again to protect the live stack's quota. The live
repo's own `.venv` was never touched.) Setup could not be completed here, so
nothing downstream of setup was executable from the cold checkout either.

`ROUVR_LAUNCH_WINDOWS=0 ./scripts/rouvr --no-chat` — NOT RUN. STOP POINT.
A second bring-up would collide with the live demo stack, which holds
127.0.0.1:7233, 127.0.0.1:8221, 127.0.0.1:8231, 0.0.0.0:5433 and 0.0.0.0:6380
(`ss -ltn` verified). The script itself refuses this: `up()` aborts with
"port 8221 answered by a foreign process (not this checkout ...); aborting
up". Stopped exactly here, as instructed.

`./scripts/rouvr status` — NOT RUN (deliberately). It is not side-effect
free: `status` calls `load_env`, which unconditionally rewrites
`/tmp/rouvr-supervisor/env.sh` (shared live state), and its pid checks are
checkout-scoped (`_ours` compares `/proc/$pid/cwd` to `$ROOT`), so from a
cold checkout at a different path it would report workers 0/4 even with the
live stack healthy. The equivalent read-only probe (`curl /health`) was done
instead (ok, above).

Doc checks: Docker daemon reachable (`docker info` ok). `muse` CLI present
(`muse` on PATH, 1.4.14). Default `python3` is 3.14.4, so the
runbook's warning (3.14 breaks tiktoken, pin 3.12/3.13) is load-bearing and
correct. `testrunner.Dockerfile` exists and the tag
`rouvr-runner:py313-p9` matches `rouvr/config.py:54,91`. Image was NOT built
(recorded as not attempted; multi-minute docker build, and disk quota was
already exhausted on /tmp).

## 2. Requires line and ports — runbook is wrong on 4 of 6 ports

Runbook says: "ports 5432/6379/7233/8080/8221/8231 free". Reality:
`scripts/rouvr` always applies `/tmp/rouvr-override.yml`, remapping db
5432→**5433**, redis 6379→**6380**, temporal-ui 8080→**8081**
(`scripts/rouvr:23-31`); `demo/docker-compose.yml` (un-overridden) still
says 5432/6379/8080. The live listeners confirm the override mapping
(5433/6380/7233/8221/8231 all bound). A follower freeing exactly the six
listed ports would still collide on 5433/6380/8081. Further: nothing is
listening on 8081 right now (temporal-ui not up), so even the remapped UI
port does not match a running service. Correct line would name
5433/6380/7233/8081/8221/8231.

## 3. Live-model env block — accurate names, but nothing is in the repo

`/tmp/rouvr-supervisor/meta-provider-env.sh` exists, mode 600, and exports
exactly the four names the runbook lists (`ROUVR_MODEL_PROVIDER`,
`META_MODEL_API_BASE_URL`, `META_MODEL_NAME`, `META_MODEL_API_KEY`).
`.secrets/rouvr/meta.env` and `~/Repository/hackathon/.secrets/rouvr/personas/`
(`coder-1.json`, `reviewer-1.json`) exist. The key-name split the runbook
describes is real: server/provider side reads `META_MODEL_API_KEY`
(`README.md:100`, `workers/README.md:7`, `rouvr/muse.py:132` unsets it for
turns), while headless `muse` turns receive `META_API_KEY`
(`rouvr/activities.py:146,149,341`). Headless flags
`--disable-approval --disable-sandbox` confirmed at `rouvr/muse.py:23,185`.

Gap: **none of this is committed**. The restart loop
(`kill $(cat .../pids/worker-$q.pid)` + `setsid -f /tmp/rouvr-worker-start.sh`)
depends on `/tmp/rouvr-worker-start.sh`, which exists only in /tmp and is not
in the repo at 8ad6d87. A cold follower gets the stack up (modulo §1) but has
no committed way to give workers the live-model env; that step is
unexecutable-from-repo.

## 4. Demo order — what is / is not executable from the repo

Steps 1–2 (instruction text, contract merge + invalid-JSON refusal +
revoked-key park pre-claim): code-present (`demo/morning_report.py`,
`rouvr/ops.py:compile_ops_contract`, `rouvr/ops.py:52`, `tests/test_ops.py`)
and corroborated by the `t1-blocked-contract/` artifacts (state blocked,
digest `ESCALATED: blocked`, zero receipts, empty judge/receipt lists).
Reproducible in principle via `demo/morning_report.py` (local path).

Step 3 (grant + `rouvr_schedule_ops` + tick fires `OpsTaskWorkflow`):
**no single committed command fires a tick.** `rouvr_schedule_ops` exists as
an MCP tool (`rouvr/mcp_server.py:499`, via `rouvr/starter.py:78`) but only
creates cron schedules — the demo would then wait on cron. Grant creation
(owner approval via POST /v1/grants) has no committed command (ad-hoc curl).
The recorded t1/t2/t3 workflow ids carry a `rerun-morning-report:...` prefix
(e.g. t1 `rerun-morning-report:t1-534875-1793534875`), and a repo-wide grep
for `rerun-morning-report` finds only the rehearsal artifacts — the firing
helper is **uncommitted**. Exact missing command: something like
`temporal schedule trigger ...` or a `client.start_workflow(OpsTaskWorkflow…)`
one-shot; neither is committed nor documented (no `temporal` CLI on PATH
either). The rehearsal README (`demo/rehearsal/2026-11-01/README.md`,
still describing older r1/r2/r3 runs and listing cron-tick firing as "Not
covered") corroborates that tick-firing was never committed.

Step 4 (kill leg): **not reproducible from committed files.**
`demo/failure_injection/kill_midstep.sh` covers only the local
`demo/morning_report.py` path, not the Temporal stack. There is no committed
stack-level kill/restart script — the runbook's own snippet leans on /tmp
pidfiles and the /tmp starter. Worse, `t2-kill/` ships **no `outbox.txt` at
all**, so the headline assertion ("exactly one outbox file for the message
id", no double-send) has no preserved artifact in its own run directory.
(Receipt side is fine: t2 task.json holds exactly 1 `ops_receipt`,
primary `d10638cafa462b06`, which equals
`sha256("morning-report:t2-537096:primary")[:16]` — recomputed independently.)

Step 5 (recover → done + receipt): artifact-verified for t2 (state done,
digest `Done. No action needed.` = `OPS_DIGEST_DONE`, `rouvr/ops.py:34`).

Step 6 (fake-200 leg): mechanism verified in code (`ops_deliver` calls
`demo.morning_report.deliver`, which honors `ROUVR_INJECT` in the *worker*
env, `rouvr/activities.py:478-486`; `ops_verify` falls back to the second
sink on deterministic fail). Artifacts check out: t3 primary reviewer
evidence records deterministic fail + judge fail ("Report is empty and
missing all ledger figures."), fallback reviewer records pass/pass, receipt
carries fallback id `4b378c5c6fa3b3b9` (= recomputed
`sha256(run_key:fallback)[:16]`), and the lied-about primary id
`e97d2f02553e266f` is absent from the outbox listing. **But no committed
command sets `ROUVR_INJECT=silent_delivery` on live workers** — it requires
editing the /tmp starter (or worker env) plus kill/restart, which is
undocumented. `demo/failure_injection/silent_delivery.sh` again covers only
the local path. Exact missing command: a committed, documented way to set
and clear inject-vars on the four supervised workers.

Steps 7–8 (one `ops_receipt` per run; digest): verified from artifacts —
t1/t2/t3 task.json each hold exactly 1 `ops_receipt` (t1 primary
`09ac561253827c0a`, t3 fallback `4b378c5c6fa3b3b9`, all deterministic
recomputations match); cron tick A done with read-back receipt, tick B
blocked with an `escalation` record (`cron/tick2-task.json`: state blocked,
evidence_kinds `["escalation"]`). Minor oddity (not a blocker): tick A task
lists 6 `escalation` evidence entries alongside its receipt — unexplained.

Reviewer/judge claim: confirmed — reviewer evidence bodies carry
`judge.verdict` pass/fail + reason, `model: meta`
(`t1-clean/judge.json`, `t3-fake200/judge.json`), and
`liveproof/judge-proof.json` shows the good/bad pair under model
`muse-spark-1.3` (5.4s).

Step 9 (live-coding leg, tasks 13/14/15): **not reproducible from committed
files.** `test_author` exists only as interactive MCP machinery
(`rouvr/mcp_server.py`, `rouvr/hiddentests.py`); there is no committed
driver for "sealed hidden tests → live coder → PR → clean-room suite →
live reviewer → signed receipt". The summaries exist and are internally
consistent (13/14/15 all done, workflows `rouvr-task-supervisor-…`, PRs
`Anxy/rouvr-liveproof#4/#5/#6`, head SHAs, receipts with digest + signature
+ key id `rouvr-receipt-1`; `token-usage.json` notes the counting method and
cost n/a as the runbook says), but reproducing them needs hours of
supervised live-model turns plus the external `rouvr-liveproof` repo. The
three PR URLs are external references and were not fetched/verified. A cold
follower cannot run this leg; the runbook's own slow-model fallback
(pre-recorded transcript, never fake a receipt) is the only honest path and
should be labelled as such, not as step 9 of the live demo.

## 5. Reset-between-takes — hazardous on the shared host, and `down` is forbidden

`./scripts/rouvr down` stops the whole Docker Temporal stack: correctly NOT
run (would kill the live demo). `rm -f /tmp/rouvr-supervisor/ledger.db*
demo/.outbox/*` was NOT run either: both paths are live shared state
(`demo/.outbox/` is gitignored and currently holds the live outbox files;
`/tmp/rouvr-supervisor/ledger.db*` is the live DB). A cold follower sharing
this host and following the reset verbatim would destroy the live demo's
ledger and outbox. The runbook needs a guard (checkout-scoped paths, or an
explicit "never on the shared host" warning).

## 6. Artifact-design gaps a cold follower cannot close

- `outbox.txt` files are cumulative snapshots of the shared live outbox dir
(t1 lists 4 files, t3 lists 7, t1-blocked lists 3), not per-run proof, so
"exactly one outbox file" is not derivable from them; t2 ships none at all.
- `t2-kill/` has no `outbox.txt`; `r1/r2/r3/` (stale, stub-era per the
rehearsal README: no `judge.json`) coexist with `t1/t2/t3/` with no note on
which set the runbook's times refer to (elapsed_s in the t-set matches the
runbook: 7.4 / 486.3 / 13.9).
- Run-through times are **not-independently-reproduced**: no workflow was
executed (GET-only by rule); the numbers above are artifact readings that
match the runbook to the decimal, not fresh measurements.

## 7. Verdict list (runbook wrong / missing / unexecutable-from-repo)

1. `<repo>` placeholder is not a clone URL (§1).
2. Requires-ports line names the pre-override ports; correct set is
5433/6380/7233/8081/8221/8231, and 8081 currently serves nothing (§2).
3. `make setup` cannot complete under /tmp quota pressure here (environmental;
exact output quoted in §1); even green, bring-up stops at the port collision
(§1). `./scripts/rouvr status` from a second checkout is misleading by design
(checkout-scoped pids) and writes shared `env.sh` (§1).
4. No committed tick-firing command; the `rerun-morning-report` helper is
uncommitted (§4 step 3).
5. No committed stack-level kill/restart or inject set/clear commands; both
`failure_injection/*.sh` scripts cover only the local demo path; the runbook
snippets depend on /tmp-only files (§3, §4 steps 4/6).
6. `t2-kill/` lacks the `outbox.txt` its own headline claim needs; shipped
`outbox.txt` files are cumulative listings, not per-run proof (§6).
7. Live-coding leg (step 9) is not reproducible from committed files; PR
links are unverified externals (§4 step 9).
8. Reset section is destructive to the live stack on a shared host and must
not be followed verbatim while the demo stack runs (§5).
9. Rehearsal README is stale (r-runs, "not covered" list predates the
t-runs/liveproof it sits next to) (§4 step 3).

What IS solid: instruction text, contract merge + refusal paths, deliver +
read-back + fallback mechanics, receipt singularity (recomputed ids match in
all three t-runs), judge evidence shape, digests, cron A/B outcomes, key-name
split, `muse exec` flags, image tag, and all named artifact paths — every
path, filename, and env var the runbook names was found at 8ad6d87 except
the /tmp-only worker starter (correctly absent from the repo) and the
uncommitted tick/kill/inject helpers.
