"""Meta Model API worker adapter (stub, first pass).

Uses the pluggable provider (backend.rouvr_models) with ROUVR_MODEL_PROVIDER=meta.
The key is read only from META_MODEL_API_KEY in the environment. Three roles map
to the demo: compile (intent -> contract), narrate (report text), judge (verifier).
"""
from __future__ import annotations

import json

from backend.rouvr_models.providers import ModelProvider, get_provider

PROMPTS = {
    "compile": "Turn the instruction into a JSON execution contract with keys steps, "
               "success_criteria, side_effects, escalation, expires_in_hours. JSON only.",
    "narrate": "Write a short, factual ops report from the JSON figures. No invented numbers.",
    "judge": "Given figures and a report, answer JSON {\"verdict\": \"pass\"|\"fail\", \"reason\": str}.",
}


class MetaWorker:
    def __init__(self, provider: ModelProvider | None = None):
        self.provider = provider or get_provider("meta")

    def run(self, role: str, payload) -> str:
        if role not in PROMPTS:
            raise ValueError(f"unknown role {role!r}")
        content = payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True)
        return self.provider.chat([{"role": "system", "content": PROMPTS[role]},
                                   {"role": "user", "content": content}], temperature=0)
