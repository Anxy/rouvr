"""EX5-S10: Organizational Intelligence Validation.

Charter goal: "Answer org reuse / failure / recommendation queries."
Assessed first: P11's reason_organizationally() already composed
Knowledge (P3), graph edges (P2), semantic retrieval (P9), and planning
briefs (P10) — three golden scenarios (G1-G3) covering precedent search,
conflict reports, and pre-planning briefs. It never referenced the
Capability Registry (P6) at all — "org reuse" specifically, finding an
existing registered capability instead of building again, had no path
into the reasoning engine. This adds the fourth golden scenario (G4,
capability_reuse_search) that closes that gap.
"""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.executor.capability_registry import RegisteredCapability
from backend.aix_core.runtime.executor.execution_kernel_adapter.service import KernelService


@pytest.mark.asyncio
async def test_g4_capability_reuse_search_finds_relevant_capability_as_recommendation_not_fact() -> None:
    svc = KernelService()
    svc.capability_registry.register(
        RegisteredCapability(
            capability_key="device_registration_flow",
            version="v1",
            tenant_id="t1",
            display_name="Device Registration Flow",
            description="Register a tenant-scoped device before barcode binding.",
            cost_class="low",
            risk_class="low",
        )
    )
    svc.capability_registry.register(
        RegisteredCapability(
            capability_key="unrelated_reporting_export",
            version="v1",
            tenant_id="t1",
            display_name="Reporting Export",
            description="Export scheduled reports as CSV.",
            cost_class="low",
            risk_class="low",
        )
    )

    result = await svc.reason_organizationally(
        tenant_id="t1", mode="capability_reuse_search",
        question="how do we handle device registration and barcode binding",
    )

    assert result["state_mutations"] == []
    assert "capability_registry" in result["sources_used"]
    matched = [a for a in result["answers"] if a["kind"] == "recommendation"]
    assert matched, "a clearly relevant capability must surface as a recommendation"
    assert matched[0]["is_fact"] is False, (
        "reuse suggestions are never asserted as fact — fit is a judgment call"
    )
    assert matched[0]["provenance"][0]["capability_key"] == "device_registration_flow"
    assert not any(
        a["provenance"] and a["provenance"][0].get("capability_key") == "unrelated_reporting_export"
        for a in result["answers"]
    ), "an unrelated capability must not be recommended just because it exists"

    options = result["options"]
    assert any(o["action"] == "register_capability_dependency" for o in options), (
        "reuse must be adopted via a real binding, never a silent write"
    )


@pytest.mark.asyncio
async def test_g4_no_match_is_honest_not_hidden() -> None:
    svc = KernelService()
    svc.capability_registry.register(
        RegisteredCapability(
            capability_key="totally_unrelated_thing",
            version="v1",
            tenant_id="t1",
            display_name="Totally Unrelated Thing",
            description="Nothing to do with the query at all.",
            cost_class="low",
            risk_class="low",
        )
    )
    result = await svc.reason_organizationally(
        tenant_id="t1", mode="capability_reuse_search", question="quantum entanglement scheduling",
    )
    assert not any(a["kind"] == "recommendation" for a in result["answers"])
    assert any(a["kind"] == "inference" and "no registered capability" in a["statement"].lower() for a in result["answers"])


@pytest.mark.asyncio
async def test_g4_is_now_a_golden_scenario() -> None:
    svc = KernelService()
    scenarios = await svc.reasoning_golden_scenarios()
    ids = {s["id"] for s in scenarios}
    assert {"G1", "G2", "G3", "G4"} <= ids
    g4 = next(s for s in scenarios if s["id"] == "G4")
    assert g4["mode"] == "capability_reuse_search"
