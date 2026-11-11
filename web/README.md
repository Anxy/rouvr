# Rouvr web workspace

Operator console for Rouvr-lite. The browser talks only to same-origin
`/api/**` routes; those proxies forward to the ledger with the session
cookie. Start the ledger first (see the root [README](../README.md)).

```bash
cd web && pnpm install && pnpm build --webpack && pnpm start --port 3000
# open http://localhost:3000/workspace/login
```

## Who may do what

| Actor | Auth | Can |
|---|---|---|
| Owner (you) | Cookie session after `/workspace/login` | Chat, approve plans, create/edit/retire tasks, retry, request changes, approve a task to `done` only when `head_verdict` is `pass`, review/verify knowledge, look up grants |
| Stack / worker | `x-rouvr-token` (ledger stack token) | Claim tasks, record attempts/results/heartbeats, create grants, seal/worker ops |

Cookie sessions restore username and CSRF from `GET /api/auth/session` on
every load, so a new browser tab keeps the same identity. Mutating calls
send `x-csrf-token`. Never set `ROUVR_INSECURE_DEV` or `ROUVR_ALLOW_ANON`
on a shared host.

## Surfaces

- **Board** — attention-first task list (polls every 3s; activity is a closed events snapshot).
- **Chat** — plan intake. Stub provider cannot author plans; set a live model on the ledger.
- **Planner / Execute** — approve materializes tasks; Record execute assigns an optional worker name. It does **not** start a worker. Open tasks stay open until a stack worker claims them.
- **Escalations** — approve (when verified), retry, request changes; grant create is stack-only.
- **Knowledge** — search, pin, archive, review/verify (owner review path).

## Dev notes

- Node 22 (`../.nvmrc`), package manager pnpm.
- Typed client: `src/lib/api/workspace.ts`. Regenerate OpenAPI types from the ledger when paths change (`pnpm gen:api`).
- Guard tests: `node --test src/app/__tests__/*.test.ts`.
