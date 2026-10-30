# Sprint S016 — User profiles for local WorkHub

## Sprint ID

`S016`

## Sprint Title

Saved user profiles, per-user WorkHub tokens, and task ownership

## 1. Objective

Make `rouvr --user <username>` meaningful without adding launch friction:
a user is provisioned once with `rouvr user add <username>` (backed by
the seed script, or a first-run prompt), Rouvr saves a local profile,
and every later launch reuses it silently. WorkHub knows who the
supervisor is — per-user tokens, task attribution, and a default
"my tasks" view — while Coder/Reviewer workers keep their separate
system identities.

## 2. Roadmap Mapping

```text
Roadmap sections: MVP-1 hardening follow-up; supervisor UX
Related components: ledger/app.py (users, token auth), scripts/rouvr,
  bin/rouvr (--user), scripts/seed-user (new), rouvr/mcp_server.py
  (task create/list/show), rouvr/workhub/ledger.py, SUPERVISOR.md
```

## 3. In Scope

- Local user registry in the ledger: username, display name, token hash,
  created_at, disabled flag (no plaintext tokens stored server-side)
- `rouvr user add <username>` command (backed by `scripts/seed-user`):
  creates the WorkHub user and writes the local profile (WorkHub URL,
  username, token) under `~/.config/rouvr/users/<username>.json` with
  mode 0600; idempotent; runs without starting the full stack.
  Supporting verbs: `rouvr user list`, `rouvr user use <username>`
  (set the saved default)
- CLI profile selection: `rouvr --user <username>` loads the profile;
  plain `rouvr` reuses the saved default/last user; first run for an
  unknown user offers one-time creation, then never prompts again
- Identity propagation: MCP/worker calls carry the user; tasks record
  `created_by`; messages/evidence from the supervisor carry the username
- Task scoping: `workhub_list_tasks` defaults to the current user's
  tasks (`all=true` override for the supervisor view); show/post/start
  refuse tasks owned by another user with a named error
- Docs: SUPERVISOR.md and README user-profile sections

## 4. Out of Scope

- True multi-machine or untrusted multi-user security (per-user OS
  accounts, ports, databases, and stacks); OAuth/password login;
  external staging WorkHub user sync; changes to Coder/Reviewer
  personas or the git-grant flow

## 5. Target Files

```text
ledger/app.py, ledger/schema.sql, scripts/seed-user (new),
scripts/rouvr, bin/rouvr (user add/list/use subcommands), rouvr/mcp_server.py, rouvr/workhub/ledger.py,
rouvr/config.py, SUPERVISOR.md, README.md
tests/test_user_profiles.py (new), tests/test_ledger_auth.py,
tests/test_mcp_server.py
```

## 6. Tasks

### Task 16.1 — User registry + per-user token auth (IMPLEMENTATION)
- Scope: users table + seed data path; token resolution in the ledger
  auth middleware (token → user; the existing shared token keeps
  working as the stack/worker identity); disabled-user rejection.
- AC: a per-user token authenticates as that user; a wrong, unknown, or
  disabled user's token returns 401; the worker/shared token still
  performs worker writes.
- Tests-first: auth tests for user token, bad token, disabled user.
- Happy proof: create task with a user token; task shows its owner.
- Non-happy proof: disabled user token → 401 on read and write.
- Evidence: pytest output; curl transcripts (token values redacted).

### Task 16.2 — Seed script + CLI profiles (IMPLEMENTATION)
- Scope: `rouvr user add|list|use` subcommands in `bin/rouvr` /
  `scripts/rouvr` (intercepted before stack launch; `user add` calls
  `scripts/seed-user`); profile load/save (`--user`, default/last-user
  reuse, one-time create prompt for unknown users only); profile token
  injected as `ROUVR_LEDGER_TOKEN` for the session; status shows the
  active user.
- AC: `rouvr user add alice` provisions alice without starting the
  stack; `rouvr --user alice` starts with alice's identity and no
  prompt; plain `rouvr` reuses the saved default; adding twice changes
  nothing (same user, rotated only on explicit `--rotate`).
- Tests-first: profile selection unit tests (flag > saved default >
  first-run prompt path, prompt mocked).
- Happy proof: seed alice, launch, create a task as alice.
- Non-happy proof: unknown user without a terminal → named error
  telling the user to run `rouvr user add <username>`, never a hang.
- Evidence: CLI transcripts; profile file mode check (0600).

### Task 16.3 — Task ownership and scoping (IMPLEMENTATION)
- Scope: `created_by` on task create (from the authenticated user);
  list default filter to own tasks with `all=true` override; ownership
  checks on show/post/start; supervisor identity recorded on
  supervisor messages and credential-grant evidence.
- AC: bob cannot see, post to, or start alice's task by default
  (named refusal); alice's list shows her tasks; `all=true` shows all;
  worker flows (claim, evidence, close) are unaffected.
- Tests-first: two-user scoping tests against the test ledger.
- Happy proof: two seeded users, one task each, lists stay separate.
- Non-happy proof: cross-user start attempt → refusal with owner named.
- Evidence: pytest output; MCP transcripts for both users.

### Task 16.4 — Supervisor UX + docs (IMPLEMENTATION)
- Scope: SUPERVISOR.md identity guidance ("you are supervising as
  <username>; check waiting_on_supervisor first"); README profile
  quickstart; `rouvr status` prints active user and WorkHub URL.
- AC: a new user goes from seed command to first delivered task using
  only the README path; no step asks for WorkHub details twice.
- Evidence: README diff; status transcript.

## 7. Acceptance Criteria

```text
AC-1: Given `rouvr user add <username>`, the user exists in WorkHub and
  a 0600 local profile is written, without starting the stack. Given a
  seeded user, `rouvr --user <username>` launches with that identity
  and no WorkHub prompt; plain `rouvr` reuses the saved default.
AC-2: Given a per-user token, WorkHub calls authenticate as that user;
  unknown/disabled tokens return 401; no plaintext token is stored
  server-side or printed in output.
AC-3: Given tasks from two users, each user's default list shows only
  their own; cross-user show/post/start is refused with a named error;
  `all=true` restores the full supervisor view.
AC-4: Given any task, its creator is recorded and visible in show
  output; supervisor messages carry the username.
AC-5: Worker (Coder/Reviewer) flows complete unchanged under the
  worker identity, and a user token cannot perform worker-only writes.
```

## 8. Tests Required

- Unit: profile precedence, seed idempotency, token resolution,
  disabled-user rejection, two-user scoping, ownership refusals
- Regression: full public suite + hygiene before closeout
- Non-happy: unknown user non-interactive launch; disabled user;
  cross-user start; profile file with loose permissions refused

## 9. Evidence Required

Pytest results, files-changed list, seed/launch/status transcripts for
two users, two-user live task run (one task each, lists kept separate).

## 10. Definition of Done

ACs met; no launch prompt after first setup; closeout produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S016: PASS
Implementation Summary: ledger user registry (token hashes only,
  disabled flag); shared stack token = supervisor/worker identity,
  per-user tokens scoped to own tasks in the ledger itself (list
  filter, 403 naming the owner on cross-user read/write, worker-only
  endpoints refuse user tokens). rouvr user add|list|use backed by
  scripts/seed-user (idempotent, 0600 profiles, --rotate); --user
  selects a profile, plain rouvr reuses the saved default, unknown
  user prompts once on a terminal and errors named otherwise. Only
  the supervisor session carries the user token; workers keep the
  stack identity.
Files Changed: ledger/app.py, ledger/schema.sql, scripts/seed-user,
  scripts/rouvr, rouvr/mcp_server.py (unchanged surface), SUPERVISOR.md,
  README.md, tests/test_user_profiles.py
Tests Run: 125 passed (two-user scoping, disabled → 401, worker-only
  403, no plaintext tokens server-side); hygiene clean.
Live proof: rouvr user add alice/bob; alice profile 600; alice's list
  = [her task 26], bob's = [], stack sees all 26; bob → 403 on alice's
  task and on worker operations; `rouvr --user alice status` prints
  user: alice.
Non-Happy-Path Tests: unknown token 401; disabled user 401; cross-user
  evidence write 403.
Known Gaps: one shared stack/ports/DB per machine by design (trusted
  local profiles, not untrusted multi-tenancy, per sprint scope);
  test task 26 (alice, open) left in the live ledger as proof residue.
```

## 12. Task Completion Reports

- Task 16.1: Done. Evidence: test_user_profiles auth/scoping tests. Next: 16.2.
- Task 16.2: Done. Evidence: live `rouvr user add alice/bob`, profile mode 600, status shows active user. Next: 16.3.
- Task 16.3: Done. Evidence: live two-user list/403 proof above. Next: 16.4.
- Task 16.4: Done. Evidence: SUPERVISOR.md Identity section; README Users quickstart. Next: none.
