"""AIX Closed-Loop 003, Packet CL3-P2: the offline PHP fallback must remove unused
use-imports when asked, and must report `remaining_asked_work` instead of silently
returning `None` once the objective still names work it could not complete.

Uses a small, generic PHP fixture (not the full CronJobController.php) per the
sprint doc's own instruction for this packet's tests.
"""

from __future__ import annotations

from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.executor.source_patch_codegen import (
    _remove_unused_php_use_imports,
    local_fallback_php_controller_refactor,
)

_OBJECTIVE_UNUSED_IMPORTS_ONLY = "Remove unused use-imports from Small.php."

_OBJECTIVE_FULL = (
    "Refactor Small.php: insert section comments before public cron action methods "
    "grouped as Fee, Reporting; replace '360_'.$domain with $this->tenantDatabaseName(...); "
    "remove unused use-imports."
)


class TestRemoveUnusedPhpUseImports:
    def test_unused_import_removed(self) -> None:
        content = (
            "<?php\n"
            "use App\\Models\\Used;\n"
            "use App\\Models\\NeverReferenced;\n"
            "class Small {\n"
            "\tpublic function feeDue() { return new Used(); }\n"
            "}\n"
        )
        updated, n = _remove_unused_php_use_imports(content)
        assert n == 1
        assert "use App\\Models\\NeverReferenced;" not in updated
        assert "use App\\Models\\Used;" in updated

    def test_used_import_kept(self) -> None:
        content = (
            "<?php\n"
            "use App\\Models\\Used;\n"
            "class Small {\n"
            "\tpublic function feeDue() { return new Used(); }\n"
            "}\n"
        )
        updated, n = _remove_unused_php_use_imports(content)
        assert n == 0
        assert updated == content

    def test_alias_import_checked_by_alias_not_fqcn(self) -> None:
        content = (
            "<?php\n"
            "use App\\Models\\SomeReallyLongClassName as Short;\n"
            "class Small {\n"
            "\tpublic function feeDue() { return new Short(); }\n"
            "}\n"
        )
        updated, n = _remove_unused_php_use_imports(content)
        assert n == 0
        assert "use App\\Models\\SomeReallyLongClassName as Short;" in updated

    def test_comment_only_reference_is_ambiguous_and_kept(self) -> None:
        content = (
            "<?php\n"
            "use App\\Models\\MaybeUsedLater;\n"
            "class Small {\n"
            "\t// TODO: use MaybeUsedLater once the feature ships\n"
            "\tpublic function feeDue() { return 1; }\n"
            "}\n"
        )
        updated, n = _remove_unused_php_use_imports(content)
        assert n == 0, "comment-only reference is ambiguous -- must fail closed and keep the import"
        assert "use App\\Models\\MaybeUsedLater;" in updated

    def test_no_use_imports_is_a_no_op(self) -> None:
        content = "<?php\nclass Small {\n\tpublic function feeDue() { return 1; }\n}\n"
        updated, n = _remove_unused_php_use_imports(content)
        assert n == 0
        assert updated == content


class TestLocalFallbackRemainingAskedWork:
    def test_unused_imports_removed_when_objective_asks(self) -> None:
        content = (
            "<?php\n"
            "use App\\Models\\Used;\n"
            "use App\\Models\\NeverReferenced;\n"
            "class Small {\n"
            "\tpublic function feeDue() { return new Used(); }\n"
            "}\n"
        )
        result = local_fallback_php_controller_refactor(
            DiscoveryReport(config_files={"Small.php": content}),
            _OBJECTIVE_UNUSED_IMPORTS_ONLY,
        )
        assert result is not None
        assert len(result.edits) == 1
        assert "NeverReferenced" not in result.edits[0].new_content
        assert result.remaining_asked_work is False

    def test_already_refactored_file_with_unused_import_still_edits(self) -> None:
        """The exact incident shape: tenant-DB helper and section comments already
        present, but an unused import still remains -- the fallback must still
        produce a real edit, not treat 'my first two passes found nothing' as done."""
        content = (
            "<?php\n"
            "use App\\Models\\Used;\n"
            "use App\\Models\\NeverReferenced;\n"
            "class Small {\n"
            "\t// --- Fee cron actions ---\n"
            "\tpublic function feeDue() { $d = $this->tenantDatabaseName($x); return new Used(); }\n"
            "\tprivate function tenantDatabaseName($domainName) {\n"
            "\t\treturn '360_' . $domainName;\n"
            "\t}\n"
            "}\n"
        )
        result = local_fallback_php_controller_refactor(
            DiscoveryReport(config_files={"Small.php": content}),
            _OBJECTIVE_FULL,
        )
        assert result is not None
        assert len(result.edits) == 1
        assert "NeverReferenced" not in result.edits[0].new_content
        assert result.remaining_asked_work is False

    def test_remaining_work_the_fallback_cannot_do_is_flagged_not_silently_none(self) -> None:
        """extract_helpers is named in the objective but this fallback has no pass for
        it -- must report `remaining_asked_work=True` with the unmet signal, never a
        bare `None` a caller could misread as 'nothing to do'."""
        objective = (
            "Refactor Small.php: extract repeated DB boilerplate into a helper method "
            "and remove unused use-imports."
        )
        content = (
            "<?php\n"
            "use App\\Models\\Used;\n"
            "use App\\Models\\NeverReferenced;\n"
            "class Small {\n"
            "\tpublic function feeDue() { return new Used(); }\n"
            "}\n"
        )
        result = local_fallback_php_controller_refactor(
            DiscoveryReport(config_files={"Small.php": content}), objective,
        )
        assert result is not None
        assert len(result.edits) == 1  # the unused import removal still happened
        assert result.remaining_asked_work is True
        assert "extract_helpers" in result.unmet_signals

    def test_objective_naming_nothing_this_fallback_covers_returns_none(self) -> None:
        content = "<?php\nclass Small {\n\tpublic function feeDue() { return 1; }\n}\n"
        result = local_fallback_php_controller_refactor(
            DiscoveryReport(config_files={"Small.php": content}),
            "Add input validation to Small.php.",
        )
        assert result is None
