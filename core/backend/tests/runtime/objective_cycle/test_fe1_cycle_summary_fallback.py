"""Regression: cycle summary strip offline fallback (FE#1)."""

from backend.aix_core.runtime.objective.discovery import DiscoveryReport
from backend.aix_core.runtime.executor.source_patch_codegen import (
    local_fallback_cycle_summary_strip,
)


def test_cycle_summary_strip_fallback_creates_tsx_surface() -> None:
    page = (
        '"use client";\n'
        'import { FixtureModeWarning } from "@/components/aix/FixtureModeWarning";\n'
        "export default function ExecutionPage() {\n"
        "  const liveStatus = { phase: null };\n"
        "  const isFixture = false;\n"
        "  return (\n"
        "    <div>\n"
        '      <PageBreadcrumb pageTitle="Execution" />\n'
        "      {isFixture && <FixtureModeWarning fixtureId=\"x\" />}\n"
        "    </div>\n"
        "  );\n"
        "}\n"
    )
    report = DiscoveryReport()
    report.config_files = {
        "src/app/(admin)/execution/page.tsx": page,
    }
    result = local_fallback_cycle_summary_strip(
        report,
        "Implement a Cycle Summary strip on the live execution experience with repair_count.",
    )
    assert result is not None
    paths = {e.path.replace("\\", "/") for e in result.edits}
    assert "src/components/aix/CycleSummaryStrip.tsx" in paths
    assert any(p.endswith("execution/page.tsx") for p in paths)
    comp = next(e for e in result.edits if e.path.endswith("CycleSummaryStrip.tsx"))
    assert "data-testid=\"cycle-summary-strip\"" in (comp.new_content or "")
    assert "repair_count" in (comp.new_content or "")
