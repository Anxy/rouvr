"""Unit tests for the read-file executor's pure helper functions: intent
classification, Windows/POSIX path extraction, and `.env` key summarization that
never retains a sensitive raw value."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aix_core.runtime.executor.read_file_execute import (
    ReadFileError,
    execute_read_file,
    extract_target_path,
    is_env_like_path,
    is_sensitive_key,
    looks_like_read_file_objective,
    resolve_relative_path,
    summarize_env_content,
)
from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolbox


def _init_git_repo(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)


class TestReadIntentClassification:
    def test_sprint_acceptance_scenario_objective_is_classified_as_read(self) -> None:
        objective = r"D:\v2.1\hyeve\services\hyeve-integration-service\.env read this file"
        assert looks_like_read_file_objective(objective) is True

    def test_edit_verb_disqualifies_read_classification_even_with_a_read_phrase(self) -> None:
        objective = "read this file and then update the SMTP_PASSWORD value"
        assert looks_like_read_file_objective(objective) is False

    def test_prohibited_modify_does_not_disqualify_read_classification(self) -> None:
        objective = (
            "Read gaef001-scratch/README_A.txt and explain what it says. "
            "Do not modify the README or any other repository file."
        )
        target_path = "gaef001-scratch/README_A.txt"
        assert looks_like_read_file_objective(objective, target_path) is True

    def test_real_world_casual_phrasing_with_a_file_path_is_classified_as_read(self) -> None:
        """The exact class of phrasing a real user typed ("hey can you check the
        ...service file") that the original fixed phrase list did not cover — a file
        path plus no edit verb must be enough, not just the sprint's literal wording."""
        objective = r"hey can you check the D:\v2.1\hyeve\services\hyeve-integration-service\.env file"
        target_path = extract_target_path(objective)
        assert looks_like_read_file_objective(objective, target_path) is True

    def test_bare_directory_path_with_no_explicit_read_phrase_is_not_a_read(self) -> None:
        """A directory has no filename component — this is `INSPECT_DIRECTORY`, a
        different, not-yet-built intent. Must not silently misclassify it as a file
        read (which would then fail closed with PATH_INVALID rather than doing
        something useful) unless the user explicitly asked to read/check it."""
        objective = r"D:\v2.1\hyeve\services\hyeve-integration-service"
        target_path = extract_target_path(objective)
        assert looks_like_read_file_objective(objective, target_path) is False

    def test_explicit_read_phrase_still_applies_even_to_a_directory_looking_path(self) -> None:
        objective = r"please read D:\v2.1\hyeve\services\hyeve-integration-service"
        target_path = extract_target_path(objective)
        assert looks_like_read_file_objective(objective, target_path) is True

    def test_objective_with_no_read_phrase_is_not_a_read(self) -> None:
        assert looks_like_read_file_objective("build a login page") is False

    def test_refactor_verb_with_file_path_is_not_a_read(self) -> None:
        objective = r'hey can you refactor this file "D:\v2.1\Visions\web.php"'
        target_path = extract_target_path(objective)
        assert looks_like_read_file_objective(objective, target_path) is False


class TestPathExtraction:
    def test_extracts_backslash_windows_path(self) -> None:
        objective = r"D:\v2.1\hyeve\services\hyeve-integration-service\.env read this file"
        assert extract_target_path(objective) == r"D:\v2.1\hyeve\services\hyeve-integration-service\.env"

    def test_extracts_forward_slash_windows_path(self) -> None:
        objective = "D:/v2.1/hyeve/services/hyeve-integration-service/.env read this file"
        assert extract_target_path(objective) == "D:/v2.1/hyeve/services/hyeve-integration-service/.env"

    def test_extracts_quoted_path_with_spaces(self) -> None:
        objective = '"D:\\folder with spaces\\file.txt" read this file'
        assert extract_target_path(objective) == "D:\\folder with spaces\\file.txt"

    def test_quoted_comment_is_not_treated_as_a_path(self) -> None:
        """Regression: SMTP/.env append objectives include a quoted comment like
        \"# SMTP (...)\". That must not win over the real Windows path."""
        objective = (
            r'In D:\v2.1\hyeve\services\hyeve-integration-service\.env append '
            r'SMTP_HOST=smtp.gmail.com under a comment "# SMTP (email delivery via SmtpMessageAdapter)"'
        )
        assert extract_target_path(objective) == r"D:\v2.1\hyeve\services\hyeve-integration-service\.env"

    def test_drive_letter_is_never_treated_as_a_url_scheme(self) -> None:
        objective = r"D:\config\.env read this file"
        target = extract_target_path(objective)
        assert target is not None
        assert target.startswith("D:\\")

    def test_no_path_present_returns_none(self) -> None:
        assert extract_target_path("read this file please") is None


class TestResolveRelativePath:
    def test_absolute_path_inside_root_resolves(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
        target = str(tmp_path / ".env")
        assert resolve_relative_path(target, tmp_path) == ".env"

    def test_path_outside_root_is_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside.env"
        assert resolve_relative_path(str(outside), tmp_path) is None

    def test_bare_relative_filename_resolves_against_root(self, tmp_path: Path) -> None:
        assert resolve_relative_path(".env", tmp_path) == ".env"


class TestEnvSummary:
    def test_sprint_example_env_file_finds_six_keys_one_sensitive(self) -> None:
        content = (
            "SMTP_HOST=smtp.example.com\n"
            "SMTP_PORT=587\n"
            "SMTP_USER=notifications@example.com\n"
            "SMTP_PASSWORD=hunter2\n"
            "SMTP_TLS=true\n"
            "SMTP_FROM=noreply@example.com\n"
        )
        summary = summarize_env_content(content)
        assert len(summary.keys) == 6
        sensitive = [k for k in summary.keys if k.sensitive]
        assert len(sensitive) == 1
        assert sensitive[0].name == "SMTP_PASSWORD"
        as_dict = summary.as_dict()
        # The raw secret value must never appear anywhere in the structured summary.
        assert "hunter2" not in str(as_dict)

    def test_non_sensitive_values_are_never_included_only_inferred_type(self) -> None:
        summary = summarize_env_content("SMTP_PORT=587\n")
        assert summary.keys[0].value_type == "numeric"
        assert "587" not in str(summary.as_dict())

    def test_malformed_line_is_recorded_not_dropped_silently(self) -> None:
        summary = summarize_env_content("SMTP_HOST=smtp.example.com\nnot a valid line\n")
        assert summary.malformed_lines == [2]

    def test_duplicate_key_is_recorded(self) -> None:
        summary = summarize_env_content("A=1\nA=2\n")
        assert summary.duplicate_keys == ["A"]

    def test_comments_and_blank_lines_are_ignored(self) -> None:
        summary = summarize_env_content("# comment\n\nA=1\n")
        assert len(summary.keys) == 1

    @pytest.mark.parametrize(
        "name",
        ["SMTP_PASSWORD", "DATABASE_URL", "ACCESS_KEY", "CLIENT_SECRET", "API_KEY", "AUTH_TOKEN", "DB_CREDENTIAL"],
    )
    def test_known_sensitive_key_variants_are_classified_sensitive(self, name: str) -> None:
        assert is_sensitive_key(name) is True

    def test_ordinary_key_is_not_sensitive(self) -> None:
        assert is_sensitive_key("SMTP_PORT") is False


class TestIsEnvLikePath:
    def test_dotenv_matches(self) -> None:
        assert is_env_like_path(".env") is True

    def test_dotenv_variant_matches(self) -> None:
        assert is_env_like_path(".env.local") is True

    def test_regular_file_does_not_match(self) -> None:
        assert is_env_like_path("config.py") is False


class TestExecuteReadFile:
    def test_reads_a_real_env_file_and_never_returns_the_raw_secret(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("SMTP_HOST=smtp.example.com\nSMTP_PASSWORD=hunter2\n", encoding="utf-8")
        _init_git_repo(tmp_path)
        toolbox = RepositoryToolbox(repository_root=tmp_path)

        result = execute_read_file(toolbox=toolbox, target_path=".env", repository_root=tmp_path)

        assert result.relative_path == ".env"
        assert result.bytes_read > 0
        assert result.env_summary is not None
        assert result.preview is None
        assert result.verification() == {
            "resource_resolved": True,
            "file_read_non_empty": True,
            "target_resource_accessed": True,
            "secret_values_redacted": True,
        }

    def test_missing_file_raises_path_not_found(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        toolbox = RepositoryToolbox(repository_root=tmp_path)
        with pytest.raises(ReadFileError) as exc_info:
            execute_read_file(toolbox=toolbox, target_path="missing.env", repository_root=tmp_path)
        assert exc_info.value.code == "PATH_NOT_FOUND"

    def test_path_outside_repository_root_raises_path_not_allowed(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        toolbox = RepositoryToolbox(repository_root=tmp_path)
        outside = str(tmp_path.parent / "outside.env")
        with pytest.raises(ReadFileError) as exc_info:
            execute_read_file(toolbox=toolbox, target_path=outside, repository_root=tmp_path)
        assert exc_info.value.code == "PATH_NOT_ALLOWED"

    def test_empty_file_raises_result_empty(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("", encoding="utf-8")
        _init_git_repo(tmp_path)
        toolbox = RepositoryToolbox(repository_root=tmp_path)
        with pytest.raises(ReadFileError) as exc_info:
            execute_read_file(toolbox=toolbox, target_path=".env", repository_root=tmp_path)
        assert exc_info.value.code == "RESULT_EMPTY"
