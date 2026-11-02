# Knowledge Lite verify list (reconciled 2026-11-02, supervisor/liveproof)
#
Ankur: confirm each line, then run the command at the bottom once.
## Canonical D1-D20 (exactly one current entry per decision)
- [ ] knowledge:122@v1 | [slice-1] D1: Tenant = organisation, possibly single person, umbrella with users under it. | D1: Tenant = organisation, possibly single person, umbrella with users under it.
- [ ] knowledge:123@v1 | [slice-1] D2: User may belong to >1 tenant and switch. | D2: User may belong to >1 tenant and switch.
- [ ] knowledge:124@v1 | [slice-1] D3: Roles per tenant: owner, admin, member, viewer. Bots get service identities, never a h | D3: Roles per tenant: owner, admin, member, viewer. Bots get service identities, never a human's.
- [ ] knowledge:125@v1 | [slice-1] D4: Sign-up/login uses Google OIDC via swappable provider interface (off-the-shelf library | D4: Sign-up/login uses Google OIDC via swappable provider interface (off-the-shelf library, not hand-rolled). Existing per-user tokens remai...
- [ ] knowledge:126@v1 | [slice-1] D5: Project mandatory fields: name, slug, owner, purpose, type (software, operations, cont | D5: Project mandatory fields: name, slug, owner, purpose, type (software, operations, content, research, mixed).
- [ ] knowledge:127@v1 | [slice-1] D6: Project optional: north star & success measures, stakeholders & approvers, systems & r | D6: Project optional: north star & success measures, stakeholders & approvers, systems & repos, constraints (budget, compliance, deadlines),...
- [ ] knowledge:128@v1 | [slice-1] D7: Profiling interview fills gaps. Model suggestions marked agent-sourced until user conf | D7: Profiling interview fills gaps. Model suggestions marked agent-sourced until user confirms. Approved profile versioned; every task recor...
- [ ] knowledge:129@v1 | [slice-1] D8: Every record carries tenant_id / project_id / task_id. Cross-tenant/project access ref | D8: Every record carries tenant_id / project_id / task_id. Cross-tenant/project access refused unless explicitly granted.
- [ ] knowledge:165@v1 | [slice-2] D9: Persistent, durable project memory. Entries: burst, note, decision, rule, chat, corres | D9: Persistent, durable project memory. Entries: burst, note, decision, rule, chat, correspondence, commentary, blob, snapshot.
- [ ] knowledge:166@v1 | [slice-2] D10: Append-only and versioned. Update = new version, archive hides, nothing hard-deleted. | D10: Append-only and versioned. Update = new version, archive hides, nothing hard-deleted. History answerable ("what did we believe on date ...
- [ ] knowledge:167@v1 | [slice-2] D11: Visibility set by creator: restricted (named users/roles), tenant-wide, or public (an | D11: Visibility set by creator: restricted (named users/roles), tenant-wide, or public (any signed-in Rouvr user, never anonymous).
- [ ] knowledge:168@v1 | [slice-2] D12: Rouvr and bots use knowledge via first-class tools: get, search, create, update, arch | D12: Rouvr and bots use knowledge via first-class tools: get, search, create, update, archive, link. Scope from session, never from argument...
- [ ] knowledge:169@v1 | [slice-2] D13: Provenance on every entry (author, source, time). Agent-written entries start unverif | D13: Provenance on every entry (author, source, time). Agent-written entries start unverified until reviewer/user confirms.
- [ ] knowledge:170@v1 | [slice-2] D14: Rules and standing instructions are authority. Bind only after owner approval (admins | D14: Rules and standing instructions are authority. Bind only after owner approval (admins may approve if delegated). Tasks/receipts record ...
- [ ] knowledge:171@v1 | [slice-2] D15: Separate curator agent runs on schedule (nightly default) to dedupe, compact, tag, li | D15: Separate curator agent runs on schedule (nightly default) to dedupe, compact, tag, link and version. Never deletes, may only propose ru...
- [ ] knowledge:172@v1 | [slice-2] D16: Session commands: store (save point), compact (structured summary with decisions, ope | D16: Session commands: store (save point), compact (structured summary with decisions, open questions, next steps, linked to raw transcript ...
- [ ] knowledge:173@v1 | [slice-2] D17: Demo runs single machine, multiple SQLite DBs as needed (auth/tenants, ledger, knowle | D17: Demo runs single machine, multiple SQLite DBs as needed (auth/tenants, ledger, knowledge per tenant). Blobs on disk, content-addressed,...
- [ ] knowledge:174@v1 | [slice-2] D18: Search keyword + meaning-based. Embeddings local model default, swappable (Meta Model | D18: Search keyword + meaning-based. Embeddings local model default, swappable (Meta Model API).
- [ ] knowledge:175@v1 | [slice-2] D19: Stay behind existing WorkHub interface so same contract can later run on full WorkHub | D19: Stay behind existing WorkHub interface so same contract can later run on full WorkHub. Model Lite on full WorkHub knowledge design wher...
- [ ] knowledge:176@v1 | [slice-2] D20: Tenant-neutral, no customer-specific logic in core. | D20: Tenant-neutral, no customer-specific logic in core.

## Standing rules
- [ ] knowledge:5@v1 | Rule (proposed by agent): always cite | Every answer using project knowledge must cite knowledge:<id>@v<n>.
- D14 (knowledge:170@v1) is the rules-authority decision: profile standing rules migrate to verified rule entries once approved.

## Related detail entries (NOT canonical - do not confirm as the decision)
These cite D-numbers but are refinements from owner replies / research sections:
- knowledge:142@v1 | [slice-1] D1/D2: A user belongs to many tenants via memberships, switches active tenant per request/ | detail, not the canonical D entry
- knowledge:143@v1 | [slice-1] D3: Roles owner/admin/member/viewer enforced server-side per tenant from one permission ma | detail, not the canonical D entry
- knowledge:144@v1 | [slice-1] D4: Google OIDC via a provider interface; per-user local tokens keep working unchanged as | detail, not the canonical D entry
- knowledge:145@v1 | [slice-1] D5/D6: Project create refuses missing mandatory fields (name, slug, owner, purpose, type); | detail, not the canonical D entry
- knowledge:146@v1 | [slice-1] D7: Profile has draft → approved versions, immutable once approved, field-level provenance | detail, not the canonical D entry
- knowledge:147@v1 | [slice-1] D8: Every ledger row, workflow input, and on-disk record resolves to tenant/project/task; | detail, not the canonical D entry
- knowledge:182@v1 | [slice-2] Storage layout (D17): `.data/knowledge/<tenant_id>.db` per tenant (WAL), `.data/knowle | detail, not the canonical D entry
- knowledge:184@v1 | [slice-2] Visibility enforcement (D11/D12, S2): one `visible_to(principal)` predicate applied *i | detail, not the canonical D entry
- knowledge:185@v1 | [slice-2] Verification & rules (D13/D14): agent-written versions start `unverified`; confirm is | detail, not the canonical D entry
- knowledge:186@v1 | [slice-2] Search (D18): FTS5 BM25 + sqlite-vec vectors in the tenant DB, merged by reciprocal-ra | detail, not the canonical D entry
- knowledge:187@v1 | [slice-2] Sessions (D16): `store` appends a `chat` version + checkpoint; `compact` is a model-wr | detail, not the canonical D entry
- knowledge:188@v1 | [slice-2] Curator (D15): a tenant-scoped service identity (Slice 1) running an Ops-style Tempora | detail, not the canonical D entry
- knowledge:189@v1 | [slice-2] WorkHub interface (D19/D20): REST mirrors `/projects/{project_id}/knowledge[/search]` | detail, not the canonical D entry

## Confirm command (run once after approval; do NOT run before)
```sh
for id in 122 123 124 125 126 127 128 129 165 166 167 168 169 170 171 172 173 174 175 176 5; do curl -s -X POST -H "X-Rouvr-Token: $(cat .data/api-token)" -H "Content-Type: application/json" -d "{}" http://127.0.0.1:8221/v1/knowledge/$id/confirm; echo; done
```
