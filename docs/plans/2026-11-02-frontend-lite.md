# Rouvr Lite frontend + API — validation roadmap (plan only)

Date: 2026-11-02. Status: proposed, no building until approved. Supersedes the
earlier same-day draft (which is kept in git history).
Goal: browser demo of Rouvr Lite (Next.js + FastAPI + SQLite, single machine)
where the API is the only path to state, the UI never touches SQLite, and Muse
CLI is one more client of the same API. Staging rule: Lite-now = single
machine, desktop-only, dark-first, hyeve-auth sign-in only (no social),
no checkout, no fine-grained permissions. Everything else is LATER.

## 1. Where the site source lives and the reuse decision

- Site source is in-repo at `web/`: Next.js 16 + React 19 + Tailwind v4
  (`web/package.json`), tokens in `web/src/app/globals.css` (`@theme`: navy
  `#071522`, abyss `#040B13`, borders `#24445E`, brand cyan `#08A9F0` /
  `#0687C2`, cloud `#F3F6FA`, success `#34D399`; fonts via `next/font` in
  `web/src/app/layout.tsx`: Ubuntu Sans UI + Geist Mono for ids/SHAs/
  timelines/receipts), components in
  `web/src/components/{sections,ui,layout,effects,brand}`, motion via
  `motion/react` (`Reveal.tsx`, `TypingText.tsx`, `AuroraBlobs.tsx`), landing
  at `web/src/app/page.tsx`.
- Decision: build the app **in the same Next.js project**, as new routes (e.g.
  `web/src/app/(lite)/…`), sharing `globals.css`, fonts, and `ui/` + `effects/`
  primitives (`Button`, `Badge`, `StatusPill`, `TerminalWindow`, `Reveal`,
  `SpotlightCard`). Do NOT fork tokens into a sibling package.
- Why: one deploy for the demo, zero token drift (the side-by-side success
  criterion is free), landing reuse for the signed-out screen is a direct
  import. A sibling package only pays off with a second deployment target,
  which Lite explicitly does not have. If the app later needs independent
  release cadence, extract shared tokens to a package then — not now.

## 2. Roadmap (staged: Lite-now vs later)

Order: A (A5/A6 can go first, tiny) → B → C → D → E (parallel with D) → F.
Estimated total: ~4–6 weeks single engineer, staged so C2 is the first
demoable milestone.

### Slice A — API foundations (first; unblocks all UI)
- **A1 OpenAPI + typed client.** FastAPI serves OpenAPI; generate a typed TS
  client into `web/src/lib/api/` (openapi-typescript + fetch wrapper). Output:
  `pnpm --filter web typecheck` green, client used by all screens. Pass: no
  hand-written fetch to API routes except the generated client. Deps: none.
  Effort: 1–2d.
- **A2 Session-scoped tenancy.** Auth middleware resolves tenant/project from
  the session (hyeve JWT → local identity) server-side; every `/v1/*` handler
  takes scope from there, never from request args. Output: 403/404 parity
  tests. Pass: fuzz test with tenant-B token cannot read/write tenant-A rows
  via any endpoint (see Slice F). Deps: A1. Effort: 2–3d.
- **A3 Wrap, don't duplicate.** New Lite router wraps existing
  `ledger/app.py` handlers (`/v1/users`, `/v1/tenants`, `/v1/projects`,
  `/v1/onboard/*`, `/v1/knowledge*`, `/v1/tasks*` incl. transitions/evidence/
  attempts/results, `/v1/grants/*`) under a stable `/api/v1` prefix compatible
  with the WorkHub D19 contract. Output: mapping table (§4) + contract tests.
  Pass: existing `ledger/test_ledger.py` suite green unmodified. Deps: A1.
  Effort: 2d.
- **A4 SSE from the outbox.** Add `outbox` table + `/api/v1/events` SSE
  endpoint (cursor/replay, per-tenant filter server-side). All motion in the
  UI derives from these events only. Output: event catalog (§4). Pass:
  kill-worker test — UI shows stuck/idle, never fake progress. Deps: A2.
  Effort: 2–3d.
- **A5 SQLite hardening.** WAL already on (`ledger/app.py::make_app` sets
  `journal_mode=WAL`); add `busy_timeout` (e.g. 5000ms) + single-writer
  discipline (one writer connection/queue per DB; workers + API share it).
  Output: config + contention test (API + worker writing evidence
  concurrently, 0 `database is locked`). Pass: new test, public suite green.
  Deps: none. Effort: 0.5–1d.
- **A6 Knowledge id regression.** The "latest only" filter
  (`id NOT IN (SELECT supersedes_id …)` in `ledger/app.py`) already excludes
  superseded 165/171–174 when 267–271 supersede them (verified live
  2026-11-02: old ids carry no `supersedes_id` of their own, new ids point at
  them, all v2 verified) — add a regression test seeding exactly that chain
  across `search`, `knowledge-rules`, `snapshot`, and `curate` paths, and fix
  any path that surfaces an old id. Output: test + fix if red. Pass: old ids
  never in "current" results. Deps: none. Effort: 0.5d.

### Slice B — Auth + onboarding (needs Owner steps in §5)
- **B1 Login via hyeve-auth-service (no Auth.js/NextAuth, no social).**
  Sign-in form → hyeve native login → callback → verify JWT against JWKS
  (`ledger/hyeve_auth.py::verify_hyeve_jwt`, fail-closed) → local session
  cookie. Output: login/logout round-trip on staging. Pass: bad/expired/
  wrong-tenant tokens rejected; no local passwords exist. Deps: A2 + Owner
  steps. Effort: 2d.
- **B2 Create-or-join org + profile interview + first project (D1–D8).**
  Wizard: new tenant or join by invite → interview Q&A (stored as profile) →
  first project. Reuses `POST /v1/tenants`, `/v1/projects`,
  `/v1/onboard/complete`. Output: wizard screens (§3). Pass:
  success-criterion path works with no CLI. Deps: B1. Effort: 3d.
- Onboarding fields (maps onto existing `/v1/onboard/complete` form):
  user — display name (mandatory), what they do / role (optional, helps the
  supervisor cater), notification preference (optional); project — name
  (mandatory), goal/purpose (mandatory, → `project_purpose`), project type
  (optional select), north star / success measure (optional),
  standing rules for agents (optional, owner-typed → verified rules). Quick
  actions: hyeve sign-in, create-org vs join-by-invite toggle, Back/Continue
  per step, resumable wizard state.

### Slice C — Workspace shell + chat (the core demo surface)
- **C1 Three-rail layout** (§3 wireframe): left rail projects/sections,
  centre Rouvr chat with inline cards, right rail "Now working" live per-bot
  cards. Output: static shell with real session data, no mocks. Pass: desktop
  1440px matches site tokens side by side. Deps: A1–A4. Effort: 3d.
- **C2 Chat asks → task created.** Message → `POST /v1/tasks_with_contract`
  → task appears on board + timeline streams via SSE. Inline cards for
  slices/tasks/approvals/receipts. Output: working loop. Pass: "watch it
  live" criterion. Deps: C1. Effort: 2–3d.
- **C3 "Wait entertainment" rule enforcement.** Motion inventory audit: every
  animation maps to an SSE event type (§4); pulse on active steps, slide-in
  timeline entries, cyan-active/green-verified; `prefers-reduced-motion`
  respected (already in `globals.css`). Output: audit list in the PR. Pass: no
  timers/intervals driving progress UI (grep-verified). Deps: C1. Effort:
  0.5d.

### Slice D — Tasks board + task page + escalations
- **D1 Board** (queued / running / needs review / done / blocked) backed by
  task state + transitions. Ledger vocabulary is used, not lane vocabulary:
  queued=open, running=claimed, needs review=needs_review. There is NO
  "changes required" ledger state (request_changes loops to the coder per the
  S018 slice-entity decision) — review feedback surfaces as reviewer-note
  cards + timeline entries, not a column. Output: board with SSE updates.
  Pass: transition round-trips (`ledger/test_ledger.py` semantics). Deps: C1.
  Effort: 2d.
- **D2 Task article page** (§3): ask → pass criteria → live timeline → PR →
  tests → reviewer notes → knowledge citations → signed receipt + Verify
  button. Receipts are HMAC (`rouvr/receipts.py`, server-side key), so the
  button calls a server-side verify endpoint and displays the result — pure
  in-browser WebCrypto verification is NOT possible without exposing the key
  (asymmetric receipt signing is LATER). Output: page. Pass: tampered receipt
  fails verification visibly. Deps: D1. Effort: 2–3d.
- **D3 Escalations inbox** (approve / retry / grant). Wraps `/v1/grants/*` +
  transitions. Output: inbox. Pass: approve/retry/grant each move a task
  observably. Deps: D1. Effort: 1–2d. (LATER: anything beyond these three
  actions.)

### Slice E — Knowledge, admin, profile (can parallelize after C1)
- **E1 Knowledge UI**: browse/search/versions/visibility/verify + curator
  suggestions (wraps `/v1/knowledge*`). Pass: verify + new-version flow
  works; superseded entries read-only. Effort: 2d.
- **E2 Admin**: members/invites, roles (owner/admin/member/viewer — coarse
  checks only), model access mode (own key / package + test-key button, key
  never reshown), usage per project. Ledger today has only users + disable —
  members/invites/roles/usage need small new tables; ship coarse + stubs per
  the brief. Pass: viewer cannot mutate; key shown once. Effort: 2–3d.
  (LATER: fine-grained permissions, checkout, social login.)
- **E3 Profile + CLI token**: name/avatar/notifications + personal API token
  (create/revoke; CLI uses it as a Bearer — CLI becomes one more API
  client). Pass: CLI end-to-end against Lite API with token. Effort: 1d.

### Slice F — Validation gates (before sign-off)
- **F1 Cross-tenant fuzz** via UI/API paths (A2 evidence expanded: tasks,
  knowledge, evidence, events, grants). Pass: zero leaks. Effort: 1d.
- **F2 Look-parity check** side by side with rouvr.ai (tokens, fonts, hero
  reveal reuse). Pass: reviewer eyeball + screenshot pair in PR. Effort:
  0.5d.
- **F3 MCP code-mode measurement.** Collapse ~26 tools in
  `rouvr/mcp_server.py` to a typed search/execute surface; measure
  tokens/run before vs after on a fixed task set. Ship only if tokens drop
  without success-rate regression. (LATER if neutral/negative.) Effort: 2d.
- **F4 Green gates**: public suite + secret scan clean on the release commit.
  Effort: ongoing.

## 3. Screen map + wireframes

Map: `/` (landing reuse) → `/login` → `/onboarding` (org → interview →
project) → `/w` workspace (default) → `/w/tasks`, `/w/tasks/[id]`,
`/w/escalations`, `/w/knowledge`, `/w/admin`, `/w/profile`. Login/admin/
profile are thin; the three wireframes below are the surfaces that must be
designed.

Workspace (`/w`): left rail projects + section nav; centre chat with inline
cards; right rail live bot cards. All live regions update only on SSE.

```text
+--------+------------------------------------------+------------------+
| ROUVR  |  ROUVR CHAT                        [+]   | NOW WORKING      |
| proj A | +----------------------------------------+ | +--------------+ |
| proj B | | Rouvr: breaking this into 3 tasks…   | | | coder-1      | |
| -slice | | [card: slice S1 .......... View]     | | | task T-12    | |
| -tasks | | [card: task T-12 Queued ..... Open]   | | | step 2/5 ●●○○○| |
| -knowl | +----------------------------------------+ | | pass 3/5 ▓▓▓░░| |
| -evid  | | You: do the auth seam first            | | +--------------+ |
|        | +----------------------------------------+ | +--------------+ |
| + New  | | [input....................] [Send]    | | | reviewer     | |
+--------+------------------------------------------+------------------+
```

Task page (`/w/tasks/[id]`): article top-to-bottom; timeline streams in;
Verify hits the server-side HMAC check (§2 D2).

```text
< back to board                         [Queued|Running|Needs review|Done]
TASK T-12 — <title>                                    [Approve][Retry]
The ask ............ (prose)
Pass criteria  3/5  [criterion ✓][criterion ✓][criterion ✓][… ○][… ○]
Live timeline (SSE — new entries slide in)
  10:02 claimed by coder-1 ............ 10:04 step 2/5 .... 10:09 tests 41/44
PR  #nnn <link>   Tests  41/44 (from event, not polled)
Reviewer notes ............   Knowledge cited  [k267][k271] (current only)
Receipt  sha:9f2c… signer:rouvr-ledger  [Verify] → ✓ signature valid (server)
```

Onboarding (`/onboarding`): three steps, resumable; step 2 is the D1–D8
interview; fields per §2 B2 (mandatory vs optional marked).

```text
Step 1/3 Organisation   (•) Create [name____]  ( ) Join [invite code____]
Step 2/3 Profile interview (D1–D8) — chat-style, one question at a time,
         display name* … role … notifications …
         [Back][Continue]
Step 3/3 First project  [name*____] [goal*____] [type] [north star]
         [standing rules] [Create project → workspace]
```

## 4. API surface

Base: FastAPI Lite router at `/api/v1` (kept contract-compatible with WorkHub
D19 for later migration — D19 today means the documented contract in
`core/backend/scripts/wh_*`, not a live service). Tenant/project scope comes
from the session on every call — never from query/body args (A2). OpenAPI
feeds the generated TS client (A1).

| Lite endpoint | Method | Maps onto existing (`ledger/app.py`) | Notes |
|---|---|---|---|
| `/health` | GET | `health` | unchanged |
| `/auth/login`, `/auth/callback`, `/auth/logout` | GET/POST | `hyeve_auth.verify_hyeve_jwt` + onboard | new thin layer; no Auth.js |
| `/me`, `/me/token` | GET/POST | `create_user`, `list_users` | profile + CLI token (E3) |
| `/tenants`, `/projects` | GET/POST | same names | session-scoped lists |
| `/onboarding` | POST | `/v1/onboard/complete` | wizard state machine |
| `/chat` | POST (stream via events) | `tasks_with_contract` | ask → task(s) |
| `/tasks`, `/tasks/{id}` | GET | `list`, `task_or_404` | board + page |
| `/tasks/{id}/transition` | POST | same | incl. idempotency key |
| `/tasks/{id}/evidence` (+lookup) | GET/POST | same | PR/tests/receipts |
| `/tasks/{id}/timeline` | GET (seed) + SSE | attempts/results/binding | seed then stream |
| `/grants`, `/grants/active` | GET/POST | same | escalations inbox |
| `/knowledge…` (search, `{kid}`, history, versions, confirm, store, compact, snapshot, store-and-close, rules, curate) | as today | same names | wrapped, latest-only (A6) |
| `/events` | GET (SSE) | **new** (outbox) | the only live channel |
| `/admin/*` (members, invites, roles, model-key, usage) | GET/POST | users/disable + new small tables | coarse roles only |
| `/receipts/verify` | POST | `receipts.verify_receipt` | HMAC check stays server-side (D2) |

SSE event types (all from the outbox; UI motion binds 1:1, C3):
`task.created`, `task.transition`, `task.step` (current step + progress),
`task.tests` (pass/total counts), `task.criteria` (n/m + which flipped),
`attempt.started/finished`, `review.note`, `receipt.signed`,
`escalation.opened/resolved`, `knowledge.versioned/confirmed`,
`worker.heartbeat` (drives "stuck/idle", never progress).

## 5. Owner steps (values needed, no secrets)

1. **hyeve-auth-service as IdP**: register Rouvr Lite as a relying service —
   need `HYEVE_AUTH_BASE_URL` (e.g. `https://auth.<domain>`),
   `HYEVE_AUTH_TENANT` (e.g. `rouvr-demo`), and JWKS URL
   `<base>/v1/auth/jwks.json` reachable from Lite. Verify:
   `curl <base>/v1/auth/jwks.json` returns keys. No social/SSO setup.
2. **Person provisioning (if wanted)**: `HYEVE_PERSON_BASE_URL` +
   `HYEVE_PERSON_PATH` (default `/v1/persons`); service token stored
   server-side only.
3. **Lite host env**: `ROUVR_LEDGER_DATABASE_URL=sqlite:///./ledger/ledger.db`,
   session secret (32+ bytes, server-only), `SITE_URL=https://<lite-host>`
   for callbacks. Single-machine deploy; no Redis/Postgres.
4. **DNS/TLS**: `auth.<domain>` and `<lite-host>` with valid certs before B1
   testing.

## 6. Risks and open questions

1. **hyeve native-login shape is assumed, not confirmed** — B1 needs the
   direct sign-in path plus reachable JWKS (fallback: local
   username/password via the onboard endpoints with the same session
   contract). Confirm before Slice B.
2. **Outbox/SSE fan-out on SQLite** — single-machine SSE is fine, but long-lived
   connections + WAL checkpointing need a soak test; cap: one SSE stream per
   tab, cursor replay, server-side per-tenant filter.
3. **MCP code-mode may not save tokens** — F3 is measured and conditional; do
   not merge on faith. Keep the ~26-tool surface until numbers land.
4. **Coarse roles will be tested by real orgs** — the first "contractor sees
   too much" report forces fine-grained permissions (explicitly LATER; say so
   in onboarding copy).
5. **Scope-by-session regressions** — every new endpoint must re-prove A2/F1;
   add the fuzz test to CI so later slices cannot silently reintroduce
   arg-scoped access.

## Sources

- [openapi-typescript](https://github.com/openapi-ts/openapi-typescript) —
  generates TypeScript types from OpenAPI 3 specs (A1 client).
