# Knowledge Lite verify list (CONFIRMED 2026-11-02, supervisor/liveproof)

Ankur approved: all 20 canonical + rule 5 confirmed via header-file loop (no token on argv).

## Canonical D1-D20 (verified)
- [x] knowledge:122@v1 verified=1 | [slice-1] D1: Tenant = organisation, possibly single person, umbrella with users under it. | D1: Tenant = organisation, possibly single person, umbrella with users under it.
- [x] knowledge:123@v1 verified=1 | [slice-1] D2: User may belong to >1 tenant and switch. | D2: User may belong to >1 tenant and switch.
- [x] knowledge:124@v1 verified=1 | [slice-1] D3: Roles per tenant: owner, admin, member, viewer. Bots get service identities, never a h | D3: Roles per tenant: owner, admin, member, viewer. Bots get service identities, never a human's.
- [x] knowledge:125@v1 verified=1 | [slice-1] D4: Sign-up/login uses Google OIDC via swappable provider interface (off-the-shelf library | D4: Sign-up/login uses Google OIDC via swappable provider interface (off-the-shelf library, not hand-rolled). Existing per-user tokens remai...
- [x] knowledge:126@v1 verified=1 | [slice-1] D5: Project mandatory fields: name, slug, owner, purpose, type (software, operations, cont | D5: Project mandatory fields: name, slug, owner, purpose, type (software, operations, content, research, mixed).
- [x] knowledge:127@v1 verified=1 | [slice-1] D6: Project optional: north star & success measures, stakeholders & approvers, systems & r | D6: Project optional: north star & success measures, stakeholders & approvers, systems & repos, constraints (budget, compliance, deadlines),...
- [x] knowledge:128@v1 verified=1 | [slice-1] D7: Profiling interview fills gaps. Model suggestions marked agent-sourced until user conf | D7: Profiling interview fills gaps. Model suggestions marked agent-sourced until user confirms. Approved profile versioned; every task recor...
- [x] knowledge:129@v1 verified=1 | [slice-1] D8: Every record carries tenant_id / project_id / task_id. Cross-tenant/project access ref | D8: Every record carries tenant_id / project_id / task_id. Cross-tenant/project access refused unless explicitly granted.
- [x] knowledge:267@v2 verified=1 | [slice-2] D9: Persistent, durable project memory. Entries: burst, note, decision, rule, chat, corres | D9: Persistent, durable project memory. Entries: burst, note, decision, rule, chat, correspondence, commentary, blob, snapshot.
- [x] knowledge:166@v1 verified=1 | [slice-2] D10: Append-only and versioned. Update = new version, archive hides, nothing hard-deleted. | D10: Append-only and versioned. Update = new version, archive hides, nothing hard-deleted. History answerable ("what did we believe on date ...
- [x] knowledge:167@v1 verified=1 | [slice-2] D11: Visibility set by creator: restricted (named users/roles), tenant-wide, or public (an | D11: Visibility set by creator: restricted (named users/roles), tenant-wide, or public (any signed-in Rouvr user, never anonymous).
- [x] knowledge:168@v1 verified=1 | [slice-2] D12: Rouvr and bots use knowledge via first-class tools: get, search, create, update, arch | D12: Rouvr and bots use knowledge via first-class tools: get, search, create, update, archive, link. Scope from session, never from argument...
- [x] knowledge:169@v1 verified=1 | [slice-2] D13: Provenance on every entry (author, source, time). Agent-written entries start unverif | D13: Provenance on every entry (author, source, time). Agent-written entries start unverified until reviewer/user confirms.
- [x] knowledge:170@v1 verified=1 | [slice-2] D14: Rules and standing instructions are authority. Bind only after owner approval (admins | D14: Rules and standing instructions are authority. Bind only after owner approval (admins may approve if delegated). Tasks/receipts record ...
- [x] knowledge:268@v2 verified=1 | [slice-2] D15: Separate curator agent runs on schedule (nightly default) to dedupe, compact, tag, li | D15: Separate curator agent runs on schedule (nightly default) to dedupe, compact, tag, link and version. Never deletes, may only propose ru...
- [x] knowledge:269@v2 verified=1 | [slice-2] D16: Session commands: store (save point), compact (structured summary with decisions, ope | D16: Session commands: store (save point), compact (structured summary with decisions, open questions, next steps, linked to raw transcript ...
- [x] knowledge:270@v2 verified=1 | [slice-2] D17: Demo runs single machine, multiple SQLite DBs as needed (auth/tenants, ledger, knowle | D17: Demo runs single machine, multiple SQLite DBs as needed (auth/tenants, ledger, knowledge per tenant). Blobs on disk, content-addressed,...
- [x] knowledge:271@v2 verified=1 | [slice-2] D18: Search keyword + meaning-based. Embeddings local model default, swappable (Meta Model | D18: Search keyword + meaning-based. Embeddings local model default, swappable (Meta Model API).
- [x] knowledge:175@v1 verified=1 | [slice-2] D19: Stay behind existing WorkHub interface so same contract can later run on full WorkHub | D19: Stay behind existing WorkHub interface so same contract can later run on full WorkHub. Model Lite on full WorkHub knowledge design wher...
- [x] knowledge:176@v1 verified=1 | [slice-2] D20: Tenant-neutral, no customer-specific logic in core. | D20: Tenant-neutral, no customer-specific logic in core.

## Standing rules
- [x] knowledge:5@v1 verified=1 | Rule (proposed by agent): always cite | Every answer using project knowledge must cite knowledge:<id>@v<n>.
- D14 (knowledge:170@v1) is the rules-authority decision.

## D9/D15-D18 v2 notes (appended, then confirmed)
Each v2 appends a Lite-status note accurate to code at 72f581c; full designs deferred to Slice 2.

## Related detail entries (untouched: verified=0, single-version chains)
- knowledge:142@v1 verified=0 | [slice-1] D1/D2: A user belongs to many tenants via memberships, switches active tenant per request/
- knowledge:143@v1 verified=0 | [slice-1] D3: Roles owner/admin/member/viewer enforced server-side per tenant from one permission ma
- knowledge:144@v1 verified=0 | [slice-1] D4: Google OIDC via a provider interface; per-user local tokens keep working unchanged as
- knowledge:145@v1 verified=0 | [slice-1] D5/D6: Project create refuses missing mandatory fields (name, slug, owner, purpose, type);
- knowledge:146@v1 verified=0 | [slice-1] D7: Profile has draft → approved versions, immutable once approved, field-level provenance
- knowledge:147@v1 verified=0 | [slice-1] D8: Every ledger row, workflow input, and on-disk record resolves to tenant/project/task;
- knowledge:182@v1 verified=0 | [slice-2] Storage layout (D17): `.data/knowledge/<tenant_id>.db` per tenant (WAL), `.data/knowle
- knowledge:184@v1 verified=0 | [slice-2] Visibility enforcement (D11/D12, S2): one `visible_to(principal)` predicate applied *i
- knowledge:185@v1 verified=0 | [slice-2] Verification & rules (D13/D14): agent-written versions start `unverified`; confirm is
- knowledge:186@v1 verified=0 | [slice-2] Search (D18): FTS5 BM25 + sqlite-vec vectors in the tenant DB, merged by reciprocal-ra
- knowledge:187@v1 verified=0 | [slice-2] Sessions (D16): `store` appends a `chat` version + checkpoint; `compact` is a model-wr
- knowledge:188@v1 verified=0 | [slice-2] Curator (D15): a tenant-scoped service identity (Slice 1) running an Ops-style Tempora
- knowledge:189@v1 verified=0 | [slice-2] WorkHub interface (D19/D20): REST mirrors `/projects/{project_id}/knowledge[/search]`

## Confirm loop used (header file, 0600, deleted after; already run)
```sh
printf 'X-Rouvr-Token: %s\n' "$(cat .data/api-token)" > /tmp/kh && chmod 600 /tmp/kh
for id in 122 123 124 125 126 127 128 129 267 166 167 168 169 170 268 269 270 271 175 176 5; do curl -s -o /dev/null -w "%{http_code}\n" -X POST -H @/tmp/kh -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8221/v1/knowledge/$id/confirm; done
shred -u /tmp/kh
```
