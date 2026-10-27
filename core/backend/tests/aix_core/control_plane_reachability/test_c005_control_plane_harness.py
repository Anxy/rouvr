"""C005: control-plane regression harness.

Proves alternate mutation authorities cannot independently execute, and that
canonical objective_cycle remains the sole production write surface for
objectives.
"""

from __future__ import annotations

from pathlib import Path

from backend.aix_core.control_plane_reachability.generator import (
    generate_reachability_manifest,
    manifest_is_green,
    parse_mounted_routers,
)
from backend.aix_core.runtime.governance.completion_authority import CANONICAL_EMITTER
from backend.legacy.langgraph.router import LANGGRAPH_WRITES_RETIRED
from backend.routers.aix_orchestrator_v2_router import ORCHESTRATOR_V2_MUTATION_RETIRED


BACKEND_ROOT = Path(__file__).resolve().parents[3]
MAIN_PY = BACKEND_ROOT / "main.py"


def test_single_production_mutation_surface_from_main() -> None:
    mounted = parse_mounted_routers(MAIN_PY)
    assert "objective_cycle_router" in mounted
    assert "langgraph_orchestration_router" not in mounted
    assert "devexecutor_runtime_router" not in mounted
    assert "aix_runtime_pool_router" not in mounted


def test_alternate_mutation_authorities_retired_flags() -> None:
    assert LANGGRAPH_WRITES_RETIRED is True
    assert ORCHESTRATOR_V2_MUTATION_RETIRED is True


def test_completion_authority_emitter_is_singular() -> None:
    assert CANONICAL_EMITTER == "backend.aix_core.runtime.governance.completion_authority.decide_completion"


def test_reachability_manifest_still_green_after_retirement() -> None:
    manifest = generate_reachability_manifest(backend_root=BACKEND_ROOT)
    assert manifest_is_green(manifest), manifest.validation_errors
    lg = next(c for c in manifest.components if c.component == "router:langgraph_orchestration_router")
    assert lg.production_caller == "none"


def test_dead_stack_packages_remain_archived_disposition() -> None:
    manifest = generate_reachability_manifest(backend_root=BACKEND_ROOT)
    for name in (
        "package:aix_core.patchworker",
        "package:aix_core.runtime_langgraph_mvp",
        "package:aix_core.planner_coder_lane",
        "package:aix_core.e2e_fake_runtime",
    ):
        item = next(c for c in manifest.components if c.component == name)
        assert item.disposition == "ARCHIVE"
        assert item.runtime_status in {"DEAD", "SCAFFOLD", "TEST_ONLY"}


def test_canonical_identity_and_completion_modules_importable() -> None:
    from backend.aix_core import runtime_identity
    from backend.aix_core.runtime.governance import completion_authority, command_event_contract

    assert runtime_identity.IDENTITY_CONTRACT_VERSION.startswith("aix.runtime_identity")
    assert completion_authority.COMPLETION_AUTHORITY_VERSION.startswith("aix.completion_authority")
    assert command_event_contract.COMMAND_EVENT_CONTRACT_VERSION.startswith("aix.command_event")
