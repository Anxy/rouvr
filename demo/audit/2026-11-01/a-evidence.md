# Evidence audit 2026-11-01 (a-evidence)

Scope: `demo/rehearsal/2026-11-01/{liveproof,cron,t1-clean,t2-kill,t3-fake200,t1-blocked-contract}/`
plus `demo/RUNBOOK.md` timed-run claims. Read-only; no services touched.
Base: branch `rouvr-development`, commit `8ad6d67`.
Signing code: `rouvr/receipts.py` (`build_links` = 11-link set,
`receipt_digest` = sha256 of sort-keys JSON, `verify_receipt` fail-closed).
Key config: `RECEIPT_SIGNING_KEY_PATH` (`rouvr/config.py`, default key id
`rouvr-receipt-1`) resolves to `/tmp/rouvr-supervisor/keys/receipt_ed25519.pem`
(mode 0600, verified read-only). Ledger reads via `X-Rouvr-Token` GET only.

## liveproof/ — GAP (crypto VERIFIED; two artifact/claim mismatches)

Crypto VERIFIED for all three tasks. Rebuilt each 11-link set from ledger
evidence (task_id, contract_rev = contract sha256, attempt_id 6/7/8,
muse_session_id, pr_url, head_sha, test_run_id, test_evidence_id 95/109/122,
verdict `approve`, reviewer `svc-sup-reviewer`, verify_evidence_id
100/113/127): recomputed digest matches `task{13,14,15}-summary.json`,
Ed25519 signature verifies against the live key, `key_id rouvr-receipt-1` matches.
Head SHAs exist in `Anxy/rouvr-liveproof` (`git ls-remote`: refs for
`rouvr/13/a1`, `rouvr/14/a1`, `rouvr/15/a1` and `refs/pull/{4,5,6}/head` all
match the summaries). `gh pr view` 4/5/6 resolve, state OPEN, head OIDs match.
Sealed suites intact: `created_by test_author`, predate the runs, `MANIFEST.json`
`tree_sha256` recomputes clean for 13/14/15. Tasks 13/14 recorded `test_run`
criteria match sealed groups exactly (9/9 and 12/12 via first-token parsing in
`rouvr/hiddentests.py::parse_criterion_outcomes`).
Tasks 13/14 review_verdicts `[approve, approve]`; `final-task.json` (task 15)
shows approve notes covering every contract criterion.

GAP 1 — `liveproof/task15-summary.json` + ledger `test_run` evidence id 122:
recorded `criteria` has 11 keys (`hidden=11/11`) but the sealed suite
`/tmp/rouvr-supervisor/hidden-tests/15/test_parse_duration.py` defines 12
criterion groups; `docstring` (`test_docstring_has_two_usage_examples`, the
file's last test) is absent. Cause: `rouvr/testrunner.py::_exec` keeps
`[-4000:]` but `sanitize_for_log` (default `limit=2000` in `rouvr/ids.py`)
re-caps to 2000 chars, and the ledger evidence body is likewise capped at
2000 chars (all three stored `hidden_tail`s end mid-line). The cut line never
contains ` PASSED`, so the group is silently dropped from `outcomes`
(`rouvr/activities.py::run_tests`). The run itself passed (pytest exit 0 is
required to reach verify/close; ledger shows `needs_review → done`; reviewer
notes independently verify the docstring criterion), so this is evidence
truncation, not a test failure — but the recorded criteria undercount the
sealed suite.
`liveproof/judge-proof.json` is internally coherent (figures
`{done:10, blocked:5, failed:0}`; good/pass reason matches figures; bad/fail
reason cites a specific `11 vs 10` mismatch) but file-only: no ledger evidence
ids or timestamps back the `model`/`seconds` fields.

GAP 2 — `demo/RUNBOOK.md` claims live tasks 13/14/15 took "~30–40 min each
wall". Ledger timestamps contradict: task 13 `created 12:27:05 → done
12:29:26` (~2.4 min), task 14 `12:31:00 → 12:33:25` (~2.4 min), task 15
`12:35:05 → 12:38:05` (~3 min); all three opened and closed inside an
11-minute window. No artifact backs 30–40 min each.

## cron/ — VERIFIED

`cron/grant.json` scope `ops:cronproof1793533438`, `expires_at
2026-11-01T11:47:58` matches `cron/schedules.json`. Tick A (task 6, created
11:44:00, under grant): state `done`, exactly one `ops_receipt` (id 20,
message `11d697bc4fefc84c`, also present in later outbox snapshots).
Tick B (task 7, created 11:49:00, after expiry): state `blocked`, single
`escalation` with `reason: no active grant for scope ops:cronproof1793533438`.
Observation (not a gap): the done tick-A task later accumulated 49
post-expiry `no active grant` escalations (schedule kept firing ~every minute
past expiry); `cron/tick1-task.json` is a point-in-time snapshot consistent
with that behavior, and the receipt count stays exactly one.

## t1-clean/ — VERIFIED

Exactly one `ops_receipt` (id 47); `t1-clean/receipts.json` byte-matches the
ledger row. Message `09ac561253827c0a` agrees across `result.json`,
`delivery` evidence (id 45), receipt body, and `outbox.txt` (present once, no
duplicate lines in the 4-entry cumulative listing). `judge.json` reviewer row
(id 46, pass, "All figures match ledger values.") matches task evidence.
`history.json` (41 events) workflow id matches `result.json`; span
12:07:55.65 → 12:08:02.94 (~7.3 s) backs the 7.4 s claim.

## t2-kill/ — GAP (minor; core facts verified)

Verified: exactly one `ops_receipt` (id 136, file matches ledger row);
message `d10638cafa462b06` agrees across `result.json`, `delivery` evidence
(id 134), receipt; `judge.json` pass row (id 135) matches task evidence.
`history.json` id matches `result.json`; event-type profile is identical to
the clean run (no timeout/retry events) with a single 460 s schedule-to-start
gap before the first activity — consistent with `kill_landed_at_task_state:
open` (kill landed before any activity started, so there was nothing in
flight to retry) followed by restart and normal completion.
GAP: `t2-kill/` contains **no `outbox.txt`**, so the RUNBOOK claim "exactly
one outbox file for the message id" has no per-run backing artifact here;
corroborated only cross-dir (`t3-fake200/outbox.txt` lists
`d10638cafa462b06.txt` exactly once; ledger shows a single `delivery` row).
Numeric nits: RUNBOOK "~3 min with all workers dead" vs the 460 s (~7.7 min)
gap in history; `elapsed_s 486.3` vs history span ~467.8 s (~18 s harness
overhead). Direction and order of magnitude are backed; the figures are
approximate.

## t3-fake200/ — VERIFIED

Exactly one `ops_receipt` (id 142, file matches ledger row) carrying the
**fallback** id `4b378c5c6fa3b3b9`, matching `result.json` (`sink: fallback`).
`judge.json`/`task.json` show the expected pair: primary reviewer fail
(id 139, `figures_match: false`, "Report is empty and missing all ledger
figures.") then fallback reviewer pass (id 141, "All figures match ledger.").
Lied-about primary id `e97d2f02553e266f` (delivery id 138) is absent from
`outbox.txt` as claimed; fallback id present exactly once; no duplicate
lines. `history.json` span 13:09:06.12 → 13:09:19.99 (~13.9 s) backs the
13.9 s claim.

## t1-blocked-contract/ — VERIFIED (as failure evidence)

`result.json` blocked/`ESCALATED`, empty `receipts.json`/`judge.json`
(consistent with a pre-claim park: nothing delivered, nothing to judge).
`task.json` escalation id 41 (`E_CONTRACT`, 12:04:58) backs the RUNBOOK story
(verbose non-schema contract, old merge failed validation); second escalation
(id 44, `blocked`, 12:07) comes from the rerun whose `history.json`
(`rerun-morning-report:t1-1793534862`, 17 events over ~0.1 s) is archived
here. Note the history covers only the rerun, not the original 12:04
failure — the task record ties both together.
