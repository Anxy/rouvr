# Roadmap Slice 1 — Identity & Tenancy + Projects

- **Status: WORK IN PROGRESS** — proposal stored 2026-11-01, awaiting owner review (Chunk 3 to follow). Not approved for implementation; no code started.
- Provenance: planning session 2026-11-01 (Chunk 1 brief, decisions D1–D8 binding). State baseline: `MEMORY.md` @ 52de485.
- Sibling: `2026-11-01-slice-2-knowledge-lite.md` (assumes this slice exists).

## Goal

Turn the S022 string partitions into real identity: multi-tenant users with roles, service identities for bots, Google OIDC behind a swappable provider, and versioned project profiles that every task stamps. Scope is the live surface only — `ledger/app.py`, `rouvr/` package, CLI/scripts. The legacy `core/backend` tree, knowledge, and task methodology are out.

## Binding decisions (owner, D1–D8)

- D1: Tenant = organisation, possibly single person, umbrella with users under it.
- D2: User may belong to >1 tenant and switch.
- D3: Roles per tenant: owner, admin, member, viewer. Bots get service identities, never a human's.
- D4: Sign-up/login uses Google OIDC via swappable provider interface (off-the-shelf library, not hand-rolled). Existing per-user tokens remain as local/CLI fallback.
- D5: Project mandatory fields: name, slug, owner, purpose, type (software, operations, content, research, mixed).
- D6: Project optional: north star & success measures, stakeholders & approvers, systems & repos, constraints (budget, compliance, deadlines), standing rules, glossary, tone, risk level.
- D7: Profiling interview fills gaps. Model suggestions marked agent-sourced until user confirms. Approved profile versioned; every task records the profile version it ran under.
- D8: Every record carries tenant_id / project_id / task_id. Cross-tenant/project access refused unless explicitly granted.

## Where we actually are (evidence, 2026-11-01)

- S022 is done but shallow. `tenants`/`projects` are name-string pairs with no fields beyond `created_at` (`ledger/schema.sql:20`). There is no ID separate from the slug, so a rename is a breaking change everywhere.
- `users.tenant` is a single mandatory column (`ledger/schema.sql:68`). D2 (user in >1 tenant) directly breaks this. There are no memberships and no roles at all.
- The shared stack token authenticates as `supervisor` and sees everything (`ledger/app.py:101`). Bots have principals in the persona YAML/AIX mapping (`rouvr/personas.py:29`) but the ledger itself cannot tell a coder bot from a human — worker writes go through the god token.
- The sleeper constraint: deployment is single-scope. `rouvr/config.py:33` holds one `tenant_id`/`project_id`, and `rouvr/api.py:64` refuses any other scope ("scope not served"). Multi-tenancy in the DB with single-tenant workers is theatre until this goes.
- D8 is half-met. Tasks carry tenant/project; contracts/evidence/receipts reach scope only via `task_id` joins. On disk, specs are scoped (`specs/<tenant>/<project>.yaml` in `rouvr/execspec.py:39`) but hidden tests and grants are keyed by bare `task_id`, and workflow IDs carry tenant+task but not project (`rouvr/ids.py:21`).
- There is no app frontend. `web/` is the marketing site. Today's humans arrive via CLI, MCP, and the supervisor session.

## Views (co-founder hat)

- **This slice is not a feature, it's permission to have a second customer.** Its commercial value is a design partner running in their own tenant on a shared stack, plus viewer access for approvers — the people who build trust in receipts without touching anything. Sell that; don't sell "enterprise IAM."
- **Sequence identity before OIDC, and keep OIDC thin.** Google login pays off only where a browser exists, and we don't have an app surface yet. Memberships, roles, and profiles are usable today via CLI/MCP. OIDC lands last, behind a flag, with a CLI loopback login — it must never block the rest.
- **The project profile is the quiet moat in this slice, not tenancy.** Tenancy is table stakes. A versioned, approved, per-project profile that every task stamps is the seed of "a colleague who understands your world" and makes receipts retrospectively interpretable. Fund D7 properly; don't let it become a settings form.
- **Do stable IDs now, while it's cheap.** We have 42 local tasks. Introducing `tenant_id`/`project_id`/`user_id` (ULIDs — `python-ulid` is already a dependency) after external users exist is a migration project; now it's a weekend.
- **Demote the god token.** Rouvr's differentiator is evidence and signed receipts — they mean little if every worker write is "supervisor." Tenant-scoped service identities with least-privilege scopes are what make the audit story true.
- **Say no, explicitly:** no SAML/SSO, SCIM, groups, custom roles, per-project ACLs, billing, or Postgres migration in this slice. Tenant role = access to all projects in that tenant until a design partner proves otherwise.

## Success criteria (mapped to D1–D8)

- D1/D2: A user belongs to many tenants via memberships, switches active tenant per request/session; first signup auto-creates a personal tenant with the user as owner.
- D3: Roles owner/admin/member/viewer enforced server-side per tenant from one permission matrix; bots authenticate only as tenant-scoped service identities, never as a human or the god token (which becomes bootstrap/break-glass only).
- D4: Google OIDC via a provider interface; per-user local tokens keep working unchanged as CLI/local fallback.
- D5/D6: Project create refuses missing mandatory fields (name, slug, owner, purpose, type); optional fields live in the profile, not ad-hoc columns.
- D7: Profile has draft → approved versions, immutable once approved, field-level provenance (user vs agent-sourced, confirmed by/at); every new task records the approved `profile_version` + hash it ran under.
- D8: Every ledger row, workflow input, and on-disk record resolves to tenant/project/task; every endpoint refuses cross-tenant/project access unless an explicit, audited access grant exists. Proven by negative tests, not assertion.

## Key decisions (builder hat)

- **AuthN vs AuthZ split.** A credential (OIDC session, local token, service token) authenticates a *principal*. The active tenant is request-scoped (explicit tenant slug/id on the call or session), never a property of the user row. One `authorize(principal, tenant, action, resource)` helper in the ledger replaces the scattered `_tenant_of`/`_stack_only` checks.
- **Data model:** `tenants(id, slug, name)`, `users(id, username, display_name, disabled)`, `memberships(user_id, tenant_id, role)`, `external_identities(provider, sub, user_id)`, `service_accounts(id, tenant_id, name, scopes, token_hash, expires_at)`, `sessions`/`api_tokens` (hashed, expiring, revocable), `projects(id, tenant_id, slug, name, owner_user_id, purpose, type)`, `project_profiles(project_id, version, body canonical JSON, sha256, status, approved_by/at)` reusing the existing `_canon` pattern from contracts, `invites(tenant_id, email, role, token_hash, expires_at)`, `access_grants` (cross-scope sharing, D8's "unless explicitly granted"), `audit_events` (append-only: actor, tenant, action, target, hash). Tasks gain `profile_version`/`profile_sha256`.
- **OIDC:** Authlib's Starlette/FastAPI client against Google's discovery document, behind our own `IdentityProvider` protocol returning verified claims. Link identities on Google's `sub`, never email (Google explicitly warns email is neither unique nor stable). Scopes `openid email profile` only — no sensitive scopes, so consent-screen verification stays light. Login issues a Rouvr session token so the rest of the stack sees one auth path. CLI gets `rouvr login` (browser loopback redirect).
- **Invites without email.** Mail delivery isn't built yet, so slice 1 invites are expiring token links an owner/admin copies to the invitee; acceptance binds the OIDC `sub` (or a local token) to a membership. SMTP is a later chunk.
- **Migrations:** replace ad-hoc `ALTER TABLE` blocks in `make_app` with a tiny numbered-migration runner + `schema_version` table. Keep SQLite; keep the S022 fail-fast on unscoped rows. Old `tenant`/`project` string columns become slugs during migration, then IDs become canonical.
- **Profiling interview:** no form builder. The supervisor session runs the interview using new MCP tools (`workhub_propose_profile`, `workhub_confirm_profile`) that write a draft version with per-field provenance; type-based question templates (software vs operations vs content…) live as data. Only approved versions are exposed to task creation; unconfirmed agent suggestions are visibly marked and never silently promoted.
- **Workers become tenant-agnostic.** Remove single-scope `Settings`/API checks; scope comes from the verified workflow input + ledger binding (the `verify_binding` fail-closed pattern already exists). Persona registry lookup is already per-tenant; a tenant with no persona pair fails start with a named error, never a silent default.

## Steps (each a sprint sheet in `docs/tasks/rouvr/`, tests-first, shippable alone)

1. **S024 — Contracts & migrations foundation** (~2–3d). ADR + permission matrix (owner/admin/member/viewer × tenant, project, task, grant, invite actions), schema design, migration runner, ID/slug rules. No behavior change. Gate: matrix reviewed by owner.
2. **S025 — Identity core in the ledger** (~4–5d). Users/memberships/roles/service-accounts/sessions/audit tables + endpoints; single authorization helper; existing per-user tokens keep working (auto-migrated to a membership); stack token demoted behind an explicit break-glass flag.
3. **S026 — D8 enforcement sweep** (~5–7d, highest risk). Every ledger endpoint, MCP tool, starter/API, Temporal input, and filesystem path (hidden tests, grants, workspaces) carries and checks tenant/project/task; workers de-singletoned; cross-scope access grants checked in the one helper. Deliverable includes an endpoint×tenant negative-test matrix.
4. **S027 — Projects & versioned profiles** (~4–5d). Project CRUD with D5 validation and slug uniqueness per tenant; profile draft/approve/version endpoints + MCP/CLI; task creation stamps approved profile version/hash and refuses when none is approved (explicit bootstrap path for the first version).
5. **S028 — Profiling interview** (~3–4d). Supervisor interview flow + per-type templates + propose/confirm tooling with agent-sourced marking. Producing suggestions only — how tasks *consume* profile content belongs to the methodology chunk; here we record and expose the approved version.
6. **S029 — Google OIDC + invites** (~4–5d). Provider interface + Authlib Google client, login/callback, account linking by `sub`, personal-tenant bootstrap, `rouvr login` loopback flow, invite create/accept. Feature-flagged; local tokens untouched. External dependency: owner creates the Google Cloud OAuth client and consent screen.
7. **S030 — Live migration & proof** (~2–3d). Migrate the live `.data` ledger (explicit scope assignment, profile v1 backfill for existing projects), docs (SUPERVISOR/README/roadmap), and a scripted live proof: two tenants, one user switching, role denials, a bot acting under its own service identity in receipts, cross-tenant refusal, task stamped with profile v1.

Total: roughly 24–32 builder-days. The critical path is S025 → S026 → S027; S028 and S029 parallelise after S027/S025 respectively.

## Validation plan

- Per sprint: new test files in house style (`tests/test_memberships_roles.py`, `tests/test_service_identities.py`, `tests/test_project_profiles.py`, `tests/test_oidc_linking.py` with the provider faked at the interface), plus the public cut staying green: `.venv/bin/pytest -q ledger demo lane/tests workers cli tests core/backend/tests/test_rouvr_model_providers.py`.
- S026 gate: an exhaustive negative matrix — every endpoint called as tenant B against tenant A's resources must 403/404; every filesystem record path asserted scoped. This is the highest-risk validation and the one that proves D8.
- S030 gate: the live two-tenant proof above on the real stack, transcripts filed like prior liveproof rounds; migration verified by row counts + integrity check before/after, old DB retained as backup.

## Risks / open questions

- **Assumption (reversible):** tenant role grants all projects in the tenant; per-project ACLs deferred. D6's stakeholders/approvers are recorded in the profile but get access only via tenant membership or an explicit access grant.
- **Assumption:** OIDC's first surface is API + CLI loopback; the browser app and tenant switcher UI wait for the frontend brief already pending in `MEMORY.md`. If the first external users are non-technical, this ordering is wrong and a minimal web shell must be pulled into S029 — owner's call.
- **Risk:** S026 touches every creation path; the S022 lesson is that one missed default reopens the partition. Mitigation is the negative matrix, not care.
- **Risk:** per-tenant persona credentials. Tenant-agnostic workers still need a coder/reviewer pair *provisioned per tenant*; slice 1 handles detection and naming, not automated provisioning.
- **Open:** shared stack token retirement — some worker internals still assume it; S025 inventories every caller before demotion.

## Sources

- Google OpenID Connect — https://developers.google.com/identity/openid-connect/openid-connect — discovery, `sub` as the stable unique identifier, email explicitly not an identifier.
- Authlib web clients — https://docs.authlib.org/en/stable/oauth2/client/web/index.html — Starlette/FastAPI OAuth/OIDC client integration via discovery URL.

