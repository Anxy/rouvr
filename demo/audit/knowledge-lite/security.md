# Knowledge Lite — read-only security audit

Date: 2026-11-02. Scope: `ledger/app.py` knowledge endpoints + auth middleware,
`ledger/hyeve_auth.py`, `rouvr/mcp_server.py` knowledge tools,
`ledger/schema.sql` knowledge tables, `tests/test_knowledge_lite.py` +
`tests/test_visibility_fuzz.py` + `tests/test_fail_closed.py`.

Method: code review plus live probes with `fastapi.testclient.TestClient`
against throwaway SQLite DBs under `/tmp` only (`ROUVR_DATA_DIR` pointed at
`/tmp`; `.data/` untouched). No source, test, or config file was modified.
Existing suites re-run read-only (`-p no:cacheprovider`): 12/12 passed.

Verdict: **not clean — 3 findings** (1 medium integrity, 2 low).
No cross-tenant *read* leak was found: tenant isolation on reads is solid.
All findings below reproduce live and cite file:line evidence.

## Findings

### F1 (medium, integrity): any signed-in user of any tenant can append versions to any public entry

`POST /v1/knowledge/{kid}/versions`
([app.py:734](ledger/app.py:734)) resolves the old row through
`_knowledge_or_404` ([app.py:612](ledger/app.py:612)), which correctly lets a
public row be *seen* cross-tenant — but the handler then performs **no tenant
write check** before inserting the new version into the victim's
tenant/project scope ([app.py:755](ledger/app.py:755)). Only a *visibility*
change is gated to creator/admin ([app.py:750](ledger/app.py:750)); title/body
changes are not.

Live evidence (throwaway DB): `gus` (tenant `globex`) versioned `amy`'s
(tenant `acme`) public entry. Response 200, new row
`tenant=acme project=site author=gus version=2 verified=0`. Afterwards the
victim's own search returned **only** the attacker's row as latest
(`[(id=2, v2, author=gus, body='hijacked')]`), and the victim's history chain
reads `[(1, v1, amy), (2, v2, gus)]`. The old body stays retrievable by id
(append-only holds), and the row starts unverified — but search surfaces the
latest regardless of `verified`, so attacker content becomes what victims see
by default. Visibility escalation/narrowing by the outsider *is* refused
(403, verified live for `public->restricted` and `public->company`).

The existing tests never cover same-visibility cross-tenant versioning: the
canary test uses company entries, and the fuzz asserts the visibility-change
gate only. The MCP `knowledge_update` docstring ("Only the creator or an
admin changes visibility") reads as if non-creator body edits are intended,
so this may be wiki-by-design for public rows — but a cross-tenant write with
no consent, audit distinction, or opt-out is at minimum an integrity hole:
recommend a tenant-equality write check on versions (and confirm, see F3), or
an explicit documented wiki-intent plus verified-gating in search.

### F2 (low, confidentiality intra-tenant): compact snapshot leaks restricted titles tenant-wide

`POST /v1/knowledge/compact` ([app.py:797](ledger/app.py:797)) correctly
filters sources to rows the caller can see ([app.py:815](ledger/app.py:815)),
but writes the snapshot entry with **default `company` visibility**
([app.py:821](ledger/app.py:821)) whose body lists every source title
([app.py:816](ledger/app.py:816)).

Live evidence: `amy` created + confirmed a `restricted` entry
(`allowed_users=[]`, title containing `leakc01`), then compacted. `amy2`
(same tenant, not a member) `GET` the snapshot: 200, and the response body
contained the restricted title. Restricted visibility promises
creator-or-named-members ([app.py:606](ledger/app.py:606)); the snapshot
breaks that promise for titles within the tenant (bodies are not embedded).
Cross-tenant is not affected (snapshot row itself is tenant-checked on read).
Recommend inheriting the most restrictive source visibility for the snapshot,
or redacting titles of restricted sources.

### F3 (low, integrity): `confirm` has no author/admin gate — any viewer can set verified=1

`POST /v1/knowledge/{kid}/confirm` ([app.py:760](ledger/app.py:760)) checks
only readability, then flips `verified`. Live: same-tenant non-author confirm
of a company entry → 200; **cross-tenant stranger confirm of a public entry
→ 200**. Combined with F1, an outsider can write a version *and* anyone can
bless it, hollowing out the "verified = human-confirmed, binding rules"
meaning that `knowledge_search`/`knowledge-rules` consumers rely on (the
`verified` flag is carried on every read). Recommend restricting confirm to
creator/admin (mirroring the visibility gate at
[app.py:750](ledger/app.py:750)) or to same-tenant members at least.

## Clean areas (every check tried, expected → actual)

Auth / fail-closed (`_auth`, [app.py:240](ledger/app.py:240)):

- Anonymous (no token) on all 12 knowledge routes (create, search, get,
  history, versions, confirm, store, compact, snapshot, store-and-close,
  curate, rules): 401 → 401 on every route.
- Wrong `X-Rouvr-Token`: 401 → 401. `Authorization: Bearer [REDACTED]` with Hyeve
  unconfigured: 401 → 401 (fail closed,
  [hyeve_auth.py:52](ledger/hyeve_auth.py:52)). Disabled user: 401 → 401.
- Hyeve code review: unverifiable token / missing subject / wrong tenant all
  raise, never pass ([hyeve_auth.py:70](ledger/hyeve_auth.py:70)); un-onboarded
  valid subject gets a sentinel identity with no tenant, which reads at most
  public rows ([app.py:618](ledger/app.py:612)) and is refused 403 on search
  ("complete Rouvr onboarding first").

Cross-tenant reads (company + restricted): gus search for acme canaries →
`[]`; get/history → 404 (not 403/400 — no existence oracle,
[app.py:626](ledger/app.py:626)); confirm/version → 404. Search with the
victim's `project=` filter → `[]`. Fuzz matrix (3 tenants × 3 users × 3
visibilities, from `test_visibility_fuzz.py`) re-run: pass.

Cross-tenant writes (company/restricted scope): create/store/compact/
snapshot/curate/store-and-close into another tenant → 403 → 403
([app.py:665](ledger/app.py:665) and equivalents).

Visibility matrix: restricted visible to author and `allowed_users` only
(non-member same-tenant get/search/history → 404/`[]`/404; sharing via a new
version → 200 and visible); company visible tenant-wide incl. other projects
(same-tenant cross-project get/search → 200 — tenant-scoped, not
project-scoped, which matches the "company = same tenant" contract at
[app.py:604](ledger/app.py:604)); public visible to any signed-in user of any
tenant (get/search/history → 200) and never to anonymous (→ 401, "any
signed-in user, never anonymous" at [app.py:603](ledger/app.py:603)).

Visibility-change gate: non-creator `versions` with changed visibility →
403 → 403; creator and stack → 200.

Append-only/versioning: old body intact after versioning; search returns
latest only; history replays the full chain oldest-first.

FTS injection: `" ' * ( ) : ^ ~ **** '`; DROP TABLE knowledge_entries;--` `a
OR 1=1` → 200/422, never 500; store intact afterwards. (Tokens come from
`re.findall(r"\w+", q)` with per-token quoting, so operators can't escape.)

Snapshot/export scoping: own-tenant snapshot → 200, file lands under
`ROUVR_DATA_DIR`, contains only caller-visible rows of the caller's tenant.

Rules endpoint (`/v1/knowledge-rules`, [app.py:877](ledger/app.py:877)):
cross-tenant company rules excluded, public rules included, anonymous → 401.

Project listing: user sees only own tenant regardless of query
([app.py:399](ledger/app.py:399)); anonymous → 401.

MCP (`rouvr/mcp_server.py`, code review — same-process live call not
feasible without a server, paths verified 1:1): every knowledge tool
delegates to the matching ledger route with the stack token
(`_ledger`, [mcp_server.py:27](rouvr/mcp_server.py:27)), i.e. supervisor
identity that sees all — correct for the supervisor front door, and the MCP
HTTP port itself is guarded by the same token ([mcp_server.py:720](rouvr/mcp_server.py:720)).

## Minor observations (no leak, noted for hygiene)

- O1: `POST /v1/knowledge/store` accepts `company`/`public` +
  `allowed_users` with 200 (list silently ignored), while create/version 422
  on the same combination ([app.py:660](ledger/app.py:660) vs
  [app.py:780](ledger/app.py:780)). Inconsistent validation; harmless today
  because `_can_see` ignores `allowed_users` outside `restricted`.
- O2: MCP auth additionally honors `ROUVR_ALLOW_ANON=1`
  ([mcp_server.py:697](rouvr/mcp_server.py:697)), which the ledger ignores.
  Both refuse public binds. Align the two.
- O3: snapshot filename interpolates tenant/project unsanitized
  ([app.py:842](ledger/app.py:825)); tenant creation is stack-only, so this
  needs the stack token (already full access) to abuse — negligible.
- Same-tenant cross-project company reads are allowed by constructor
  (`_can_see` checks tenant only). Flagged as intended per the company
  contract, not a leak — recording here because "cross-project reads" was in
  scope.

## Verification runs

- `tests/test_knowledge_lite.py`, `tests/test_visibility_fuzz.py`,
  `tests/test_fail_closed.py`: 12/12 pass in this session
  (`-p no:cacheprovider`, no repo writes).
- Live probes: 72-check matrix + supplementary hijack/escalation runs, all
  against `/tmp` SQLite DBs; every result above is observed, not inferred.
