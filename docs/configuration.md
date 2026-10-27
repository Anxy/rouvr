# Configuration

Copy `.env.example` to `.env`. Secrets are read from the environment only; never commit `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `ROUVR_MODEL_PROVIDER` | `stub` | `stub` (offline), `meta` (Meta Model API), `ollama` (local open weights) |
| `META_MODEL_API_KEY` | – | Key for the Meta Model API when provider is `meta` |
| `OLLAMA_MODEL` | – | Model name for Ollama, e.g. `llama3.1` |
| `HYEVE_AIX_SERVICE_*` | see `.env.example` | Core service settings (database, Redis, Temporal) |

`PYTHONPATH` must include `.:core:core/src:core/libs`; the Makefile sets it. See `.env.example` for the full list.
