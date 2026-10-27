"""C001 reachability schema, generator, and CI gate tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.aix_core.control_plane_reachability.generator import (
    SCHEMA_VERSION,
    default_manifest_path,
    generate_reachability_manifest,
    manifest_is_green,
    parse_mounted_routers,
    validate_manifest,
    write_manifest,
)
from backend.aix_core.control_plane_reachability.schema import (
    DISPOSITIONS,
    RUNTIME_STATUSES,
    ReachabilityComponent,
    ReachabilityManifest,
)


BACKEND_ROOT = Path(__file__).resolve().parents[3]
MAIN_PY = BACKEND_ROOT / "main.py"


def test_schema_rejects_invalid_runtime_status() -> None:
    with pytest.raises(ValueError, match="invalid runtime_status"):
        ReachabilityComponent.from_dict(
            {
                "component": "x",
                "kind": "package",
                "entrypoint": "x",
                "production_caller": "none",
                "runtime_status": "NOT_A_STATUS",
                "disposition": "KEEP",
            }
        )


def test_schema_rejects_invalid_disposition() -> None:
    with pytest.raises(ValueError, match="invalid disposition"):
        ReachabilityComponent.from_dict(
            {
                "component": "x",
                "kind": "package",
                "entrypoint": "x",
                "production_caller": "none",
                "runtime_status": "DEAD",
                "disposition": "MAYBE",
            }
        )


def test_parse_mounted_routers_matches_main_py() -> None:
    mounted = parse_mounted_routers(MAIN_PY)
    assert "objective_cycle_router" in mounted
    assert mounted["objective_cycle_router"] == "backend.routers.objective_cycle_router"
    assert "execution_kernel_router" in mounted
    assert "aix_orchestrator_v2_router" in mounted
    # C004-T02: LangGraph write router unmounted
    assert "langgraph_orchestration_router" not in mounted
    # Explicitly unmounted alternate executor entrypoints
    stems = {module.rsplit(".", 1)[-1] for module in mounted.values()}
    assert "devexecutor_runtime_router" not in stems
    assert "aix_runtime_pool_router" not in stems
    assert "langgraph_orchestration_router" not in stems
    assert len(mounted) == 18


def test_generate_manifest_classifies_every_mounted_router() -> None:
    manifest = generate_reachability_manifest(backend_root=BACKEND_ROOT)
    assert manifest.schema_version == SCHEMA_VERSION
    assert manifest_is_green(manifest), manifest.validation_errors

    mounted = parse_mounted_routers(MAIN_PY)
    for module in mounted.values():
        stem = module.rsplit(".", 1)[-1]
        match = next(c for c in manifest.components if c.component == f"router:{stem}")
        assert match.production_caller.startswith("backend.main.include_router")
        assert match.disposition in DISPOSITIONS
        assert match.runtime_status in RUNTIME_STATUSES


def test_generate_manifest_includes_objective_cycle_as_expand() -> None:
    manifest = generate_reachability_manifest(backend_root=BACKEND_ROOT)
    oc = next(c for c in manifest.components if c.component == "package:aix_core.objective_cycle")
    assert oc.disposition == "EXPAND"
    assert oc.runtime_status == "LIVE"
    assert "completion" in oc.completion_authority

    ek = next(c for c in manifest.components if c.component == "package:aix_core.execution_kernel")
    assert ek.disposition == "ADAPTER"

    lg = next(c for c in manifest.components if c.component == "router:langgraph_orchestration_router")
    assert lg.disposition == "REPLACE"
    assert lg.production_caller == "none"
    assert lg.runtime_status == "IMPLEMENTED_NOT_WIRED"

    dead_pw = next(c for c in manifest.components if c.component == "package:aix_core.patchworker")
    assert dead_pw.disposition == "ARCHIVE"
    assert dead_pw.runtime_status == "DEAD"


def test_unmounted_devexecutor_is_not_live() -> None:
    manifest = generate_reachability_manifest(backend_root=BACKEND_ROOT)
    de = next(c for c in manifest.components if c.component == "router:devexecutor_runtime_router")
    assert de.production_caller == "none"
    assert de.runtime_status == "IMPLEMENTED_NOT_WIRED"
    assert de.disposition == "ARCHIVE"


def test_validate_fails_on_unclassified_mounted_router() -> None:
    bad = ReachabilityManifest(
        schema_version=SCHEMA_VERSION,
        repository_revision="test",
        generated_from="test",
        components=[
            ReachabilityComponent(
                component="router:mystery_router",
                kind="router",
                entrypoint="backend.routers.mystery_router:router",
                production_caller="backend.main.include_router(mystery_router)",
                runtime_status="LIVE",
                disposition="INVESTIGATE",
                notes="Mounted but missing seed policy.",
            )
        ],
    )
    errors = validate_manifest(bad, mounted_router_stems={"mystery_router"})
    assert any(e.startswith("UNCLASSIFIED_MOUNTED_ROUTER:") for e in errors)


def test_manifest_round_trip_dict() -> None:
    manifest = generate_reachability_manifest(backend_root=BACKEND_ROOT)
    restored = ReachabilityManifest.from_dict(manifest.to_dict())
    assert len(restored.components) == len(manifest.components)
    assert restored.components[0].component == manifest.components[0].component


def test_checked_in_manifest_path_is_under_c001_evidence() -> None:
    path = default_manifest_path(BACKEND_ROOT)
    assert path.name == "reachability_manifest.v1.json"
    assert "C001" in str(path)
    assert "evidence" in str(path)


def test_write_and_reload_manifest(tmp_path: Path) -> None:
    manifest = generate_reachability_manifest(backend_root=BACKEND_ROOT)
    out = tmp_path / "reachability_manifest.v1.json"
    write_manifest(manifest, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["component_count"] == len(manifest.components)
    assert payload["component_count"] >= 100


def test_live_main_mounted_routes_covered_by_router_components() -> None:
    """L3: every main.py-mounted router has a green manifest disposition.

    Full OpenAPI boot requires monorepo `hyeve_core` (may be absent in isolated
    AIX checkouts). Capsule C001 DoD is satisfied by comparing the manifest to
    the production include_router graph in main.py; OpenAPI parity remains
    covered by existing endpoint_classification tests when hyeve_core is present.
    """
    mounted = parse_mounted_routers(MAIN_PY)
    assert len(mounted) == 18

    manifest = generate_reachability_manifest(backend_root=BACKEND_ROOT)
    assert manifest_is_green(manifest)

    live_routers = {
        c.component
        for c in manifest.components
        if c.kind == "router" and c.production_caller != "none"
    }
    for module in mounted.values():
        stem = module.rsplit(".", 1)[-1]
        assert f"router:{stem}" in live_routers

    # Competing control-plane authorities must remain explicitly classified.
    assert "router:objective_cycle_router" in live_routers
    assert "router:execution_kernel_router" in live_routers
    assert "router:aix_orchestrator_v2_router" in live_routers
    assert "router:langgraph_orchestration_router" not in live_routers

    # Alternate executor entrypoints must stay unmounted and non-LIVE.
    unmounted = {
        c.component: c
        for c in manifest.components
        if c.component
        in {
            "router:devexecutor_runtime_router",
            "router:aix_runtime_pool_router",
            "router:langgraph_orchestration_router",
        }
    }
    assert unmounted["router:devexecutor_runtime_router"].production_caller == "none"
    assert unmounted["router:aix_runtime_pool_router"].production_caller == "none"
    assert unmounted["router:langgraph_orchestration_router"].production_caller == "none"


def test_openapi_control_plane_paths_when_hyeve_core_available() -> None:
    """Optional L3/L4: exercise OpenAPI when monorepo hyeve_core is importable."""
    pytest.importorskip("hyeve_core")
    from backend.main import app

    app.openapi_schema = None
    schema = app.openapi()
    paths = set(schema.get("paths", {}))
    assert any(p.startswith("/aix/objectives") for p in paths)
    assert any(p.startswith("/aix/execution-kernel") for p in paths)
    assert any(p.startswith("/api/aix/orchestrator-v2") for p in paths)
    # C004-T02: LangGraph write path must be absent from production OpenAPI.
    assert "/api/aix/langgraph/run" not in paths
