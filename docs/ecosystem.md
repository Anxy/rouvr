# Ecosystem: Rouvr on Hyeve

Rouvr was developed inside eBizneeds' Hyeve platform (it was formerly called "AIX"). The production deployment depends on several Hyeve services. This public repo replaces them with lightweight local stand-ins so it runs on its own.

| Hyeve service | What it does in production | Standalone replacement here |
|---|---|---|
| WorkHub | System of record: tasks, contracts, leases, results, reviews, evidence, receipts. Bots are stateless; all state lives here. | `ledger/` (WorkHub-lite: tasks, hashed contracts, append-only evidence, idempotent receipts; refuses `done` without contract + evidence). No auth, leases or multi-tenancy. |
| Hyeve Agent SDK | Worker runtime used by bots to claim work and report results | `workers/` |
| Comm / Notification service | Outbound email/notifications, returning provider message ids used for readback verification | File outbox (`demo/.outbox/`) |
| Auth service | Principals, tokens, tenancy; enforces builder ≠ reviewer by identity | Not shipped |
| OpsBus | Event bus for wakes and operational events | Not shipped |
| Integration service | Model/tool integrations | `core/backend/rouvr_models/` (Meta Model API, Ollama, stub) |
| CodeIntel | Code indexing for software-delivery tasks | Not shipped |
| Hyeve Core | Shared Python framework | Vendored at `core/libs/hyeve_core/` |

Internal names (`aix_core`, `hyeve_aix_service`, `HYEVE_AIX_SERVICE_*`) remain in the code to avoid breaking imports. Core backend tests that need the unshipped services fail outside the private deployment; that is why CI runs only `make test-public`.
