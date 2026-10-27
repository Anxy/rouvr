"""Generic bound-file assignment / completion-gate tests (no SMTP hardcoding)."""

from __future__ import annotations

from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates
from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.executor.read_file_execute import normalize_repo_relative_path
from backend.aix_core.runtime.executor.source_patch_codegen import (
    extract_env_assignments,
    extract_text_replacements,
    local_fallback_bound_file_assignments,
    local_fallback_text_replacements,
)


def test_extract_env_assignments_skips_redacted_values() -> None:
    text = "SETTING_ALPHA_731=ok API_PASSWORD=***REDACTED*** OTHER_PORT=587"
    assert extract_env_assignments(text) == {
        "SETTING_ALPHA_731": "ok",
        "OTHER_PORT": "587",
    }


def test_normalize_repo_relative_path_keeps_dotenv() -> None:
    assert normalize_repo_relative_path(".env") == ".env"
    assert normalize_repo_relative_path("./.env") == ".env"
    assert ".env".lstrip("./") == "env"


def test_bound_file_assignment_append() -> None:
    report = DiscoveryReport(
        keywords=("setting",),
        config_files={".env": "ENVIRONMENT=local\nDATABASE_URL=postgres://x\n"},
    )
    objective = (
        'append SETTING_ALPHA_731=5 OTHER_FLAG=true '
        'under a comment "# generic settings block"'
    )
    result = local_fallback_bound_file_assignments(
        report,
        objective,
        assignments=extract_env_assignments(objective),
        preferred_path=".env",
    )
    assert result is not None
    assert len(result.edits) == 1
    content = result.edits[0].new_content
    assert "ENVIRONMENT=local" in content
    assert "SETTING_ALPHA_731=5" in content
    assert "# generic settings block" in content


def test_text_replacement_across_discovered_files() -> None:
    report = DiscoveryReport(
        config_files={
            "a.py": "def calculate_delta_492():\n    return 1\n",
            "b.py": "from a import calculate_delta_492\nprint(calculate_delta_492())\n",
        }
    )
    objective = "rename calculate_delta_492 to calculate_delta_999"
    result = local_fallback_text_replacements(report, objective)
    assert result is not None
    assert len(result.edits) == 2
    joined = "\n".join(e.new_content for e in result.edits)
    assert "calculate_delta_999" in joined
    assert "calculate_delta_492" not in joined


def test_diff_only_completion_gates_with_state_assertions() -> None:
    discovery = DiscoveryReport(
        keywords=("setting",),
        config_files={".env": "ENVIRONMENT=local\nSETTING_ALPHA_731=5\n"},
    )
    result = evaluate_completion_gates(
        discovery=discovery,
        diff_stats={".env": {"additions": 2, "deletions": 0}},
        diff_text="diff --git a/.env b/.env\n+SETTING_ALPHA_731=5\n",
        test_result=None,
        diff_only_verification_allowed=True,
        state_assertions_passed=True,
    )
    assert result.passed is True


def test_extract_text_replacements_patterns() -> None:
    pairs = extract_text_replacements("replace 'CustomerPayload731' with 'CustomerResponse731'")
    assert pairs == [("CustomerPayload731", "CustomerResponse731")]
