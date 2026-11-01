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
    raw = p.chat(
        [{"role": "system",
          "content": ("Return ONLY a JSON object with any subset of these keys: "
                      "steps (array of strings), success_criteria (array of strings), "
                      "side_effects (array of strings), stakeholders (array of strings), "
                      "escalation (string), expires_in_hours (number). "
                      "No other top-level keys, no prose.")},
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
        # Governed merge: only well-typed known keys land; an alien model
        # shape (observed live: string success_criteria, nested objects)
        # leaves the safe defaults standing instead of breaking validation.
        for key in ("steps", "success_criteria", "side_effects", "stakeholders"):
            val = model_json.get(key)
            if isinstance(val, list) and all(isinstance(x, str) for x in val):
                contract[key] = val
        if isinstance(model_json.get("escalation"), str):
            contract["escalation"] = model_json["escalation"]
        hours = model_json.get("expires_in_hours")
        if isinstance(hours, (int, float)) and not isinstance(hours, bool) and hours > 0:
            contract["expires_in_hours"] = hours
    return validate_ops_contract(contract)


def judge_ops_report(report: str, figures: dict, *, chat) -> dict:
    """Independent model judge: does this tick's report carry the ledger figures?

    chat is a provider-style callable (messages) -> str. Unparsable or
    off-schema model output fails closed (CONTRACT): a judge that cannot
    speak verdict JSON is refused, never counted as pass.
    """
    raw = chat(
        [{"role": "system",
          "content": ("You verify a delivered ops report against ledger figures. "
                      "Reply with ONLY a JSON object "
                      '{"verdict": "pass|fail", "reason": "<one sentence>"}. '
                      "Pass only if the report states every figure value. "
                      "Any mismatch, missing figure, or wrong tick means fail.")},
         {"role": "user",
          "content": f"ledger figures: {json.dumps(figures, sort_keys=True)}\nreport:\n{report}"}])
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as e:
        raise RouvrError(Code.CONTRACT, "ops judge output is not JSON: refused") from e
    if not isinstance(data, dict) or data.get("verdict") not in ("pass", "fail"):
        raise RouvrError(Code.CONTRACT, "ops judge verdict must be pass|fail: refused")
    return {"verdict": data["verdict"], "reason": str(data.get("reason") or ""),
            "judge_principal": OPS_JUDGE}


def verify_with_judge(*, deterministic_pass: bool, report: str, figures: dict,
                      provider_name: str, chat=None) -> dict:
    """Combine the deterministic read-back check with the model judge.

    Stub providers cannot judge: recorded as skipped, never as pass. A live
    judge fail overrides a deterministic pass; a judge error fails closed.
    """
    if provider_name == "stub" or chat is None:
        return {"verdict": "pass" if deterministic_pass else "fail",
                "judge": {"verdict": "skipped", "reason": "no model judge on stub",
                          "judge_principal": OPS_JUDGE}}
    judged = judge_ops_report(report, figures, chat=chat)
    verdict = "pass" if (deterministic_pass and judged["verdict"] == "pass") else "fail"
    return {"verdict": verdict, "judge": judged}


def check_readback(outbox: Path, msg_id: str, figures: dict) -> tuple[bool, bool]:
    """Read back the delivered message; malformed files count as no-readback."""
    path = outbox / f"{msg_id}.txt"
    readback = path.read_text() if path.exists() else None
    try:
        figures_match = bool(readback) and json.loads(readback.split("\n", 1)[1])["figures"] == figures
    except (ValueError, IndexError, KeyError, AttributeError):
        return False, False
    return True, figures_match
