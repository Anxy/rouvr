from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from backend.services.devexecutor_runlog_resolver_service import get_runlog_root

ARTIFACT_SCHEMA = "aix.model_ingress_presend_artifacts.current"
REDACTION_SCHEMA = "aix.model_ingress_redaction_report.current"
SECRET_KEY_MARKERS = ("authorization", "token", "secret", "api_key", "apikey", "password", "credential")
BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(value or "").strip())
    return cleaned.strip("-") or "unknown"


def _redact(value: Any, *, path: tuple[str, ...] = ()) -> tuple[Any, list[str]]:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        hits: list[str] = []
        for key, item in value.items():
            key_text = str(key)
            key_path = (*path, key_text)
            if any(marker in key_text.lower() for marker in SECRET_KEY_MARKERS):
                redacted[key_text] = "[REDACTED]"
                hits.append(".".join(key_path))
                continue
            redacted_item, item_hits = _redact(item, path=key_path)
            redacted[key_text] = redacted_item
            hits.extend(item_hits)
        return redacted, hits
    if isinstance(value, list):
        redacted_items: list[Any] = []
        hits: list[str] = []
        for index, item in enumerate(value):
            redacted_item, item_hits = _redact(item, path=(*path, str(index)))
            redacted_items.append(redacted_item)
            hits.extend(item_hits)
        return redacted_items, hits
    if isinstance(value, str):
        replaced = BEARER_RE.sub("Bearer [REDACTED]", value)
        if replaced != value:
            return replaced, [".".join(path)]
    return value, []


def _write_json(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True), encoding="utf-8")
    return str(path)


def persist_model_ingress_presend_artifacts(
    *,
    source: str,
    request_id: str,
    request_body: dict[str, Any],
    artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_fingerprint = _fingerprint(request_body)
    digest = request_fingerprint.split(":", 1)[1]
    root = get_runlog_root() / "model-ingress" / _safe_name(source) / digest[:16]
    redacted_request, redacted_paths = _redact(request_body)
    metadata = dict(request_body.get("metadata") or {}) if isinstance(request_body, dict) else {}
    breakdown = dict(metadata.get("prompt_token_breakdown") or {})
    schema_validation = {
        "schema": "aix.model_ingress_schema_validation.current",
        "request_id": request_id,
        "status": "passed",
        "request_fingerprint": request_fingerprint,
        "ingress_schema": metadata.get("ingress_schema"),
        "model_request_schema": metadata.get("schema_name"),
    }
    route_decision = {
        "schema": "aix.model_ingress_route_decision.current",
        "request_id": request_id,
        "source": source,
        "provider": request_body.get("provider"),
        "model": request_body.get("model"),
        "request_fingerprint": request_fingerprint,
    }
    redaction_report = {
        "schema": REDACTION_SCHEMA,
        "request_id": request_id,
        "redaction_applied": bool(redacted_paths),
        "redacted_paths": sorted(set(redacted_paths)),
        "secret_values_removed": bool(redacted_paths),
        "structure_preserved": True,
    }
    refs = {
        "schema": ARTIFACT_SCHEMA,
        "request_id": request_id,
        "source": source,
        "fingerprint": request_fingerprint,
        "request_body_ref": _write_json(root / "request_body.json", redacted_request),
        "prompt_token_breakdown_ref": _write_json(root / "prompt_token_breakdown.json", breakdown),
        "schema_validation_ref": _write_json(root / "schema_validation.json", schema_validation),
        "route_decision_ref": _write_json(root / "route_decision.json", route_decision),
        "redaction_report_ref": _write_json(root / "redaction_report.json", redaction_report),
    }
    if artifact:
        refs["compiled_artifact_ref"] = _write_json(root / "compiled_artifact.json", dict(artifact))
    refs["manifest_ref"] = _write_json(root / "manifest.json", refs)
    return refs


__all__ = ["persist_model_ingress_presend_artifacts"]
