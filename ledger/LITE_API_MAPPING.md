# Lite API mapping (FL-A3) — wrap, don't duplicate

Lite router: `ledger/lite.py::mount_lite_routes`, wired in
`ledger/app.py::make_app`. Every Lite route reuses the **same endpoint
function object** as its ledger counterpart — no duplicated handler
bodies, no divergent validation. Code source of truth for this table is
`ledger/lite.py::MAPPING` (method, ledger path, handler name); the
contract tests assert each row exists on both prefixes with identical
endpoint identity.

D19-compatible: stable `/api/v1` prefix matching the WorkHub contract
router (`APIRouter(prefix="/api/v1")`); ledger request/response shapes
are retained so the same contract can later run on full WorkHub. Auth
parity: the ledger auth middleware canonicalizes `/api/v1/*` back to
its ledger path before the guard, so guarded/open status is identical
on both prefixes — open on both: `onboard/register`, `onboard/login`,
`health`. Intentionally unaliased: `GET /demo/onboard` (demo page, not
API).

| Lite endpoint | Method | Ledger handler (`ledger/app.py`) | Notes |
|---|---|---|---|
| `/api/v1/health` | GET | `GET /health` (`health`) | auth-open alias, unchanged shape |
| `/api/v1/users` | POST | `POST /v1/users` (`create_user`) | stack-only |
| `/api/v1/users` | GET | `GET /v1/users` (`list_users`) | stack-only |
| `/api/v1/tenants` | POST | `POST /v1/tenants` (`create_tenant`) | idempotent register |
| `/api/v1/projects` | POST | `POST /v1/projects` (`create_project`) | idempotent register |
| `/api/v1/projects` | GET | `GET /v1/projects` (`list_projects`) | tenant filter |
| `/api/v1/users/{username}/disable` | POST | `POST /v1/users/{username}/disable` (`disable_user`) | stack-only |
| `/api/v1/onboard/register` | POST | `POST /v1/onboard/register` (`onboard_register`) | auth-open (pre-account) |
| `/api/v1/onboard/login` | POST | `POST /v1/onboard/login` (`onboard_login`) | auth-open (pre-account) |
| `/api/v1/onboard/complete` | POST | `POST /v1/onboard/complete` (`onboard_complete`) | hyeve JWT or token |
| `/api/v1/knowledge` | POST | `POST /v1/knowledge` (`create_knowledge`) | latest-only read paths |
| `/api/v1/knowledge/search` | GET | `GET /v1/knowledge/search` (`search_knowledge`) | FTS + fuzz RRF |
| `/api/v1/knowledge/{key}` | GET | `GET /v1/knowledge/{key}` (`get_knowledge`) | id or slug |
| `/api/v1/knowledge/{kid}/history` | GET | `GET /v1/knowledge/{kid}/history` (`knowledge_history`) | versions |
| `/api/v1/knowledge/{kid}/versions` | POST | `POST /v1/knowledge/{kid}/versions` (`version_knowledge`) | new version |
| `/api/v1/knowledge/{kid}/confirm` | POST | `POST /v1/knowledge/{kid}/confirm` (`confirm_knowledge`) | verify flag |
| `/api/v1/knowledge/store` | POST | `POST /v1/knowledge/store` (`knowledge_store`) | session store |
| `/api/v1/knowledge/compact` | POST | `POST /v1/knowledge/compact` (`knowledge_compact`) | session compact |
| `/api/v1/knowledge/snapshot` | POST | `POST /v1/knowledge/snapshot` (`knowledge_snapshot`) | snapshot |
| `/api/v1/knowledge/store-and-close` | POST | `POST /v1/knowledge/store-and-close` (`knowledge_store_and_close`) | store + close |
| `/api/v1/knowledge-rules` | GET | `GET /v1/knowledge-rules` (`knowledge_verified_rules`) | verified rules |
| `/api/v1/knowledge/curate` | POST | `POST /v1/knowledge/curate` (`knowledge_curate`) | curator suggestions |
| `/api/v1/tasks` | POST | `POST /v1/tasks` (`create_task`) | scope tenant+project required |
| `/api/v1/tasks_with_contract` | POST | `POST /v1/tasks_with_contract` (`create_task_with_contract`) | atomic create+bind |
| `/api/v1/tasks` | GET | `GET /v1/tasks` (`list_tasks`) | state/tenant filter |
| `/api/v1/tasks/{tid}` | GET | `GET /v1/tasks/{tid}` (`get_task`) | + contracts/evidence/receipts |
| `/api/v1/tasks/{tid}/contracts` | POST | `POST /v1/tasks/{tid}/contracts` (`add_contract`) | dedupe by sha |
| `/api/v1/grants` | POST | `POST /v1/grants` (`create_grant`) | stack-only |
| `/api/v1/grants/active` | GET | `GET /v1/grants/active` (`active_grant`) | newest unexpired |
| `/api/v1/tasks/{tid}/evidence/lookup` | GET | `GET /v1/tasks/{tid}/evidence/lookup` (`lookup_evidence`) | fence lookup |
| `/api/v1/tasks/{tid}/evidence` | POST | `POST /v1/tasks/{tid}/evidence` (`add_evidence`) | append-only |
| `/api/v1/tasks/{tid}/transition` | POST | `POST /v1/tasks/{tid}/transition` (`transition`) | idempotent, done needs contract+evidence |
| `/api/v1/tasks/{tid}/binding` | GET | `GET /v1/tasks/{tid}/binding` (`get_binding`) | active assignment+lease |
| `/api/v1/tasks/{tid}/assignments/claim` | POST | `POST /v1/tasks/{tid}/assignments/claim` (`claim_assignment`) | idempotent claim |
| `/api/v1/tasks/{tid}/leases/renew` | POST | `POST /v1/tasks/{tid}/leases/renew` (`renew_lease`) | epoch-guarded |
| `/api/v1/tasks/{tid}/assignments/reclaim` | POST | `POST /v1/tasks/{tid}/assignments/reclaim` (`reclaim_assignment`) | expired only |
| `/api/v1/tasks/{tid}/attempts` | POST | `POST /v1/tasks/{tid}/attempts` (`open_attempt`) | epoch must match lease |
| `/api/v1/tasks/{tid}/results` | POST | `POST /v1/tasks/{tid}/results` (`record_result`) | verdict + core |
| `/api/v1/workers/heartbeat` | POST | `POST /v1/workers/heartbeat` (`worker_heartbeat`) | stack-only |
| `/api/v1/events` | GET | `GET /v1/events` (`events`) | SSE replay, tenant-scoped |

Contract tests: `ledger/test_lite_contract.py` (alias identity,
functional parity, error parity, auth parity, OpenAPI coverage).
