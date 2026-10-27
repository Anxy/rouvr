# AIX Discuss

Brings AIX into VS Code / AIX Studio's native Chat and Agent Mode:

- **`@aix`** in Chat — discuss and scope a new Objective (AIX's `/api/aix/objectives/discuss` API).
- **AIX sidebar Agent** — admit → cycle → real execution (`real_execute`), with exploration, approval, file propose/Keep, and truthful completion status.
- **AIX** in Agent Mode's model picker — routes the built-in tool-calling agent loop through `hyeve-integration-service`'s OpenAI-compatible endpoint.

## Real Execution (0.2.17)

Aligned with AIX Execution Competence (EC-004) and Real Execution:

- Reads backend-authored `execution_stages`, `final_report`, and `real_execute.objective_status` (never invents success from `phase` alone).
- **EC-08:** `phase=BLOCKED` with a stale `objective_status=COMPLETED` is shown as **Blocked**, never Completed.
- Treats `COMPLETED_FOR_REVIEW` as a finished run so Agent Mode can start a new objective.
- Surfaces `strategy_id` / capability-mismatch reason codes from the live cycle.
- On `WAITING_APPROVAL`, resumes with **approval identity** echoed from GET cycle: `plan_id`, `plan_revision`, `approval_gate_id` (plus local `approval_reference`).
- Honours mutation scope (`allowed_files`), proposed edits review (Keep/Undo), and cancellation of active runs.

## Setup

1. Command Palette → **AIX: Sign In** (real login against `hyeve-auth-service`, tokens stored in VS Code's encrypted secret storage — never in settings, never typed into any AI chat).
2. Settings → set:
   - `aix.authUrl` — your `hyeve-auth-service` base URL (e.g. `http://localhost:8222`)
   - `aix.orchestratorUrl` — AIX-Frontend BFF (e.g. `http://localhost:3000`) or direct orchestrator when gateway signing is not required
   - `aix.integrationUrl` — your `hyeve-integration-service` base URL (e.g. `http://localhost:8233`), needed for the Agent Mode model
   - `aix.tenantId` — optional, your default tenant (prefilled at sign-in)
   - `aix.repositoryId` — optional key in `OBJECTIVE_CYCLE_REPOSITORY_ROOTS`; blank uses the open workspace folder name
   - `aix.model` — optional, a specific model id for `hyeve-integration-service` to route to

## Auth flow

Both the discuss chat and Agent Mode model use the same Bearer token obtained via **AIX: Sign In** — the token is sent as `Authorization: Bearer <token>` plus `X-Hyeve-Tenant-Id: <tenant>` on every request.

## Notes

- No secrets are ever stored in `settings.json`. Sign-in tokens live in VS Code's SecretStorage and auto-refresh.
- The `@aix` discuss chat is a bounded objective-scoping conversation (no file edits, no tool calls) — it will not propose deploy/merge/schema/credential changes.
- Package with: `npm run package` from `extensions/aix-chat` → writes `../../aix-chat.vsix`.
