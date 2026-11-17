# Sprint S024 — Supervisor chat ops (NL + chips)

## Sprint ID

`S024`

## Sprint Title

Plan and trigger every workspace mutation from supervisor chat via
natural-language intents and in-thread action chips (secrets stay in dialogs)

## 1. Objective

Make supervisor chat the control plane for the operator loop: draft →
approve → bind repo → grant (dialog) → start/resume delivery → task
transitions/seal/retire → projects and knowledge — without leaving chat
for non-secret actions. Mutations run only through a named op catalog
(fail closed). GitHub PATs and passwords never travel as chat text.

## 2. Roadmap Mapping

```text
Roadmap sections: user direct order 2026-11-17 (supervisor chat ops);
  extends lite plan loop + delivery start (S019 grants, compose delivery).
Related components: ledger/chat_ops.py (new), ledger/app.py (POST /v1/chat),
  web/src/components/workspace/ChatSurface.tsx, web/src/lib/api/workspace.ts,
  tests/test_chat_ops.py, web delivery-ui-guard,
  docs/tasks/rouvr/S024-supervisor-chat-ops.md (this sheet)
```

## 3. In Scope

- Op catalog with phrases, confirm, and `needs_dialog` handoffs
- Pre-model resolver: explicit `op`/`op_args` → pending confirm → phrase match
- Runners that call existing ledger route handlers (no duplicated business rules)
- ChatSurface chips + DeliveryStartDialog / GitGrantPanel handoffs
- Tests: phrase matching, dialog handoff, secret rejection, UI guards
- Docs: this sheet, roadmap note, sprint index entry

## 4. Out of Scope

- Unconstrained model tool-calling (invented endpoints)
- Putting credentials in the chat transcript
- Removing Board / PlansInbox (remain as visual mirrors)

## 5. Tasks / AC

| ID | Task | AC |
|---|---|---|
| 24.1 | Catalog + SSE protocol | `ledger/chat_ops.py`; result frames carry `actions` / `op_offer` / `op_result` |
| 24.2 | Plan/delivery ops | approve, execute, start/restart; dialog for grants |
| 24.3 | Task + escalation ops | transition, seal, evidence, retire, patch |
| 24.4 | Projects + knowledge | create/archive/exec-spec; review/pin/archive/version |
| 24.5 | Tests + docs | `tests/test_chat_ops.py` green; delivery-ui-guard; index |

## 6. Verify

```bash
.venv/bin/python -m pytest tests/test_chat_ops.py tests/test_chat_intake.py -q
cd web && node --test src/app/__tests__/delivery-ui-guard.test.ts
```

## 7. Evidence

- Catalog covers plan/delivery/task/project/knowledge/session domains
- Chip click sends `op` + `op_args`; NL phrases resolve with conversation context
- Grant/login ops return `needs_dialog` instead of accepting secrets in chat
