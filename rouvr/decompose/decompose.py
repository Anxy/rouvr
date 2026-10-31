"""Decomposer-lite single-candidate model call (S021)."""
from __future__ import annotations

import json


def decompose_brief(brief: str, *, model=None) -> dict:
    """One model call turns a raw brief into a single v1.3 candidate.

    Fail closed: stub provider, unparsable output, or a candidate whose
    shape (or any task verifier) does not parse is refused, never
    defaulted. Exactly one candidate is returned, never a ranked list.
    """
    from backend.rouvr_models.providers import get_provider
    from rouvr.decompose.validator import parse_verifier
    provider = model or get_provider()
    if provider.name == "stub":
        raise RuntimeError(
            "decompose needs a real model: set ROUVR_MODEL_PROVIDER "
            "(meta|ollama|openrouter) with its key, then retry")
    raw = provider.chat([
        {"role": "system", "content": (
            "Turn the brief into ONE v1.3 decomposition candidate. Reply with ONLY "
            "a JSON object with keys: requirements (list of {id, text, source_ref}), "
            "tasks (list of {id, title, type, allowed_paths, pinned_paths, verifier, "
            "depends_on, covers} where verifier uses only test_passes, "
            "no_changes_outside, path_absent joined with and), coverage_ledger "
            "(requirement id -> [task ids]), dropped_content (list of {cut, why}). "
            "One candidate only.")},
        {"role": "user", "content": brief}])
    try:
        candidate = json.loads(raw)
    except ValueError:
        raise RuntimeError("decomposer returned unparsable output; create refused") from None
    if not isinstance(candidate, dict):
        raise RuntimeError("decomposer returned a non-object; create refused")
    for req in candidate.get("requirements", []):
        if not all(k in req for k in ("id", "text", "source_ref")):
            raise RuntimeError("decomposer requirement missing id/text/source_ref; refused")
    tasks = candidate.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise RuntimeError("decomposer returned no tasks; refused")
    for t in tasks:
        missing = [k for k in ("id", "allowed_paths", "pinned_paths", "verifier",
                               "depends_on", "covers") if k not in t]
        if missing:
            raise RuntimeError(f"decomposer task missing {missing}; refused")
        try:
            parse_verifier(t["verifier"])
        except ValueError as e:
            raise RuntimeError(f"decomposer verifier rejected: {e}") from None
    if not isinstance(candidate.get("coverage_ledger"), dict) or \
            not isinstance(candidate.get("dropped_content"), list):
        raise RuntimeError("decomposer missing coverage_ledger/dropped_content; refused")
    return candidate
