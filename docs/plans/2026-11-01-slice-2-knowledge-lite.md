# Roadmap Slice 2 — Knowledge Lite (assuming Slice 1 exists)

- **Status: WORK IN PROGRESS** — proposal stored 2026-11-01, awaiting owner review (Chunk 3 to follow) and the owner's R1 decision at the S031 gate below. Not approved for implementation; no code started.
- Provenance: planning session 2026-11-01 (Chunk 2 brief, decisions D9–D20 binding, research R1–R3, owner success criteria S2–S5).
- Predecessor: `2026-11-01-slice-1-identity-tenancy-projects.md` (identities, memberships, roles, service accounts, versioned project profiles assumed landed).

## Goal

Durable, append-only, versioned project memory behind the WorkHub interface: nine entry kinds, creator-set visibility, provenance with unverified-until-confirmed agent writes, owner-approved rules as binding authority stamped into tasks/receipts, hybrid keyword+semantic search with citations, session store/compact/snapshot, and a kill-safe nightly curator. Single machine, per-tenant SQLite, blobs on disk.

## Binding decisions (owner, D9–D20)

- D9: Persistent, durable project memory. Entries: burst, note, decision, rule, chat, correspondence, commentary, blob, snapshot.
- D10: Append-only and versioned. Update = new version, archive hides, nothing hard-deleted. History answerable ("what did we believe on date X").
- D11: Visibility set by creator: restricted (named users/roles), tenant-wide, or public (any signed-in Rouvr user, never anonymous).
- D12: Rouvr and bots use knowledge via first-class tools: get, search, create, update, archive, link. Scope from session, never from arguments.
- D13: Provenance on every entry (author, source, time). Agent-written entries start unverified until reviewer/user confirms.
- D14: Rules and standing instructions are authority. Bind only after owner approval (admins may approve if delegated). Tasks/receipts record rule versions used. Profile standing rules become approved rule entries.
- D15: Separate curator agent runs on schedule (nightly default) to dedupe, compact, tag, link and version. Never deletes, may only propose rule changes (to tenant owner), safe to kill and rerun.
- D16: Session commands: store (save point), compact (structured summary with decisions, open questions, next steps, linked to raw transcript citing message ids), snapshot (restorable state), store-and-close (snapshot+compact then end). New sessions open from latest snapshot.
- D17: Demo runs single machine, multiple SQLite DBs as needed (auth/tenants, ledger, knowledge per tenant). Blobs on disk, content-addressed, owner-only perms.
- D18: Search keyword + meaning-based. Embeddings local model default, swappable (Meta Model API).
- D19: Stay behind existing WorkHub interface so same contract can later run on full WorkHub. Model Lite on full WorkHub knowledge design where sensible.
- D20: Tenant-neutral, no customer-specific logic in core.

Owner success criteria: S2 zero cross-tenant/visibility leaks in adversarial test set (including search results, snippets, links); S3 knowledge written in one session is retrieved and cited in a later one, unverified entry never applied as rule; S4 store-and-close then resume restores same open tasks, profile version, decisions; S5 curator run killed midway and rerun produces no duplicates and no deletions.

## Views (co-founder hat)

- **Knowledge is the asset; auth was a commodity.** That reverses Slice 1's logic. Buy auth (Authlib), but the knowledge store is where Rouvr's governance story lives — versioning, "what did we believe on X", unverified vs binding rules, receipts citing rule versions. Every framework below optimises *recall quality*; none treats *authority and audit* as first-class. Adopting one as system-of-record means fighting its mutation model forever.
- **The leak surface is the product risk, not retrieval quality.** S2 will be lost in snippets, link titles, embedding indexes, and the public corpus — not in `get`. Design and budget accordingly: probably a third of this slice is adversarial work.
- **Rules are the differentiator, memory is table stakes.** A competitor can bolt on a memory library in a weekend. "This task ran under rule set v7, approved by the owner, here is the signed receipt" is not bolt-on-able. Fund D14 like a feature, not plumbing.
- **The curator is the most dangerous agent we'll ship.** A helpful agent that silently compacts meaning is a corruption engine. It gets proposer rights, deterministic idempotent operations first, and meaning-changing output only as unverified new versions.
- **Say no:** no graph database, no always-on extraction pipeline rewriting what users wrote, no cross-tenant blob deduplication (a shared content hash is an existence oracle), no sending restricted text to a remote embedder by default.

## Part A — Research

### R1: Mem0 vs Zep/Graphiti vs Letta vs LlamaIndex vs Cognee vs own SQLite

Licences/maturity verified via GitHub API on 2026-11-01; capabilities from official docs (Sources).

**Mem0** (Apache-2.0, ~68k stars, actively pushed). Self-hostable library/server; data on your infra; OSS defaults are local Qdrant + SQLite history store; LLM/embedder/vector store are swappable. Memories are LLM-extracted facts scoped by `user_id`/`agent_id`/`run_id` + metadata filters. Against D9–D18: it has add/search but its model is *facts extracted from chat*, not nine governed entry kinds; updates/deletes are normal operations, not immutable version chains with as-of queries; there is no restricted/tenant/public visibility model, no unverified→verified state, no rule authority; graph memory is Platform-only, not OSS. Fit: a possible future derived fact-extraction aid. **Not a system of record.**

**Zep / Graphiti** (Graphiti core Apache-2.0, ~32k stars; Zep itself is the managed/commercial layer). The strongest temporal model here: bi-temporal facts with invalidation, episode-based provenance, hybrid semantic+BM25+graph search, point-in-time queries — genuinely close to D10's "what did we believe." Blockers: it requires Neo4j, FalkorDB, or Neptune (no SQLite), violating D17's single-machine operating model; it stores entity/fact triplets extracted by an LLM, not documents, blobs, chats, or snapshots; partitioning is a coarse group id, not per-entry visibility; extraction cost/latency runs on every write. **Borrow its ideas (bi-temporal semantics, episode provenance, hybrid fusion); do not adopt.** A derived graph index can be revisited post-Lite if entity questions dominate real usage.

**Letta** (Apache-2.0, ~25k stars). A stateful-agent *platform*: adopting it means adopting a second agent runtime alongside Temporal and our personas. Its server requires Postgres + pgvector. Archival memory is agent-scoped semantic passages; the SDK can update/delete them; memory blocks are an interesting pattern for D16 snapshots. Wrong layer entirely — it competes with Rouvr's core rather than serving it. **Reject; steal the memory-block/snapshot ergonomics.**

**LlamaIndex** (MIT, ~53k stars). A framework, not a system: docstore/index-store/vector-store abstractions with persistence, but versioning, permissions, provenance states, and rule authority would all still be ours to build — on top of a very large dependency surface. Its loaders/parsers may be useful later for ingesting correspondence and blob formats behind our own interface. **Not the core; possible ingestion library later.**

**Cognee** (Apache-2.0, ~33k stars, very actively developed) — the strongest "and better" candidate, and the only credible alternative to building. Self-hosted, local defaults (SQLite relational, embedded vector/graph stores), MCP server, dataset-level permissions and multi-user mode, swappable LLM/embedder including Fastembed. Against it: `forget` is a hard delete (D10 forbids); datasets are not our tenant/project/entry-visibility model; memory is an LLM-built knowledge graph, so what you get back is derived, not the versioned entry you wrote — as-of reconstruction and rule-version stamping don't map; it ships its own user model duplicating Slice 1 identity; it's young and churning (v1.0 surface still moving). **Credible Plan B, but it would become a second system to govern.**

**Own SQLite** (our existing pattern). We already operate SQLite in WAL mode with canonical-JSON+sha256 versioning for contracts in `ledger/app.py:36`, append-only evidence/receipts, and 0600/0700 `.data` discipline. SQLite gives us FTS5 (BM25, snippets) natively and sqlite-vec (Apache-2.0/MIT, pure C, partition-key columns) for vectors in the same file — one file per tenant *is* the isolation boundary, which no framework above gives us. Costs are real: we own chunking, hybrid ranking, and re-embedding; sqlite-vec is pre-v1 (mitigated below); no graph traversal. But every D10–D14 invariant is directly expressible and testable.

**Recommendation (owner decides): build the system of record on per-tenant SQLite; treat any framework as, at most, a future rebuildable derived index.** Sprint S031 below is a timeboxed decision gate: a 3-day Cognee spike against the same S2/S3 fixtures, so Plan B gets a fair, evidence-based hearing before we commit. If Cognee can demonstrate entry-version fidelity, as-of queries, and zero-leak visibility on our fixtures, the recommendation changes.

### R2: OIDC library + membership model vs S022

Confirmed from Slice 1: Authlib (Starlette/FastAPI client, Google discovery) behind an `IdentityProvider` protocol fits, and knowledge consumes Slice 1 principals only (`user_id`, service-account id, tenant role) — Knowledge Lite adds no identity tables. Naming migration needed: S022's `tenant`/`project` strings become slugs beside ULID ids, `users.tenant` is replaced by memberships, and knowledge DB files are named by immutable `tenant_id` (never slug, so renames are free). WorkHub's historical `tenant_id`/`project_id` naming (below) matches the new IDs, which eases D19.

### R3: Full WorkHub knowledge model — mirror what, drop what

What exists in `core/backend` (`scripts/wh_knowledge_lint.py:43`): knowledge lives as artefacts behind `GET/POST/PATCH /projects/{project_id}/knowledge` with `…/knowledge/search?q=`, fields `uri` (`wh://…`), `kind`, `title/label`, `content`, `tags`, and `metadata_json` carrying `source`, `source_task_number`, `source_docs`, `last_verified_at`; scope normalisation splits `project_specific` vs `shared_common` with `retrieval{include, rank_weight}` (`scripts/normalize_workhub_knowledge_scope.py:48`); dedupe marks a canonical entry and archives the secondary — never deletes (`scripts/wh_knowledge_dedupe_apply.py:32`). The AIX side adds project memory with kinds, provenance, confidence, supersede-never-delete (`aix_core/runtime/executor/project_memory.py:26`), a promotion ledger `proposed → accepted` requiring approval (`aix_core/runtime/executor/memory_promotion_ledger.py`), and session capsules mapped to `context_pack_document` artefacts with stale/ahead version checks (`session_factory/knowledge.py:27`).

Mirror: the `/projects/{id}/knowledge` API shape and `wh://` URIs (D19 compatibility), provenance fields (`source`, `source_task`, `last_verified_at`) promoted to real columns, project-specific vs shared ranking as a weighting input, canonical+archive dedupe, promotion-ledger semantics for D13/D14, capsule format for D16 snapshots. Drop: free-form `metadata_json` as the schema (the lint script exists because it drifted), PATCH-in-place updates (violates D10), in-memory stores, and CodeIntel coupling. Note: the full WorkHub client in-repo is retired (`core/backend/clients/workhub_knowledge_client.py:10`), so D19 means matching the *documented API contract in these scripts*, not a live service we can test against today.

## Key decisions (builder hat)

- **Storage layout (D17):** `.data/knowledge/<tenant_id>.db` per tenant (WAL), `.data/knowledge/blobs/<tenant_id>/<sha256-prefix>/…` (0600, no cross-tenant dedupe), plus one derived `public-index.db` holding *only* public entries' searchable text/embeddings, rebuilt from tenant DBs on demand. Public visibility (D11) across per-tenant DBs is the one place fan-out would hurt; a derived, rebuildable public index keeps queries single-file and concentrates the leak surface where tests can hammer it.
- **Core schema (one model, nine kinds):** `entries(id, tenant_id, project_id?, kind, uri, visibility, created_by_principal, created_at, archived_at)`, `versions(entry_id, version, title, body/blob_ref, body_sha256, meta, author, source, created_at)`, `events(entry_id, type[created|versioned|archived|verified|rule_approved|linked|tagged], actor, at, payload)` — as-of queries replay versions+events, so "believed on X" = latest version created ≤X, not archived ≤X, with verification status as of X. `acl(entry_id, principal_or_role)` for restricted; `links(from_entry, to_entry, kind)`; `tags`; `chunks(version, span, text)` for citations; `embeddings(chunk, model_id, vector blob)`. Kind-specific bodies validated per kind (blob→hash ref, chat→transcript ref, snapshot→state JSON, rule→instruction text + scope).
- **Visibility enforcement (D11/D12, S2):** one `visible_to(principal)` predicate applied *inside* every query — keyword, vector, link traversal, as-of — with filtering before ranking and snippet generation only after ACL passes. Invisible linked entries render as absent (no title, no existence oracle). Restricted embeddings never enter the public index. MCP tools take **no scope arguments**: tenant/project come from the Slice-1 session (active tenant + session's bound project), and tools refuse unbound sessions where a project is required.
- **Verification & rules (D13/D14):** agent-written versions start `unverified`; confirm is an event by a human or reviewer principal. Retrieval returns status with every hit; context assembly for tasks puts only verified content in the authority section. Rules bind via a versioned **rule manifest** (rule entry ids + versions + hash) approved by owner (delegatable to admin, recorded); task creation stamps the manifest — extending the Slice-1 profile stamp — and the signed receipt links in `rouvr/receipts.py:31` gain the manifest hash. Slice-1 profile standing rules migrate to `rule` entries needing one explicit owner approval to bind.
- **Search (D18):** FTS5 BM25 + sqlite-vec vectors in the tenant DB, merged by reciprocal-rank fusion, deterministic tie-break. Raw vectors also stored as plain blobs, so the index is rebuildable and sqlite-vec (pre-v1) stays behind a `VectorIndex` interface with a brute-force fallback. `Embedder` interface with pinned model id + dimension: default local Fastembed (Apache-2.0, ONNX, no torch; model cached in `.data` after first download — air-gapped installs must pre-seed it). Meta Model API adapter only after verifying at build time that it actually offers an embeddings endpoint; remote embedding of restricted entries is off unless the tenant opts in. Hits cite `entry_id@version + chunk span` (S3).
- **Sessions (D16):** `store` appends a `chat` version + checkpoint; `compact` is a model-written structured entry (decisions, open questions, next steps) linked to the raw transcript blob citing muse session-log message ids (records carry `id`/`sequence`/`recorded_at`); `snapshot` is a `snapshot` entry (tenant, project, profile version, rule manifest, open task ids, muse session id, capsule fields mirrored from the AIX capsule); `store-and-close` = compact+snapshot, then supervisor bootstrap opens new sessions from the latest snapshot. Transcripts pass the existing secret-redaction scanner before storage.
- **Curator (D15):** a tenant-scoped service identity (Slice 1) running an Ops-style Temporal workflow on a nightly schedule gated by a standing grant (`curator:<tenant_id>`, reusing the T5 grant pattern in `rouvr/starter.py:78`). A `curator_runs` ledger with cursor + idempotency key `(entry_version, op, model_id)` makes kill-and-rerun safe. Auto-applies only reversible, non-semantic ops as new versions/events (exact-hash dedupe → canonical+archive flag, tags, link suggestions, re-embedding); compactions are new *unverified* versions; rule changes are proposals to the owner, never applied.
- **WorkHub interface (D19/D20):** REST mirrors `/projects/{project_id}/knowledge[/search]` with WorkHub field names mapped in one adapter layer; core code speaks only Slice-1 IDs and contains zero tenant-specific logic.

## Steps (sprint sheets S031+, tests-first, each shippable)

1. **S031 — Decision gate & contracts** (~4d). Cognee spike vs SQLite prototype on shared S2/S3 fixtures; ADR recording owner's R1 decision; entry/version/event schema, visibility matrix, as-of semantics, leak threat model; verify FTS5/sqlite-vec availability in our Python 3.12 build. Gate: owner decision before S032 starts.
2. **S032 — Knowledge core store** (~6–7d). Per-tenant DB + migrations, nine kinds with validation, append-only versions/events, archive, as-of, ACL predicate, links, per-tenant CAS blobs. Keyword-agnostic.
3. **S033 — Tools & WorkHub API** (~5d). REST adapter + MCP tools `knowledge_get/search/create/update/archive/link/confirm` (session-scoped, no scope args), FTS5 keyword search with cited spans, provenance/verification surfaced in every response.
4. **S034 — Rules authority** (~4d). Rule approval/delegation events, rule manifest, profile standing-rules migration, task + receipt stamping, authority-only context assembly.
5. **S035 — Semantic search & public index** (~5–6d). Chunking, Embedder interface + local Fastembed, sqlite-vec behind interface, hybrid ranking, re-embed on model swap, public-index build/merge.
6. **S036 — Sessions** (~4–5d). store/compact/snapshot/store-and-close MCP+CLI surface, transcript blob + message-id citations, resume-from-snapshot bootstrap in the supervisor flow.
7. **S037 — Curator** (~5d). Grant-gated nightly workflow, run ledger/idempotency, deterministic ops first, LLM ops as unverified versions, owner proposal queue.
8. **S038 — Adversarial gate & live proof** (~4–5d). The S2–S5 suite on the real stack (below), seed demo corpus, docs/runbook.

Total roughly 37–42 builder-days. Critical path S031→S032→S033→S034; S035–S037 parallelise after S033.

## Validation plan (owner criteria as gates)

- **S2 zero leaks:** an adversarial fixture set run in CI — two tenants, restricted entries named with unique canary tokens; attack via keyword search, semantic search (paraphrase queries), snippets/highlights, link traversal, as-of, public index, blob hash guessing, curator output, and snapshot resume. Every surface must return neither content, title, nor existence. This suite is a merge gate from S032 onward, not an S038 afterthought.
- **S3 cross-session:** session A writes a decision; store-and-close; fresh session B retrieves and *cites* it (entry@version+span recorded in task evidence); an unverified agent entry phrased as an instruction is retrieved as unverified and provably absent from the task's rule manifest.
- **S4 resume:** scripted store-and-close → new session → assert identical open tasks, profile version, rule manifest hash, and decision list.
- **S5 curator kill/rerun:** kill mid-run (SIGKILL worker), rerun → row counts and hashes show zero duplicate versions, zero deletions; run ledger explains every applied op.
- Standing gates: public test cut stays green; every sprint adds its module tests (`tests/test_knowledge_*.py`).

## Risks / open questions

- **Owner decision (R1):** own SQLite (recommended) vs Cognee-backed. Decided at the S031 gate with spike evidence, not before — but note that choosing Cognee reopens identity (its user model vs Slice 1) and D10 (hard delete) and would reshape S032–S035.
- **Assumption:** entries are project-scoped by default with optional tenant-level entries (`project_id` null) for org-wide knowledge; public entries retain their origin tenant/project for provenance. Say if knowledge should be project-only.
- **Assumption:** local embedding model is an English-first small model for speed; multilingual corpora need the larger multilingual variant and a re-embed. Flag if demo tenants are multilingual.
- **Risk:** sqlite-vec is pre-v1; mitigated by the interface + stored raw vectors + rebuild test, but a breaking change could force a pin-and-vendor decision.
- **Risk:** transcripts are secret-bearing by nature; redaction-before-store plus restricted-by-default visibility for `chat`/`snapshot` kinds is the mitigation, and S038 tests it with planted canary secrets.
- **Risk:** append-only growth. Archive hides but bytes remain; per-tenant blob dedupe inside the tenant bounds the worst case, and a future compaction/export story is explicitly post-Lite.

## Sources

- Mem0 Open Source overview — https://docs.mem0.ai/open-source/overview ; configuration — https://docs.mem0.ai/open-source/configuration ; add memory/scoping — https://docs.mem0.ai/core-concepts/memory-operations/add.md ; licence/maturity — https://github.com/mem0ai/mem0
- Graphiti overview — https://help.getzep.com/graphiti/getting-started/overview ; quick start (backends) — https://help.getzep.com/graphiti/getting-started/quick-start ; https://github.com/getzep/graphiti
- Letta archival memory — https://docs.letta.com/v1-sdk/memory/archival-memory ; self-hosting/Postgres — https://docs.letta.com/v1-sdk/docker ; https://github.com/letta-ai/letta
- LlamaIndex storing — https://developers.llamaindex.ai/python/framework/module_guides/storing/ ; https://github.com/run-llama/llama_index
- Cognee introduction — https://docs.cognee.ai/getting-started/introduction ; setup configuration — https://docs.cognee.ai/setup-configuration/overview ; permissions — https://docs.cognee.ai/setup-configuration/permissions ; https://github.com/topoteretes/cognee
- sqlite-vec — https://raw.githubusercontent.com/asg017/sqlite-vec/main/README.md ; SQLite FTS5 — https://sqlite.org/fts5.html ; Fastembed — https://github.com/qdrant/fastembed

