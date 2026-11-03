You are the demo worker. Worktree: $HOME/dev/rouvr-demo2-wt (branch demo/version-build).
Ledger API: http://127.0.0.1:18234. Auth: header x-rouvr-token: $ROUVR_LEDGER_TOKEN
(never print it, never put it in files or transcripts).

FIX ROUND (verify caught a real missing case — receipt follows):

The reviewer probe e2e/demo2_probe_nosubprocess.py FAILS against your
submission: each GET /v1/version spawns `git rev-parse --short HEAD`
(3 requests -> 3 spawns). In a container without .git every request pays a
failing subprocess. Fix:

1. In ledger/app.py: resolve the sha ONCE at import time into a module-level
   BUILD constant (same place as VERSION), keeping the "dev" fallback on any
   error. The handler returns {"version": VERSION, "build": BUILD} with no
   per-request subprocess.
2. In tests/test_version_endpoint.py: add a regression test that
   monkeypatches subprocess.run to raise and asserts GET /v1/version still
   returns 200 with the same build value (proves no per-request git).
3. Verify from the worktree root (TMPDIR=$HOME/dev/rouvr/.data/tmp,
   python $HOME/dev/rouvr/.venv/bin/python -m pytest):
   tests/test_version_endpoint.py, tests/test_fl_a1_client.py,
   ledger/test_lite_contract.py, plus the probe
   $HOME/dev/rouvr/e2e/demo2_probe_nosubprocess.py (run with cwd=/tmp
   so it exercises the worktree code, not the repo). All green.
   (Note: build value equals the worktree HEAD short sha; if you commit in
   the worktree the value changes — do NOT commit, leave that to me.)
4. Record on tasks 6, 7, 8: POST /v1/tasks/{tid}/evidence
   {"kind": "fix", "body": {"summary": "BUILD resolved once at import; regression test added; probe + suite green"},
   "idempotency_key": "demo2-fix-<tid>"}.
   Tasks stay in needs_review (no re-transition needed).

Reply with: the diff summary, test tails, and confirmation the probe passes.
Max 2 rounds, then stop and report honestly.
