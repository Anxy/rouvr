# workers

Adapters that do the actual work. Each one is driven by core or the lane and reports back evidence.

| Worker | Where | Status |
|---|---|---|
| Meta Model API (Llama): compile / narrate / judge | `workers/meta_worker/` | Stub adapter over the pluggable provider. Key comes only from `META_MODEL_API_KEY`. |
| SDK worker (Node) | `workers/sdk-worker/` | Carried over from the private repo. |
| Muse Code session adapter | `core/backend/aix_core/framework/execution/muse_execute_adapter.py`, `muse_session.py` | Stays in core because it is imported as `backend.*`. |
| Codex / Claude headless adapter | `lane/tools/canary_model_adapter.py` | Spawns `codex exec` / `claude -p` with no TTY. |
