"""Supervisor chat: model turn -> slice with tasks (Step 2).

``POST /v1/chat`` (aliased to ``/api/v1/chat`` by the Lite mount) streams a
single model turn as SSE ``token`` events, then materializes the plan: one
slice task (with the acceptance criteria as its contract) plus up to
``MAX_TASKS`` child tasks, each linked by evidence and each carrying a
``chat:create`` receipt whose body names the chat message id as its source.
The server appends inline markdown task cards (with real ids) to the reply.

Rules enforced server-side, not just by prompt:
- at most one clarifying question: a non-empty ``question`` wins and
  creates nothing, even if the model also emitted tasks;
- token budget: the model call passes ``MAX_TOKENS`` and the incoming
  message is capped at ``MESSAGE_LIMIT`` characters (422 beyond);
- task fan-out is clamped to ``MAX_TASKS``.

Model access mirrors the ``core/backend/rouvr_models/providers.py`` env
contract (``ROUVR_MODEL_PROVIDER`` = meta|ollama|openrouter|stub, keys from
the environment only) but is implemented here with stdlib urllib so the
ledger stays self-contained for hidden-suite worktree imports. The default
model for the ``meta`` provider is ``muse-spark-1.3``.
"""
from __future__ import annotations

import json
import os
import urllib.request

MODEL_DEFAULT = "muse-spark-1.3"
# Reasoning models spend most of the budget thinking; 1024 truncates the
# visible turn (see e2e/diag_empty.py). 2048 keeps turns complete.
MAX_TOKENS = 2048
MESSAGE_LIMIT = 4000
MAX_TASKS = 8
PROVIDER_TIMEOUT = 60.0
MAX_RETRIES = 2  # streaming attempts beyond the first when a turn comes back empty
RETRY_BACKOFF_S = (0.5, 1.0)  # short pauses between retries

SYSTEM_PROMPT = """Rouvr supervisor. Reply STRICT JSON only, no prose:
{"question": str|null, "slice": {"title": str, "intent": str,
"acceptance_criteria": [str]}|null, "tasks": [{"title": str, "intent": str}]}
Ambiguous: set only "question" (one question), null slice, empty tasks.
Clear: "question" null, slice title <80 chars with criteria, <=8 tasks."""

_PROVIDER_PRESETS = {
    "meta": ("META_MODEL_API_BASE_URL", "", "META_API_KEY", "META_MODEL_NAME", MODEL_DEFAULT),
    "ollama": ("OLLAMA_BASE_URL", "http://localhost:11434/v1", None, "OLLAMA_MODEL", "llama3.1"),
    "openrouter": ("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1",
                   "OPENROUTER_API_KEY", "OPENROUTER_DEFAULT_MODEL",
                   "meta-llama/llama-3.1-70b-instruct"),
}


def provider_from_env(env=None) -> dict:
    """Resolve the chat model from the environment. Keys never hard-coded."""
    env = os.environ if env is None else env
    name = (env.get("ROUVR_MODEL_PROVIDER") or "stub").strip().lower()
    if name == "stub":
        return {"name": "stub", "base_url": "stub://", "model": "stub", "api_key": None}
    if name not in _PROVIDER_PRESETS:
        raise ValueError(f"unknown ROUVR_MODEL_PROVIDER {name!r}; choose meta|ollama|openrouter|stub")
    url_var, url_default, key_var, model_var, model_default = _PROVIDER_PRESETS[name]
    key = env.get(key_var) if key_var else None
    if key_var and not key:
        raise RuntimeError(f"{key_var} is not set (required for provider {name!r})")
    base_url = env.get(url_var) or url_default
    if not base_url:
        raise RuntimeError(f"{url_var} is not set (required for provider {name!r})")
    return {"name": name, "base_url": base_url,
            "model": env.get(model_var) or model_default, "api_key": key}


def _stub_response(content: str) -> str:
    """Deterministic stub: a plan-shaped user message passes through so tests
    can drive both branches without a network; anything else echoes."""
    try:
        obj = json.loads(content)
    except ValueError:
        obj = None
    if isinstance(obj, dict) and any(k in obj for k in ("question", "slice", "tasks")):
        return json.dumps(obj)
    return json.dumps({"stub": True, "echo": content})


def stream_completion(messages: list[dict], provider: dict, max_tokens: int = MAX_TOKENS,
                       timeout: float = PROVIDER_TIMEOUT):
    """Yield response text chunks. Raises RuntimeError on transport failure."""
    if provider["name"] == "stub":
        body = _stub_response(messages[-1].get("content", "") if messages else "")
        step = max(1, len(body) // 3)
        for i in range(0, len(body), step):
            yield body[i:i + step]
        return
    payload = json.dumps({"model": provider["model"], "messages": messages,
                          "max_tokens": max_tokens, "stream": True}).encode()
    headers = {"Content-Type": "application/json"}
    if provider.get("api_key"):
        headers["Authorization"] = f"Bearer {provider['api_key']}"
    req = urllib.request.Request(provider["base_url"].rstrip("/") + "/chat/completions",
                                 data=payload, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except Exception as e:
        raise RuntimeError(f"model request failed: {e}")
    with resp:
        buf = ""
        for raw in resp:
            buf += raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                chunk = _sse_content(line)
                if chunk == "[DONE]":
                    buf = ""
                    break
                if chunk:
                    yield chunk
            else:
                continue
            break
        if buf.strip():
            chunk = _sse_content(buf)
            if chunk and chunk != "[DONE]":
                yield chunk


def _sse_content(line: str) -> str | None:
    """Text of one SSE data line, or None when the line carries nothing."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if data == "[DONE]":
        return "[DONE]"
    try:
        delta = json.loads(data)["choices"][0].get("delta", {})
    except (ValueError, KeyError, IndexError):
        return None
    return delta.get("content") or None


def complete_once(messages: list[dict], provider: dict, max_tokens: int = MAX_TOKENS,
                  timeout: float = PROVIDER_TIMEOUT) -> str:
    """One non-streaming turn. Last resort when streaming comes back empty."""
    if provider["name"] == "stub":
        return _stub_response(messages[-1].get("content", "") if messages else "")
    payload = json.dumps({"model": provider["model"], "messages": messages,
                          "max_tokens": max_tokens, "stream": False}).encode()
    headers = {"Content-Type": "application/json"}
    if provider.get("api_key"):
        headers["Authorization"] = f"Bearer {provider['api_key']}"
    req = urllib.request.Request(provider["base_url"].rstrip("/") + "/chat/completions",
                                 data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception as e:
        raise RuntimeError(f"model request failed: {e}")
    try:
        return data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, AttributeError):
        return ""


def parse_chat_plan(text: str) -> dict:
    """Validate one model turn. Returns {question, slice, tasks} with tasks
    clamped to MAX_TASKS. A question always wins over creations."""
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`").strip()
        if "\n" in body and body.split("\n", 1)[0].strip().lower() in ("json", "jsonc"):
            body = body.split("\n", 1)[1]
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model turn did not contain a JSON object")
    try:
        obj = json.loads(body[start:end + 1])
    except ValueError as e:
        raise ValueError(f"model turn was not valid JSON: {e}")
    if not isinstance(obj, dict):
        raise ValueError("model turn JSON must be an object")
    question = obj.get("question")
    if isinstance(question, str) and question.strip():
        return {"question": question.strip(), "slice": None, "tasks": []}
    slice_obj = obj.get("slice") or {}
    if not isinstance(slice_obj, dict):
        raise ValueError("slice must be an object")
    title = slice_obj.get("title", "")
    intent = slice_obj.get("intent", "")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("slice.title is required")
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError("slice.intent is required")
    criteria = slice_obj.get("acceptance_criteria", [])
    if not isinstance(criteria, list) or not all(isinstance(x, str) and x.strip() for x in criteria):
        raise ValueError("slice.acceptance_criteria must be a list of strings")
    raw_tasks = obj.get("tasks", [])
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("a plan needs at least one task")
    tasks = []
    for t in raw_tasks[:MAX_TASKS]:
        if not isinstance(t, dict):
            raise ValueError("tasks must be objects")
        tt, ti = t.get("title", ""), t.get("intent", "")
        if not isinstance(tt, str) or not tt.strip() or not isinstance(ti, str) or not ti.strip():
            raise ValueError("every task needs a title and an intent")
        tasks.append({"title": tt.strip(), "intent": ti.strip()})
    return {"question": None,
            "slice": {"title": title.strip(), "intent": intent.strip(),
                      "acceptance_criteria": [x.strip() for x in criteria]},
            "tasks": tasks}


def render_cards(slice_row: dict, task_rows: list[dict]) -> str:
    """Inline markdown task cards with real ids, appended to the reply."""
    lines = [f"- [ ] slice #{slice_row['id']} {slice_row['title']} (open)"]
    for t in task_rows:
        lines.append(f"- [ ] #{t['id']} {t['title']} ({t['state']})")
    return "\n".join(lines)
