"""Regression: UI surface objective coverage rejects type-only diffs."""

from backend.aix_core.objective_cycle.objective_coverage import check_objective_coverage


def test_ui_surface_coverage_rejects_client_type_only_diff() -> None:
    objective = (
        "Implement and update the AIX frontend live execution experience to show a "
        "Cycle Summary strip with phase and repair_count. Add focused UI or API tests."
    )
    diff = (
        "--- a/src/lib/execution-kernel-client.ts\n"
        "+++ b/src/lib/execution-kernel-client.ts\n"
        "@@ -1,3 +1,10 @@\n"
        "+export type CycleSummary = {\n"
        "+  phase?: string | null;\n"
        "+  repair_count?: number | null;\n"
        "+};\n"
    )
    result = check_objective_coverage(objective=objective, diff_text=diff)
    assert "ui_surface_change" in result.triggered_signals
    assert result.satisfied is False


def test_ui_surface_coverage_accepts_tsx_page_diff() -> None:
    objective = (
        "Implement a Cycle Summary strip on the live execution experience and add tests."
    )
    diff = (
        "--- a/src/app/(admin)/execution/page.tsx\n"
        "+++ b/src/app/(admin)/execution/page.tsx\n"
        "@@ -10,0 +11,6 @@\n"
        '+      <div data-testid="cycle-summary">Cycle Summary</div>\n'
    )
    result = check_objective_coverage(objective=objective, diff_text=diff)
    assert result.satisfied is True
