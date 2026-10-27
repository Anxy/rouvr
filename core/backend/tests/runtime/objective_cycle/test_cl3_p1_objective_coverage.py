"""AIX Closed-Loop 003, Packet CL3-P1: `objective_coverage.py` unit tests plus the
gate-level acceptance criteria named in the sprint doc (unrelated EDIT must not fail;
a diff that genuinely does the extract/import-removal work must pass)."""

from __future__ import annotations

from backend.aix_core.runtime.verification.completion_gates import evaluate_completion_gates
from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.objective_cycle.objective_coverage import check_objective_coverage


class TestCheckObjectiveCoverageUnit:
    def test_no_signals_triggered_is_satisfied(self) -> None:
        result = check_objective_coverage(
            objective="Append DB_TIMEOUT=30 to the .env file.",
            diff_text="--- a/.env\n+++ b/.env\n@@ -1,1 +1,2 @@\n DB_HOST=localhost\n+DB_TIMEOUT=30\n",
        )
        assert result.satisfied is True
        assert result.triggered_signals == ()

    def test_unused_import_removal_triggered_and_missing(self) -> None:
        result = check_objective_coverage(
            objective="Remove unused use-imports from Foo.php.",
            diff_text="--- a/Foo.php\n+++ b/Foo.php\n@@ -1,2 +1,2 @@\n-echo 'old';\n+echo 'new';\n",
        )
        assert result.satisfied is False
        assert "remove_unused_imports" in result.missing_signals

    def test_unused_import_removal_satisfied_by_removed_use_line(self) -> None:
        result = check_objective_coverage(
            objective="Remove unused use-imports from Foo.php.",
            diff_text="--- a/Foo.php\n+++ b/Foo.php\n@@ -1,3 +1,2 @@\n use App\\Models\\Unused;\n-use App\\Models\\NeverReferenced;\n use App\\Models\\Used;\n",
        )
        assert result.satisfied is True
        assert "remove_unused_imports" not in result.missing_signals

    def test_extract_single_call_site_wrapper_does_not_satisfy(self) -> None:
        """Mirrors the incident's own `centralDatabaseName()` -- a new function with
        exactly one call site is a rename with extra steps, not an extraction of
        repeated boilerplate."""
        diff = (
            "--- a/Foo.php\n+++ b/Foo.php\n@@ -1,3 +1,8 @@\n"
            "+\tprivate function centralDatabaseName()\n"
            "+\t{\n"
            "+\t\treturn env('DB_DATABASE', '360degree');\n"
            "+\t}\n"
            "+\t$db = $this->centralDatabaseName();\n"
        )
        result = check_objective_coverage(
            objective="Extract repeated DB boilerplate into a helper method.",
            diff_text=diff,
        )
        assert result.satisfied is False
        assert "extract_helpers" in result.missing_signals

    def test_extract_helper_reused_at_two_call_sites_satisfies(self) -> None:
        diff = (
            "--- a/Foo.php\n+++ b/Foo.php\n@@ -1,3 +1,10 @@\n"
            "+\tprivate function tenantTableName($db, $table)\n"
            "+\t{\n"
            "+\t\treturn $db . '.' . $table;\n"
            "+\t}\n"
            "+\t$a = $this->tenantTableName($db, 'file_downloads');\n"
            "+\t$b = $this->tenantTableName($db, 'invoices');\n"
        )
        result = check_objective_coverage(
            objective="Extract repeated DB boilerplate into a helper method.",
            diff_text=diff,
        )
        assert result.satisfied is True

    def test_section_comments_already_in_pre_image_are_not_a_failure(self) -> None:
        result = check_objective_coverage(
            objective="Group public cron actions with clear section comments.",
            diff_text="--- a/Foo.php\n+++ b/Foo.php\n@@ -1,2 +1,2 @@\n-old();\n+new();\n",
            pre_image_snippet="// --- Fee cron actions ---\npublic function feeDue() {}\n",
        )
        assert result.satisfied is True

    def test_section_comments_missing_from_both_pre_image_and_diff_fails(self) -> None:
        result = check_objective_coverage(
            objective="Group public cron actions with clear section comments.",
            diff_text="--- a/Foo.php\n+++ b/Foo.php\n@@ -1,2 +1,2 @@\n-old();\n+new();\n",
            pre_image_snippet="public function feeDue() {}\n",
        )
        assert result.satisfied is False
        assert "section_comments" in result.missing_signals


class TestGateLevelAcceptance:
    def test_unrelated_bound_edit_still_completes_without_coverage_failure(self) -> None:
        result = evaluate_completion_gates(
            discovery=DiscoveryReport(config_files={".env": "DB_HOST=localhost\n"}),
            diff_stats={".env": {"additions": 1, "deletions": 0}},
            diff_text="--- a/.env\n+++ b/.env\n@@ -1,1 +1,2 @@\n DB_HOST=localhost\n+DB_TIMEOUT=30\n",
            test_result={"returncode": 0},
            task_family="EDIT",
            objective="Append DB_TIMEOUT=30 to the .env file.",
        )
        assert result.gates.get("objective_coverage") is True
        assert result.passed is True

    def test_diff_that_actually_extracts_and_removes_imports_passes_coverage(self) -> None:
        diff = (
            "--- a/Foo.php\n+++ b/Foo.php\n@@ -1,5 +1,10 @@\n"
            "-use App\\Models\\NeverReferenced;\n"
            " use App\\Models\\Used;\n"
            "+\tprivate function tenantTableName($db, $table)\n"
            "+\t{\n"
            "+\t\treturn $db . '.' . $table;\n"
            "+\t}\n"
            "+\t$a = $this->tenantTableName($db, 'file_downloads');\n"
            "+\t$b = $this->tenantTableName($db, 'invoices');\n"
        )
        result = evaluate_completion_gates(
            discovery=DiscoveryReport(config_files={"Foo.php": "<?php\nclass Foo {}\n"}),
            diff_stats={"Foo.php": {"additions": 6, "deletions": 1}},
            diff_text=diff,
            test_result={"returncode": 0},
            task_family="REFACTOR",
            objective="Refactor Foo.php: extract repeated DB boilerplate into a helper method and remove unused use-imports.",
        )
        assert result.gates.get("objective_coverage") is True
        assert result.passed is True


class TestFocusedTestsCoverage:
    def test_focused_tests_ask_fails_without_test_file_in_diff(self) -> None:
        result = check_objective_coverage(
            objective="Add focused tests proving retired mutation routes stay fail-closed.",
            diff_text="--- a/backend/main.py\n+++ b/backend/main.py\n@@ -1,1 +1,2 @@\n app = FastAPI()\n+MATRIX = {}\n",
        )
        assert result.satisfied is False
        assert "focused_tests" in result.missing_signals

    def test_focused_tests_ask_passes_when_test_file_added(self) -> None:
        result = check_objective_coverage(
            objective="Add focused tests proving retired mutation routes stay fail-closed.",
            diff_text=(
                "--- /dev/null\n+++ b/backend/tests/test_public_router_authority_matrix.py\n"
                "@@ -0,0 +1,3 @@\n+def test_one_canonical_writer():\n+    assert True\n"
            ),
        )
        assert result.satisfied is True
        assert "focused_tests" not in result.missing_signals


    def test_objective_requires_focused_tests_helper(self) -> None:
        from backend.aix_core.objective_cycle.objective_coverage import (
            objective_requires_focused_tests,
        )

        assert objective_requires_focused_tests(
            "Add focused tests proving retired mutation routes stay fail-closed."
        )
        assert not objective_requires_focused_tests("Update PUBLIC_ROUTER_AUTHORITY_MATRIX classifications.")
