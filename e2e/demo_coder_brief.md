You are the demo worker. Worktree: $HOME/dev/rouvr-demo-wt (branch demo/version-endpoint).
Ledger API: http://127.0.0.1:18234. Auth: header x-rouvr-token: $ROUVR_LEDGER_TOKEN
(from your environment — never print it, never put it in files or transcripts).

Do these steps in order. You cover ledger tasks 2, 3 and 4 (slice 1 is owned
by the supervisor):

1. Claim: POST /v1/tasks/{2,3,4}/assignments/claim with
   {"key": "demo-coder-<tid>", "principal": "demo-coder", "attempt_no": 1}.
   Keep each response's lease_epoch.
2. Implement in the worktree (and ONLY these files):
   - ledger/app.py: GET /v1/version returning {"version": "<string>"}. Put a
     module-level VERSION constant next to the other constants and use it.
     Follow the shape of the existing GET /health handler.
   - ledger/lite.py MAPPING plus one row in ledger/LITE_API_MAPPING.md so
     /api/v1/version aliases it (see the /api/v1/health rows).
   - tests/test_version_endpoint.py: session-authenticated test (log in as
     admin like tests/test_auth_session.py does, or use the stack token like
     tests/test_tenant_project.py does) asserting 200 + non-empty version on
     BOTH /v1/version and /api/v1/version with identical bodies.
   - Regenerate snapshots the repo tests pin: run
     $HOME/dev/rouvr/.venv/bin/python scripts/export_openapi.py from the
     worktree root, then (cd web && pnpm install --prefer-offline && pnpm gen:api).
3. Verify: run with TMPDIR=$HOME/dev/rouvr/.data/tmp and python
   $HOME/dev/rouvr/.venv/bin/python -m pytest, from the worktree root:
   tests/test_version_endpoint.py, tests/test_fl_a1_client.py,
   ledger/test_lite_contract.py, tests/test_auth_session.py.
   All must pass. If anything fails, fix it and re-run (max 2 fix rounds,
   then stop and report the failure honestly).
4. Record: for each of tasks 2, 3, 4: POST /v1/tasks/{tid}/attempts with
   {"key": "demo-att-<tid>", "attempt_no": 1, "lease_epoch": <epoch>}, then
   POST /v1/tasks/{tid}/results with {"key": "demo-res-<tid>",
   "attempt_id": <id>, "lease_epoch": <epoch>, "verdict": "pass",
   "result": {"summary": "<what you did + test counts>", "tests": "<files>"}}.
   Use verdict "fail" with the real error if verification failed.
5. Submit: POST /v1/tasks/{tid}/transition {"to": "needs_review"} for 2, 3, 4.

Reply with: files changed, test command outputs (tail), and the transition
states. Never reveal tokens or passwords.
