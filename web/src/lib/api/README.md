# Typed API client (`web/src/lib/api/`)

- `openapi.json` — snapshot of the Rouvr ledger FastAPI OpenAPI spec.
- `schema.d.ts` — TypeScript types generated from that snapshot. Do not edit.
- `client.ts` — fetch wrapper: the typed `api` client plus same-origin
  Next.js route helpers. This module is the only place in `web/src`
  allowed to call `fetch()` against API routes.
- `index.ts` — public re-exports (`@/lib/api`).

Regenerate after any ledger route change (repo root):

```bash
pnpm gen:api
```

This exports the live spec (`scripts/export-openapi.py`) and regenerates
the types. Generation runs via `pnpm dlx` so the generator resolves its
own TypeScript 5 peer: the workspace runs TypeScript 7, whose compiler
API `openapi-typescript@7` cannot load yet.
