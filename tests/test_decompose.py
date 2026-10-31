"""Decomposer-lite tests (S021): mini-language, validator, run gate, parent check, decompose, demo."""
import json
from pathlib import Path

import pytest

from rouvr.decompose import validator as V


def _raises_parse(v):
    with pytest.raises(V.VerifierParseError):
        V.parse_verifier(v)


def test_mini_parses_four_primitives():
    assert V.parse_verifier({"test_passes": ["tests/test_x.py::test_y", "abc"]}) == (
        "test_passes", "tests/test_x.py::test_y", "abc")
    assert V.parse_verifier({"no_changes_outside": [["a/", "b"]]}) == (
        "no_changes_outside", ("a/", "b"))
    assert V.parse_verifier({"path_absent": ["gone.txt"]}) == ("path_absent", "gone.txt")
    node = V.parse_verifier({"and": [{"path_absent": ["x"]}]})
    assert node[0] == "and" and len(node[1]) == 1


def test_mini_rejects_unknown_primitives():
    _raises_parse({"http_get": ["http://x"]})
    _raises_parse({"test_passes": ["no-colons-here", "abc"]})
    _raises_parse({"test_passes": ["a.py::t"]})
    _raises_parse({"no_changes_outside": ["not-a-list-of-list"]})
    _raises_parse({"and": []})
    _raises_parse(["and"])
    _raises_parse({"and": [{"test_passes": ["a.py::t", "s"]}, {"bogus": []}]})


def _task(verifier, **kw):
    t = {"id": "T", "allowed_paths": ["rouvr/decompose/"], "pinned_paths": ["roadmap.json"],
         "covers": ["R"], "verifier": verifier, "verifier_fails_on_head": "assertion"}
    t.update(kw)
    return t


def test_validator_rejects_hollow_verifier(tmp_path):
    """A verifier that passes on the current head is hollow, not good."""
    ok, notes = V.validate_task(_task({"path_absent": ["definitely-not-here.txt"]}),
                                {"R"}, tmp_path)
    assert not ok and any("hollow" in n for n in notes)


def test_validator_rejects_overlap(tmp_path):
    ok, notes = V.validate_task(
        _task({"path_absent": ["x"]}, allowed_paths=["a/"], pinned_paths=["a/b.py"]),
        {"R"}, tmp_path)
    assert not ok and any("overlap" in n for n in notes)
    ok, _ = V.validate_task(
        _task({"path_absent": ["x"]}, allowed_paths=["a/"], pinned_paths=["a/"]),
        {"R"}, tmp_path)
    assert not ok


def _canned(outcome, detail="d"):
    def run(node, root):
        return outcome, detail
    return run


def test_validator_requires_unanimous_assertion_fail(tmp_path):
    """ERROR or disagreement or PASS never counts as a failing verifier."""
    from rouvr.decompose import validator as VV
    real = VV.run_verifier
    try:
        VV.run_verifier = lambda node, root: ("PASS", "ok")
        ok, notes = V.validate_task(_task({"path_absent": ["x"]}), {"R"}, tmp_path, runs=2)
        assert not ok and any("hollow" in n for n in notes)
        calls = {"n": 0}

        def flaky(node, root):
            calls["n"] += 1
            return ("FAIL_ASSERT", "a") if calls["n"] % 2 else ("FAIL_ASSERT", "b")
        VV.run_verifier = flaky
        ok, notes = V.validate_task(_task({"path_absent": ["x"]}), {"R"}, tmp_path, runs=4)
        assert not ok and any("flaky" in n for n in notes)
        VV.run_verifier = lambda node, root: ("ERROR", "boom")
        ok, notes = V.validate_task(_task({"path_absent": ["x"]}), {"R"}, tmp_path, runs=2)
        assert not ok and any("error" in n for n in notes)
    finally:
        VV.run_verifier = real


def test_run_gate_fails_pinned_touch(tmp_path):
    """Touching a pinned path fails the gate even when the verifier passes."""
    from rouvr.decompose import runner
    task = {"id": "T", "allowed_paths": ["src/"], "pinned_paths": ["src/lock.py"],
            "verifier": {"path_absent": ["nothing-here"]}}
    receipt = runner.run_gate(task, tmp_path, _run=_canned("PASS"),
                              _changed=lambda root: ["src/lock.py"])
    assert receipt["verdict"] == "fail"
    assert receipt["pinned_touched"] == ["src/lock.py"]
    receipt = runner.run_gate(task, tmp_path, _run=_canned("PASS"),
                              _changed=lambda root: ["src/other.py"])
    assert receipt["verdict"] == "pass"
    assert receipt["pinned_touched"] == []


def test_parent_check_flags_decomposition_failure(tmp_path):
    """Green children plus a red parent is DECOMPOSITION_FAILURE, not pass."""
    from rouvr.decompose import runner

    def fake_run(node, root):
        name = node[1] if node[0] == "test_passes" else ""
        if "parent" in name:
            return ("FAIL_ASSERT", "assert combo wrong")
        return ("PASS", "ok")
    out = runner.check_parent(("test_passes", "t.py::test_parent", "s"),
                              [("test_passes", "t.py::test_a", "s"),
                               ("test_passes", "t.py::test_b", "s")],
                              tmp_path, _run=fake_run)
    assert out["verdict"] == "DECOMPOSITION_FAILURE"
    assert out["action"] == "re-decompose"
    out = runner.check_parent(("test_passes", "t.py::test_parent_ok", "s"),
                              [("test_passes", "t.py::test_a", "s")],
                              tmp_path, _run=lambda node, root: ("PASS", "ok"))
    assert out["verdict"] == "COMPOSES"


def test_decompose_one_candidate():
    """One model call, one candidate; malformed output refused."""
    from rouvr.decompose import decompose as D
    candidate = {"requirements": [{"id": "R1", "text": "t", "source_ref": "s"}],
                 "tasks": [{"id": "T1", "title": "t", "type": "build",
                            "allowed_paths": ["a/"], "pinned_paths": ["b"],
                            "verifier": {"path_absent": ["b"]}, "depends_on": [],
                            "covers": ["R1"]}],
                 "coverage_ledger": {"R1": ["T1"]}, "dropped_content": []}

    class FakeModel:
        name = "meta"
        def chat(self, messages):
            return json.dumps(candidate)
    out = D.decompose_brief("brief", model=FakeModel())
    assert out == candidate

    class Garbage:
        name = "meta"
        def chat(self, messages):
            return "not json"
    with pytest.raises(RuntimeError):
        D.decompose_brief("brief", model=Garbage())

    class Stub:
        name = "stub"
        def chat(self, messages):  # pragma: no cover
            raise AssertionError("must not be called")
    with pytest.raises(RuntimeError, match="[Ss]tub|model"):
        D.decompose_brief("brief", model=Stub())


def test_demo_covers_four_beats():
    body = (Path(__file__).parent.parent / "demo" / "decompose-demo.sh").read_text()
    for beat in ("BEAT 1", "BEAT 2", "BEAT 3", "BEAT 4"):
        assert beat in body
