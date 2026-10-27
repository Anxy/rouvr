"""C024 context budget / compaction tests."""

from __future__ import annotations

from backend.aix_core.runtime.objective.objective_contract.context_budget import (
    ContentAddressedCache,
    fit_thirty_step_mission,
)
from backend.aix_core.runtime.objective.objective_contract.context_manifest import add_item, new_manifest


def test_c024_compaction_preserves_immutables() -> None:
    m = new_manifest(tenant_id="t", run_id="cyc")
    add_item(m, role="objective", source="obj", content="Ship feature X with DoD")
    add_item(m, role="approval", source="appr", content="approved gate_1")
    add_item(m, role="plan", source="plan", content="plan revision 1")
    add_item(m, role="evidence", source="ev", content="evidence hash abc")
    for i in range(30):
        add_item(
            m,
            role="working",
            source=f"scratch-{i}",
            content=("working note " + str(i) + " ") * 80,
            relevance=0.1,
        )
    result = fit_thirty_step_mission(m)
    assert result["ok"] is True
    assert result["immutable_count"] == 4
    roles = {i.role for i in m.items if not i.exclusion_reason}
    assert {"objective", "approval", "plan", "evidence"} <= roles


def test_c024_cache_content_addressed() -> None:
    cache = ContentAddressedCache()
    k1 = cache.put("hello")
    k2 = cache.put("hello")
    assert k1 == k2
    assert cache.get(k1) == "hello"
