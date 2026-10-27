"""AIX-PA-002 bounded planner-context packing."""

from __future__ import annotations

from backend.aix_core.runtime.planner.planner_context_budget import (
    budget_planner_prompt,
    estimate_tokens,
)
from backend.infrastructure.model_gateway.intelligence_layer.prompts import STEP_CONTRACT_PLANNER_SYSTEM_PROMPT


def test_budget_keeps_objective_and_obligations_and_drops_optional() -> None:
    evidence = {
        "objective": "complex change",
        "obligations": [{"id": "o1", "description": "config"}, {"id": "o2", "description": "service"}],
        "definition_of_done": ["config", "service"],
        "symbols_found": [f"sym{i}" for i in range(80)],
        "files_read": ["a.py", "b.py", "c.py"],
        "file_excerpts": {
            "a.py": "x" * 8000,
            "b.py": "y" * 8000,
            "c.py": "z" * 8000,
            "tests/test_a.py": "t" * 4000,
        },
        "constraints": ["allowed_files:a.py"],
        "observed_problem": "p" * 4000,
        "prior_failure_evidence": "fail" * 500,
        "executor_profile": {"role": "executor", "blob": "q" * 4000},
        "method_bodies_are_executor_owned": True,
    }
    report = budget_planner_prompt(
        system_prompt=STEP_CONTRACT_PLANNER_SYSTEM_PROMPT,
        evidence=evidence,
        cap=2500,
    )
    assert report.estimated_input_tokens <= report.configured_input_cap
    assert "objective" in report.evidence_included
    assert "obligations" in report.evidence_included
    assert report.evidence["objective"] == "complex change"
    assert report.evidence["obligations"]
    assert report.evidence_dropped or report.evidence_compacted


def test_budget_report_exposes_token_fields() -> None:
    report = budget_planner_prompt(
        system_prompt="short system",
        evidence={"objective": "x", "obligations": [], "file_excerpts": {}},
        cap=2000,
    )
    payload = report.to_dict()
    assert payload["estimated_input_tokens"] == estimate_tokens(
        "short system\n\nEvidence:\n" + __import__("json").dumps(report.evidence, sort_keys=True)
    )
    assert payload["configured_input_cap"] == 2000
    assert "evidence_included" in payload
    assert "evidence_compacted" in payload
    assert "evidence_dropped" in payload
