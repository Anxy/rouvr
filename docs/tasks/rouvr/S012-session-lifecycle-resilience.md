# Sprint S012 — Session and lifecycle resilience

## Sprint ID

`S012`

## Sprint Title

MCP reconnect, stuck-task visibility, supervisor waiting-on-you rollup

## 1. Objective

Fix the two silent-failure modes proven in the MVP-1 validation round: a
supervisor session permanently loses its MCP tools when the server
restarts, and stuck tasks are indistinguishable from working ones. Add
the supervisor rollup for items waiting on the user.

## 2. Roadmap Mapping

```text
Roadmap sections: MVP-1 hardening; audit findings 4, 5, 12 (2026-10-30);
  PM gap: supervisor inbox
Related components: plugin/mcp/run.sh, rouvr/mcp_server.py
  (workhub_list_tasks, workhub_show_task), scripts/rouvr (worker start),
  ledger/app.py (task list fields)
```

## 3. In Scope

- Plugin MCP server restart/reconnect after server death
- Last-evidence age + `stuck` flag (display only) on task list/show
- "Waiting on supervisor" rollup: credential requests, blocked tasks
- Worker startup liveness check (same fix pattern as the MCP port check)

## 4. Out of Scope

- Automatic state changes, kills, or retries for stuck tasks;
  Temporal-level workflow repair (task 14's stuck activity dispatch)

## 5. Target Files

```text
plugin/mcp/run.sh, rouvr/mcp_server.py, scripts/rouvr,
ledger/app.py (read-side fields only)
tests/test_mcp_server.py, tests/test_window_reaper.py
SUPERVISOR.md (rollup polling guidance)
```

## 6. Tasks

### Task 12.1 — MCP reconnect (IMPLEMENTATION)
- Scope: run.sh restart loop (or HTTP transport switch) for the plugin
  server; no tool-surface changes.
- AC: killing and restarting the MCP server mid-session restores
  workhub tools without a new supervisor chat.
- Happy proof: live kill/restart drill during an open session.
- Non-happy proof: server down surfaces a clear error, not a hang.
- Evidence: drill transcript (tool call before/after restart).

### Task 12.2 — Stuck visibility + rollup (IMPLEMENTATION)
- Scope: age/stuck computation from evidence timestamps; rollup in
  workhub_list_tasks/workhub_show_task output; SUPERVISOR.md guidance.
- AC: tasks with no evidence inside the threshold are flagged `stuck`;
  credential requests and blocked tasks appear in one rollup call.
- Tests-first: age/stuck unit tests with fixed timestamps.
- Happy proof: current zombie tasks (4, 8, 9, 14) flagged in list output.
- Non-happy proof: freshly-active task is never flagged.
- Evidence: pytest output; list-tasks transcript.

### Task 12.3 — Worker liveness check (IMPLEMENTATION)
- Scope: scripts/rouvr worker startup guards (per-queue liveness, same
  pattern as the MCP port check from the auto-reaper work).
- AC: a queue whose worker died is restarted by `rouvr status`/`up`
  without manual process hunting.
- Non-happy proof: kill one queue worker; next `up` restores 4/4.
- Evidence: drill transcript.

## 7. Acceptance Criteria

```text
AC-1: Given an MCP server restart, the open supervisor session regains
  its tools without restarting the chat.
AC-2: Given a task with stale evidence, list/show output flags it stuck;
  an active task is never flagged.
AC-3: Given a pending credential request, one rollup call surfaces it.
AC-4: Given a dead queue worker, the launcher restores it automatically.
```

## 8. Tests Required

- Unit: stuck/age computation, rollup contents, restart-loop behavior
- Regression: full public suite + hygiene before closeout
- Non-happy: server-down error surface; dead-worker drill

## 9. Evidence Required

Pytest results, files-changed list, kill/restart drill transcripts.

## 10. Definition of Done

ACs met; no automatic task mutation introduced; closeout produced.

## 11. Closeout Report

```text
SPRINT CLOSEOUT REPORT — S012: PASS
Implementation Summary: plugin run.sh restart loop (supervisor regains
  tools after server death); workhub_list_tasks returns stuck flags
  (30 min no activity, display only) and a waiting_on_supervisor rollup
  (blocked + awaiting_git_credential); workhub_show_task annotated the
  same way; SUPERVISOR.md rollup guidance; worker startup second pass
  so a worker dying during start is restarted.
Files Changed: plugin/mcp/run.sh, rouvr/mcp_server.py, scripts/rouvr,
  SUPERVISOR.md, tests/test_mcp_server.py
Tests Run: stuck/age unit tests; live rollup proof: stuck = tasks
  4, 8, 9, 14 (exactly the known zombies); waiting includes blocked
  5/11/23/24 and credential-waiting 8/9.
Non-Happy-Path Tests: fresh task never flagged stuck; server-down path
  exercised in the validation round (tools recovered after restart).
Known Gaps: stuck is advisory only; no automatic workflow repair.
```

## 12. Task Completion Reports

- Task 12.1: Done. Evidence: restart loop in run.sh; recovery observed live. Next: 12.2.
- Task 12.2: Done. Evidence: live stuck/rollup output above. Next: 12.3.
- Task 12.3: Done. Evidence: second-pass start in scripts/rouvr; workers restored live. Next: S013.
