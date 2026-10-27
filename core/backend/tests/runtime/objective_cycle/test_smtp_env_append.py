"""Unit tests for SMTP .env append local fallback."""

from __future__ import annotations

from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates
from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.executor.read_file_execute import normalize_repo_relative_path
from backend.aix_core.runtime.executor.source_patch_codegen import (
    extract_env_assignments,
    local_fallback_bound_file_assignments,
)


def test_extract_smtp_assignments_skips_redacted_values() -> None:
    text = "SMTP_HOST=smtp.gmail.com SMTP_PASSWORD=***REDACTED*** SMTP_PORT=587"
    assert extract_env_assignments(text) == {
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "587",
    }


def test_local_fallback_appends_smtp_block_to_env() -> None:
    report = DiscoveryReport(
        keywords=("smtp",),
        config_files={
            ".env": "ENVIRONMENT=local\nDATABASE_URL=postgres://x\n",
        },
    )
    objective = (
        'append SMTP_HOST=smtp.gmail.com SMTP_PORT=587 SMTP_USER=dev@example.com '
        'SMTP_PASSWORD=secret SMTP_TLS=true SMTP_FROM=dev@example.com '
        'under a comment "# SMTP (email delivery via SmtpMessageAdapter)"'
    )
    result = local_fallback_bound_file_assignments(
        report,
        objective,
        assignments=extract_env_assignments(objective),
        preferred_path=".env",
    )
    assert result.no_files_identified is False
    assert len(result.edits) == 1
    edit = result.edits[0]
    assert edit.path == ".env"
    assert "ENVIRONMENT=local" in edit.new_content
    assert "DATABASE_URL=postgres://x" in edit.new_content
    assert "# SMTP (email delivery via SmtpMessageAdapter)" in edit.new_content
    assert "SMTP_HOST=smtp.gmail.com" in edit.new_content
    assert "SMTP_PASSWORD=secret" in edit.new_content
    assert "SMTP_TLS=true" in edit.new_content


def test_normalize_repo_relative_path_keeps_dotenv() -> None:
    assert normalize_repo_relative_path(".env") == ".env"
    assert normalize_repo_relative_path("./.env") == ".env"
    assert normalize_repo_relative_path(".env").lstrip("./") == "env"  # documents the bug we avoid
    assert normalize_repo_relative_path("backend/config.py") == "backend/config.py"


def test_local_fallback_already_satisfied_when_smtp_keys_present() -> None:
    report = DiscoveryReport(
        keywords=("smtp",),
        config_files={
            ".env": (
                "ENVIRONMENT=local\n"
                "# SMTP (email delivery via SmtpMessageAdapter)\n"
                "SMTP_HOST=smtp.gmail.com\n"
                "SMTP_PORT=587\n"
                "SMTP_USER=dev@example.com\n"
                "SMTP_PASSWORD=fake-app-password\n"
                "SMTP_TLS=true\n"
                "SMTP_FROM=dev@example.com\n"
            ),
        },
    )
    objective = (
        "append SMTP_HOST=smtp.gmail.com SMTP_PORT=587 SMTP_USER=dev@example.com "
        "SMTP_PASSWORD=fake-app-password SMTP_TLS=true SMTP_FROM=dev@example.com"
    )
    result = local_fallback_bound_file_assignments(
        report,
        objective,
        assignments=extract_env_assignments(objective),
        preferred_path=".env",
    )
    assert result.already_satisfied is True
    assert result.edits == []


def test_dotenv_only_completion_gates_pass_without_tests() -> None:
    discovery = DiscoveryReport(
        keywords=("smtp",),
        config_files={".env": "ENVIRONMENT=local\nSMTP_HOST=x\n"},
    )
    result = evaluate_completion_gates(
        discovery=discovery,
        diff_stats={".env": {"additions": 7, "deletions": 0}},
        diff_text="diff --git a/.env b/.env\n+SMTP_HOST=x\n",
        test_result=None,
    )
    assert result.passed is False
    assert result.gates["verification_ran"] is False
    assert result.gates["verification_passed"] is False
