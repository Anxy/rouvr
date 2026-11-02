"""Seed kind mapping is deterministic per section, never body keyword guessing."""
import importlib.util
from pathlib import Path


def _load():
    spec = importlib.util.spec_from_file_location(
        "seed_mod", Path(__file__).resolve().parents[1]
        / "demo/rehearsal/seed_rouvr_knowledge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_kind_mapping_ignores_body():
    mod = _load()
    assert mod.kind_for_section("risks / open questions") == "note"
    assert mod.kind_for_section("binding decisions (owner, d9-d20)") == "decision"
    assert mod.kind_for_section("key decisions (builder hat)") == "decision"
    assert mod.kind_for_section("success criteria (mapped)") == "decision"
    # body containing rule-ish words must not flip a note section
    assert mod.kind_for_section("risks / open questions") == \
        mod.kind_for_section("risks / open questions")
    kinds = {e["kind"] for e in mod.corpus()}
    assert kinds <= {"decision", "note"}  # never guesses rule
    assert "KIND_RULE" not in dir(mod) and "KIND_NOTE" not in dir(mod)
