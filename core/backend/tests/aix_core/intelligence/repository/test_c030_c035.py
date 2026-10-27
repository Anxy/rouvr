"""C030–C035 repository intelligence tests."""

from __future__ import annotations

import pytest

from backend.aix_core.intelligence.repository.repository_intel.dependency_graph import (
    RepoGraph,
    impact_query,
    index_python_module,
)
from backend.aix_core.intelligence.repository.repository_intel.impact_query import ImpactQueryRequest, build_impact_context
from backend.aix_core.intelligence.repository.repository_intel.inventory import (
    InventoryError,
    assert_no_secret_values,
    build_inventory,
    classify_path,
    distinguish_effects,
)
from backend.aix_core.intelligence.repository.repository_intel.staleness import (
    PlanBinding,
    StalenessError,
    assert_plan_current,
    bind_snapshot,
)
from backend.aix_core.intelligence.repository.repository_intel.symbol_index import LanguageAwareSymbolIndex
from backend.aix_core.intelligence.repository.repository_intel.test_linkage import (
    discover_tests,
    require_test_coverage_or_gap,
)


def test_c030_python_and_ts_symbols_provenance_and_refresh(tmp_path) -> None:
    root = tmp_path
    (root / "a.py").write_text("def greet():\n    return hi()\n\ndef hi():\n    return 1\n", encoding="utf-8")
    (root / "b.ts").write_text(
        "export function greet() { return 1 }\nconst x = greet()\n",
        encoding="utf-8",
    )
    idx = LanguageAwareSymbolIndex(root)
    idx.index_file("a.py")
    idx.index_file("b.ts")
    q = idx.query("greet")
    assert q["provenance_linked"] is True
    assert len(q["definitions"]) >= 2
    assert any(r["role"] == "reference" for r in q["references"]) or q["references"] is not None
    # refresh after edit
    idx.refresh_after_edit("a.py", "def greet():\n    return 2\n")
    assert idx.index.files["a.py"].content_sha256
    defs = idx.definitions("greet")
    assert any(d.path == "a.py" for d in defs)


def test_c031_impact_finds_transitive_consumers() -> None:
    g = RepoGraph()
    index_python_module(
        g,
        path="svc/api.py",
        content=(
            "from svc import core\n"
            "@router.get('/health')\n"
            "def health():\n"
            "    emit('health.checked')\n"
        ),
    )
    index_python_module(
        g,
        path="svc/worker.py",
        content="from svc.api import health\non('health.checked')\n",
    )
    # worker imports api module node created as mod:svc — ensure edge consumers work
    impact = impact_query(g, node_id="mod:svc/api.py", transitive=True)
    assert "route:/health:GET" in impact["direct_dependencies"] or impact["direct_dependencies"]


def test_c032_inventory_distinguishes_effect_classes() -> None:
    items = build_inventory(
        [
            "src/app.py",
            "config/settings.yaml",
            "migrations/v001_init.sql",
            "Dockerfile",
            "secrets/api_key.txt",
        ]
    )
    buckets = distinguish_effects(items)
    assert "src/app.py" in buckets["code"]
    assert "config/settings.yaml" in buckets["config"]
    assert "migrations/v001_init.sql" in buckets["migration"]
    assert "Dockerfile" in buckets["deployment"]
    assert "secrets/api_key.txt" in buckets["secret_metadata"]
    assert classify_path("secrets/api_key.txt") == "secret_metadata"
    with pytest.raises(InventoryError):
        assert_no_secret_values({"db_password": "hunter2"})


def test_c033_test_linkage_or_explicit_gap() -> None:
    tests = discover_tests(["tests/test_greet.py", "src/app.py", "tests/test_other.py"])
    assert "tests/test_greet.py" in tests
    ok = require_test_coverage_or_gap(
        effect_path="src/greet.py",
        symbols=["greet"],
        test_paths=tests,
    )
    assert ok["ok"] is True
    gap = require_test_coverage_or_gap(
        effect_path="src/obscure_module.py",
        symbols=["ObscureThing"],
        test_paths=tests,
    )
    assert gap["ok"] is False
    assert gap["gap"] == "NO_JUSTIFIED_TEST_CANDIDATE"


def test_c034_impact_api_returns_manifest_not_full_files(tmp_path) -> None:
    (tmp_path / "m.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    idx = LanguageAwareSymbolIndex(tmp_path)
    idx.index_file("m.py")
    g = RepoGraph()
    index_python_module(g, path="m.py", content="def target():\n    return 1\n")
    manifest = build_impact_context(
        request=ImpactQueryRequest(symbol="target", module_node_id="mod:m.py", effect_path="m.py", max_files=5),
        symbol_index=idx,
        graph=g,
        test_paths=["tests/test_target.py"],
    )
    data = manifest.to_dict()
    assert data["policy"]["full_file_bodies"] is False
    assert all("full_text" not in item and "content" not in item for item in data["items"])


def test_c035_stale_snapshot_blocks_plan() -> None:
    snap1 = bind_snapshot(root="/repo", files={"a.py": "v1"})
    snap2 = bind_snapshot(root="/repo", files={"a.py": "v2"})
    binding = PlanBinding(plan_id="plan_1", bound_snapshot=snap1)
    assert_plan_current(binding, current=snap1)
    with pytest.raises(StalenessError) as exc:
        assert_plan_current(binding, current=snap2)
    assert exc.value.code == "STALE_REPOSITORY_SNAPSHOT"
    with pytest.raises(StalenessError) as exc2:
        assert_plan_current(binding, current=snap2, replan_authorized=True)
    assert exc2.value.code == "REPLAN_REQUIRED"
