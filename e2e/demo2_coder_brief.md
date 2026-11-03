You are the demo worker. Worktree: $HOME/dev/rouvr-demo2-wt (branch demo/version-build).
Ledger API: http://127.0.0.1:18234. Auth: header x-rouvr-token: $ROUVR_LEDGER_TOKEN
(from your environment — never print it, never put it in files or transcripts).

You cover ledger tasks 6, 7 and 8 (slice 5 is owned by the supervisor).
Task titles: 6 "Resolve short git SHA with dev fallback", 7 "Expose build in
version response", 8 "Extend version tests".

1. Claim: POST /v1/tasks/{6,7,8}/assignments/claim with
   {"key": "demo2-coder-<tid>", "principal": "demo2-coder", "attempt_no": 1}.
   Keep each response's lease_epoch.
2. Implement in the worktree (and ONLY these files):
   - ledger/app.py: extend GET /v1/version to return
     {"version": VERSION, "build": <short git HEAD sha, or "dev" when git is
     unavailable>}. Resolve the sha with `git rev-parse --short HEAD`,
     falling back to "dev" on any error. Keep it simple and synchronous.
   - tests/test_version_endpoint.py: extend the test to assert the `build`
     key exists, is a non-empty string, and is identical on both prefixes.
   - Keep every snapshot that the repo's tests pin consistent with your
     change (the repo has tests that compare the live spec against committed
     snapshots — find them and satisfy them the way this repo does).
3. Verify: run from the worktree root with
   TMPDIR=$HOME/dev/rouvr/.data/tmp and python
   $HOME/dev/rouvr/.venv/bin/python -m pytest:
   tests/test_version_endpoint.py plus the repo's own contract/snapshot
   tests that pin the API surface. All must pass. If anything fails, fix it
   and re-run (max 2 fix rounds, then stop and report honestly).
4. Record: for each of tasks 6, 7, 8: POST /v1/tasks/{tid}/attempts with
   {"key": "demo2-att-<tid>", "attempt_no": 1, "lease_epoch": <epoch>}, then
   POST /v1/tasks/{tid}/results with {"key": "demo2-res-<tid>",
   "attempt_id": <id>, "lease_epoch": <epoch>, "verdict": "pass",
   "result": {"summary": "<what you did + test counts>", "tests": "<files>"}}.
   Use verdict "fail" with the real error if verification failed.
5. Submit: POST /v1/tasks/{tid}/transition {"to": "needs_review",
   "idempotency_key": "demo2-submit-<tid>"} for 6, 7, 8.

Reply with: files changed, test command outputs (tail), and the transition
states. Never reveal tokens or passwords.
