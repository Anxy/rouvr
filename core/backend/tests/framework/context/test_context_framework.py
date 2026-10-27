"""F3 context framework tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.aix_core.framework.context import (
    ContextAssemblyPipeline,
    ContextBundle,
    ContextCompressor,
    DurableProvenanceStore,
    MemoryClass,
    classify_memory_source,
    durable_provenance_enabled,
)
from backend.aix_core.runtime.objective.contracts import UnderstandingEvidenceV1
from backend.hyeve_aix_service.config import settings


def test_flag_defaults_off() -> None:
    assert bool(getattr(settings, "AIX_FRAMEWORK_DURABLE_PROVENANCE", True)) is False
    assert durable_provenance_enabled() is False


def test_memory_taxonomy_five_tiers() -> None:
    assert {m.value for m in MemoryClass} == {
        "WORKING",
        "EPISODIC",
        "SEMANTIC",
        "PROCEDURAL",
        "DURABLE_PROVENANCE",
    }
    assert classify_memory_source("understanding.evidence") == MemoryClass.SEMANTIC
    assert classify_memory_source("ledger.provenance") == MemoryClass.DURABLE_PROVENANCE


@pytest.mark.asyncio
async def test_provenance_cold_restart_replay() -> None:
    store = DurableProvenanceStore()
    await store.record_intent(
        objective_id="obj-1",
        intent_text="Fix the login bug",
        intent_id="intent-1",
    )
    # Simulate cold restart with a fresh store replaying recorded events
    replayed = DurableProvenanceStore()
    for evt in store.events("obj-1"):
        await replayed.record_event(evt)
    history = await replayed.replay_history("obj-1")
    intent = history.get_intent("obj-1")
    assert intent is not None
    assert intent.text == "Fix the login bug"


def test_context_assembly_parity_with_capsule() -> None:
    understanding = UnderstandingEvidenceV1(
        objective_id="obj-1",
        cycle_id="cyc-1",
        files_read=("a.py", "b.py"),
        symbols_found=("Foo",),
        evidence_refs=("ev://1",),
        observed_problem="x",
        understanding_complete=True,
    )
    pipeline = ContextAssemblyPipeline()
    bundle, capsule = pipeline.assemble(
        step_id="step-1",
        understanding=understanding,
        target_scope=("a.py",),
        invariants=("keep API",),
        intent_text="edit a.py",
        intent_id="i1",
    )
    assert bundle.step_id == "step-1"
    assert "a.py" in bundle.target_files
    assert capsule.step_id == "step-1"
    assert capsule.relevant_files == ("a.py",)


def test_compression_preserves_law_a_tokens() -> None:
    bundle = ContextBundle(
        step_id="s1",
        intent_text="do the thing " * 200,
        target_files=("src/core.py", "src/api.py"),
        symbol_definitions=tuple(f"Sym{i}" for i in range(200)),
        previous_failure="TYPE_ERROR in src/core.py",
        validation_guidelines=("g1", "g2", "g3"),
        continuity_tokens=("CREATE_FILE.write:src/core.py", "entity:User"),
        evidence_refs=tuple(f"ev://{i}" for i in range(100)),
        memory_classes=(MemoryClass.WORKING, MemoryClass.SEMANTIC),
        token_count_estimate=50_000,
    )
    out = ContextCompressor.compress(bundle, token_budget=200)
    assert out.target_files == bundle.target_files
    assert out.continuity_tokens == bundle.continuity_tokens
    assert out.previous_failure == bundle.previous_failure
    assert out.intent_text  # never emptied
    assert out.token_count_estimate <= 200 or out.token_count_estimate < bundle.token_count_estimate


def test_no_experimental_capsule_import_in_framework_context() -> None:
    root = Path(__file__).resolve().parents[3] / "aix_core" / "framework" / "context"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from backend.aix_core.intelligence.context.experimental" not in text
        assert "import backend.aix_core.intelligence.context.experimental" not in text


def test_no_oc_wiring_yet() -> None:
    """F3 wiring is behind durable_provenance flag inside durable_api_backend only."""
    from backend.hyeve_aix_service.config import settings

    assert bool(getattr(settings, "AIX_FRAMEWORK_DURABLE_PROVENANCE", True)) is False
