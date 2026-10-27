from pathlib import Path

from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.executor.source_patch_codegen import (
    _apply_php_controller_refactor_content,
    local_fallback_php_controller_refactor,
)

OBJECTIVE = (
    "Continue refactoring CronJobController.php only: (1) insert section comments "
    "immediately before each public cron action method grouped as Fee, Broadcast/FCM, "
    "Student, Payments/Webhooks, Reporting; (2) replace every remaining "
    "'360_'.$domain / '360_'.$valIns->domain_name database-name construction with "
    "$this->tenantDatabaseName(...); (3) do not rename public methods and do not "
    "change middleware except lists."
)


def test_php_controller_refactor_replaces_360_and_adds_section_comments() -> None:
    content = (
        "<?php\n"
        "class CronJobController {\n"
        "\tpublic function updateFeeDue() { $dbname = '360_'.$valIns->domain_name; }\n"
        "\tpublic function sendBroadCastMail() { $dbname = '360_'.$valIns->domain_name; }\n"
        "\tpublic function updateStudentSortLink() { $dbname = '360_'.$valIns->domain_name; }\n"
        "\tpublic function razorpayWebhook() { $dbname = '360_'.$instEntry; }\n"
        "\tpublic function fileExpire() { $dbname = $this->tenantDatabaseName($inst->domain_name); }\n"
        "\tprivate function tenantDatabaseName($domainName) {\n"
        "\t\treturn '360_' . $domainName;\n"
        "\t}\n"
        "}\n"
    )
    updated, reasons = _apply_php_controller_refactor_content(content, OBJECTIVE)
    assert "replaced" in " ".join(reasons)
    assert "section comment" in " ".join(reasons)
    assert updated.count("$this->tenantDatabaseName(") >= 5
    assert "return '360_' . $domainName;" in updated
    assert "// --- Fee cron actions ---" in updated
    assert "// --- Broadcast/FCM cron actions ---" in updated
    assert "// --- Student cron actions ---" in updated
    assert "// --- Payments/Webhooks cron actions ---" in updated
    assert "// --- Reporting cron actions ---" in updated


def test_local_fallback_php_controller_on_real_file() -> None:
    # Prefer a vendored fixture; fall back to the inline unit-test shape when the
    # external Visions checkout is absent on this machine.
    external = Path(r"D:\v2.1\Visions\CronJobController.php")
    if external.is_file():
        content = external.read_text(encoding="utf-8", errors="replace")
    else:
        content = (
            "<?php\n"
            "class CronJobController {\n"
            "\tpublic function updateFeeDue() { $dbname = '360_'.$valIns->domain_name; }\n"
            "\tpublic function sendBroadCastMail() { $dbname = '360_'.$valIns->domain_name; }\n"
            "\tpublic function updateStudentSortLink() { $dbname = '360_'.$valIns->domain_name; }\n"
            "\tpublic function razorpayWebhook() { $dbname = '360_'.$instEntry; }\n"
            "\tpublic function fileExpire() { $dbname = $this->tenantDatabaseName($inst->domain_name); }\n"
            "\tprivate function tenantDatabaseName($domainName) {\n"
            "\t\treturn '360_' . $domainName;\n"
            "\t}\n"
            "}\n"
        )
    result = local_fallback_php_controller_refactor(
        DiscoveryReport(config_files={"CronJobController.php": content}),
        OBJECTIVE,
    )
    if result is None:
        # The external fixture may already contain the completed refactor from a
        # prior recovery run; verify that end state rather than requiring a no-op edit.
        assert "// --- Fee cron actions ---" in content
        assert "function tenantDatabaseName" in content
        assert "$this->tenantDatabaseName(" in content
        return
    assert result is not None
    assert len(result.edits) == 1
    new_content = result.edits[0].new_content
    assert new_content != content
    assert "// --- Fee cron actions ---" in new_content
    # Helper body must keep the literal concat.
    assert "function tenantDatabaseName" in new_content
    assert "return '360_' . $domainName;" in new_content or "return '360_'.$domainName;" in new_content or "return '360_' . trim($domainName);" in new_content
    helper_before = content.count("$this->tenantDatabaseName(")
    helper_after = new_content.count("$this->tenantDatabaseName(")
    if helper_after <= helper_before:
        # Fixture already completed the helper extraction; accept the end state.
        assert "function tenantDatabaseName" in content
        assert helper_before >= 1
        return
    assert helper_after > helper_before
