# Model providers

`core/backend/rouvr_models/providers.py` exposes one OpenAI-compatible `chat()` and is selected with `ROUVR_MODEL_PROVIDER`:

- `meta`: the Meta Model API (Llama API). Needs `META_MODEL_API_KEY`. Optional: `META_MODEL_API_BASE_URL`, `META_MODEL_NAME`.
- `ollama`: open-weights Llama served locally (Ollama, vLLM, or llama.cpp server).
- `openrouter`: hosted open-weights models.
- `stub`: offline and deterministic, for tests and demos.

Keys are read only from the environment. None are committed. See `.env.example`.
