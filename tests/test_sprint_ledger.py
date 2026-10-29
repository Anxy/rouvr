"""Dev-process tooling: sprint lifecycle first-class functions (tools/sprint_ledger.py)."""
import copy
from pathlib import Path

import pytest

from tools import sprint_ledger as L

DOC = {"version": 1, "updated": "2026-10-29",
       "machine": {"states": L.STATES, "transitions": [
           {"from": f, "to": t, "by": b, "requires": r} | ({"verdict": v} if v else {})
           for f, t, b, r, v in L.TRANSITIONS]},
       "sprints": []}


def fresh(tmp_path):
    doc = copy.deepcopy(DOC)
    (tmp_path / "sheet.md").write_text("# sheet")
    L.register(doc, "S100", "sheet.md")
    doc["sprints"][0]["file"] = str(tmp_path / "sheet.md")
    return doc


def test_full_thread_with_rework(tmp_path):
    doc = fresh(tmp_path)
    L.assign(doc, "S100", worker="w", supervisor="sup")
    L.start(doc, "S100", "TaskKind IMPLEMENTATION; scope x.py")
    L.submit(doc, "S100", tests="12 pass", evidence="pytest out")
    L.review(doc, "S100", "change_required", by="sup", comment="scope too wide")
    assert L.get(doc, "S100")["state"] == "running"
    L.submit(doc, "S100", tests="13 pass", evidence="pytest out 2")
    L.review(doc, "S100", "approved", by="sup", verification="re-ran 13 pass")
    L.closeout(doc, "S100", by="sup", ref="sheet §11")
    s = L.get(doc, "S100")
    assert s["state"] == "done"
    assert [c["verdict"] for c in s["cycles"]] == ["change_required", "approved"]
    assert L.validate_doc(doc, tmp_path) == []


def test_refusals_leave_state_untouched(tmp_path):
    doc = fresh(tmp_path)
    with pytest.raises(L.LedgerError):
        L.review(doc, "S100", "approved", by="sup", verification="v")  # not in needs_review
    L.start(doc, "S100", "x")
    with pytest.raises(L.LedgerError):
        L.review(doc, "S100", "approved", by="sup", verification="v")  # running, not needs_review
    with pytest.raises(L.LedgerError):
        L.closeout(doc, "S100", by="sup", ref="r")  # illegal jump
    with pytest.raises(L.LedgerError):
        L.start(doc, "S100", "")  # empty pre_task fails requires
    L.submit(doc, "S100", tests="t", evidence="e")
    with pytest.raises(L.LedgerError):
        L.review(doc, "S100", "change_required", by="sup")  # comment required
    with pytest.raises(L.LedgerError):
        L.review(doc, "S100", "approved", by="sup")  # verification required
    s = L.get(doc, "S100")
    assert s["state"] == "needs_review" and s["cycles"] == []  # refusals mutated nothing
    with pytest.raises(L.LedgerError):
        L.review(doc, "S100", "maybe", by="sup")  # bad verdict


def two_sprints(tmp_path):
    doc = copy.deepcopy(DOC)
    (tmp_path / "a.md").write_text("# a"); (tmp_path / "b.md").write_text("# b")
    L.register(doc, "S200", "a.md", depends_on=["S201"])
    L.register(doc, "S201", "b.md")
    for s in doc["sprints"]:
        s["file"] = str(tmp_path / Path(s["file"]).name)
    return doc


def test_claim_next_blockers_and_self_review(tmp_path):
    doc = two_sprints(tmp_path)
    assert L.next_sprint(doc)["id"] == "S201"  # S200 waits on S201
    with pytest.raises(L.LedgerError):
        L.claim(doc, "S200", by="w1", pre_task="x")  # dependency unfinished
    s = L.claim(doc, "S201", by="w1", pre_task="x")
    assert (s["state"], s["worker"]) == ("running", "w1")
    assert L.claim(doc, "S201", by="w1", pre_task="x") is s  # resume own claim
    with pytest.raises(L.LedgerError):
        L.claim(doc, "S201", by="w2", pre_task="x")  # held by w1
    L.submit(doc, "S201", tests="t", evidence="e")
    with pytest.raises(L.LedgerError):
        L.review(doc, "S201", "approved", by="w1", verification="v")  # self-review
    L.review(doc, "S201", "approved", by="sup", verification="v")
    L.closeout(doc, "S201", by="sup", ref="r")
    assert L.next_sprint(doc)["id"] == "S200"  # dep done: S200 now claimable


def test_block_resume_and_validate_errors(tmp_path):
    doc = fresh(tmp_path)
    L.block(doc, "S100", by="supervisor", note="waiting on grant")
    assert L.get(doc, "S100")["state"] == "blocked"
    L.resume(doc, "S100", by="supervisor", note="grant landed")
    assert L.get(doc, "S100")["state"] == "queued"
    doc["sprints"][0]["state"] = "done"
    errors = L.validate_doc(doc, tmp_path)
    assert any("done but missing" in e for e in errors)
    doc["sprints"][0]["state"] = "banana"
    assert any("unknown state" in e for e in L.validate_doc(doc, tmp_path))
