"""rouvr/ops.py — governed ops-task pieces shared by demo/morning_report.py and OpsTaskWorkflow.

The ops contract is JSON-schema-validated (jsonschema is already a dependency):
invalid model output is refused, never merged. The judge is a separate principal
(OPS_JUDGE) from the executor (OPS_EXECUTOR) and records model + verdict evidence.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from rouvr.errors import Code, RouvrError

OPS_EXECUTOR = "ops-worker"  # runs the steps, holds the lease/claim
OPS_JUDGE = "ops-judge"  # verifies delivery, never executes

OPS_CONTRACT_SCHEMA = {
    "type": "object",
    "required": ["steps", "success_criteria", "side_effects", "stakeholders",
                 "escalation", "expires_in_hours"],
    "properties": {
        "steps": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "success_criteria": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "side_effects": {"type": "array", "items": {"type": "string"}},
        "stakeholders": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "escalation": {"type": "string", "minLength": 1},
        "expires_in_hours": {"type": "number", "exclusiveMinimum": 0},
    },
    "additionalProperties": True,
}

OPS_DIGEST_DONE = "Done. No action needed."


def validate_ops_contract(contract: dict) -> dict:
    """Schema-check an ops contract; invalid is refused, never merged."""
    try:
        jsonschema.validate(contract, OPS_CONTRACT_SCHEMA)
    except jsonschema.ValidationError as e:
        raise RouvrError(Code.CONTRACT, f"ops contract refused: {e.message}") from e
    return contract


def compile_ops_contract(instruction: str) -> dict:
    """Intent -> validated execution contract via the configured model provider."""
    import os

    from backend.rouvr_models.providers import get_provider
    p = get_provider()
    if os.environ.get("ROUVR_INJECT") == "revoked_key":
        raise RouvrError(Code.AUTHZ, "model credential revoked (injected)")
    raw = p.chat([{"role": "system", "content": "Return a JSON execution contract."},
                  {"role": "user", "content": instruction}])
    contract = {
        "steps": ["health_probe", "compile_report", "deliver", "verify"],
        "success_criteria": ["report delivered and read back", "figures match ledger", "health ok"],
        "side_effects": ["write one message to stakeholder outbox"],
        "stakeholders": ["owner"],
        "escalation": "owner",
        "expires_in_hours": 24,
        "provider": p.name,
    }
    if p.name != "stub":
        try:
            model_json = json.loads(raw)
        except (ValueError, TypeError):
            raise RouvrError(Code.CONTRACT, "model contract is not JSON: refused, not merged")
        if not isinstance(model_json, dict):
            raise RouvrError(Code.CONTRACT, "model contract is not an object: refused, not merged")
        contract.update(model_json)
    return validate_ops_contract(contract)


def check_readback(outbox: Path, msg_id: str, figures: dict) -> tuple[bool, bool]:
    """Read back the delivered message; malformed files count as no-readback."""
    path = outbox / f"{msg_id}.txt"
    readback = path.read_text() if path.exists() else None
    try:
        figures_match = bool(readback) and json.loads(readback.split("\n", 1)[1])["figures"] == figures
    except (ValueError, IndexError, KeyError, AttributeError):
        return False, False
    return True, figures_match
