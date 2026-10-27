from __future__ import annotations

import json
from pathlib import Path

from backend.services.aix_model_ingress_artifact_service import persist_model_ingress_presend_artifacts


def test_presend_artifacts_write_refs_and_redact_secrets(tmp_path: Path, monkeypatch) -> None:
    from backend.services import devexecutor_runlog_resolver_service as runlog_module

    monkeypatch.setattr(runlog_module.settings, "AIX_RUNLOG_ROOT", str(tmp_path))
    refs = persist_model_ingress_presend_artifacts(
        source="planner",
        request_id="req-1",
        request_body={
            "provider": "openrouter",
            "model": "m",
            "messages": [{"role": "user", "content": "Bearer secret-token-value"}],
            "metadata": {
                "schema_name": "aix.model_request.current",
                "ingress_schema": "aix.planner_ingress.current",
                "authorization": "Bearer abc123",
                "prompt_token_breakdown": {"estimated_prompt_tokens": 12},
            },
        },
        artifact={"schema_name": "aix.model_request.current", "request_id": "req-1"},
    )

    assert refs["schema"] == "aix.model_ingress_presend_artifacts.current"
    request_body = json.loads(Path(refs["request_body_ref"]).read_text(encoding="utf-8"))
    redaction_report = json.loads(Path(refs["redaction_report_ref"]).read_text(encoding="utf-8"))
    assert request_body["metadata"]["authorization"] == "[REDACTED]"
    assert request_body["messages"][0]["content"] == "Bearer [REDACTED]"
    assert redaction_report["redaction_applied"] is True
    assert redaction_report["structure_preserved"] is True
    assert Path(refs["prompt_token_breakdown_ref"]).exists()
    assert Path(refs["schema_validation_ref"]).exists()
    assert Path(refs["route_decision_ref"]).exists()
    assert Path(refs["compiled_artifact_ref"]).exists()
