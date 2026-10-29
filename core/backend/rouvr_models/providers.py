"""Pluggable model providers for Rouvr (all OpenAI-compatible chat endpoints).

Select with ROUVR_MODEL_PROVIDER = meta | ollama | openrouter | stub.
Keys come only from the environment; nothing is hard-coded.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass

_PRESETS = {
    # Meta Model API — OpenAI-compatible surface; endpoint+model from env only.
    "meta": ("META_MODEL_API_BASE_URL", "", "META_MODEL_API_KEY", "META_MODEL_NAME", ""),
    # Open weights served locally (Ollama / vLLM / llama.cpp server).
    "ollama": ("OLLAMA_BASE_URL", "http://localhost:11434/v1", None, "OLLAMA_MODEL", "llama3.1"),
    "openrouter": ("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "OPENROUTER_DEFAULT_MODEL", "meta-llama/llama-3.1-70b-instruct"),
}


@dataclass
class ModelProvider:
    name: str
    base_url: str
    model: str
    api_key: str | None = None

    def chat(self, messages: list[dict], *, timeout: float = 60.0, **params) -> str:
        if self.name == "stub":
            return json.dumps({"stub": True, "echo": messages[-1].get("content", "")})
        body = json.dumps({"model": self.model, "messages": messages, **params}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.base_url.rstrip("/") + "/chat/completions", data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        return data["choices"][0]["message"]["content"]


def get_provider(name: str | None = None, env: dict | None = None) -> ModelProvider:
    env = os.environ if env is None else env
    name = (name or env.get("ROUVR_MODEL_PROVIDER") or "stub").strip().lower()
    if name == "stub":
        return ModelProvider("stub", "stub://", "stub")
    if name not in _PRESETS:
        raise ValueError(f"unknown ROUVR_MODEL_PROVIDER {name!r}; choose meta|ollama|openrouter|stub")
    url_var, url_default, key_var, model_var, model_default = _PRESETS[name]
    key = env.get(key_var) if key_var else None
    if key_var and not key:
        raise RuntimeError(f"{key_var} is not set (required for provider {name!r})")
    base_url, model = env.get(url_var) or url_default, env.get(model_var) or model_default
    if not base_url:
        raise RuntimeError(f"{url_var} is not set (required for provider {name!r})")
    return ModelProvider(name, base_url, model, key)
