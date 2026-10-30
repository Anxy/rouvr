"""R7: submit gate is the only exit. Pure logic, no network."""
import json

import pytest

from rouvr.errors import Code, RouvrError
from rouvr.gate import (GateEvidence, count_prior_submits, evaluate, read_submission_file, submit_result)


def facts(**kw) -> GateEvidence:
    base = dict(expected_branch="rouvr/7/a1", pr_url="https://gh/pull/1", pr_head_branch="rouvr/7/a1",
                ci_status="success", criteria=("c1", "c2"), evidence_refs=frozenset({"testrun:a", "testrun:b"}),
                prior_submits=0)
    base.update(kw)
    return GateEvidence(**base)


def sub(**kw) -> dict:
    base = {"pr_url": "https://gh/pull/1",
            "claims": [{"criterion_id": "c1"}, {"criterion_id": "c2"}]}
    base.update(kw)
    return base


def test_happy_path_approves():
    v = evaluate(sub(), facts())
    assert (v.verdict, v.feedback) == ("approved", None)


def test_exit_without_submit_fails(tmp_path):
    assert read_submission_file(tmp_path) is None
    v = evaluate(None, facts())
    assert v.verdict == "failed" and v.feedback and "\n" not in v.feedback


def test_bad_submits_get_one_line_feedback(tmp_path):
    (tmp_path / "submit.json").write_text("{not json")
    assert evaluate(read_submission_file(tmp_path), facts()).verdict == "reject"
    v = evaluate(sub(pr_url="http://x"), facts())
    assert v.verdict == "reject" and v.feedback.startswith("BAD_SUBMIT ") and "\n" not in v.feedback
    v = evaluate(sub(), facts(pr_head_branch="other"))
    assert v.verdict == "reject" and v.feedback.startswith("PR_NOT_ON_BRANCH ")
    v = evaluate(sub(), facts(ci_status="pending"))
    assert v.verdict == "reject" and v.feedback.startswith("CI_NOT_GREEN ")
    v = evaluate(sub(), facts(criteria=("c1", "c2", "c3")))
    assert v.verdict == "reject" and v.feedback == "CRITERION_UNCLAIMED fix=claim-each-criterion criterion_id=c3"
    v = evaluate(sub(), facts(evidence_refs=frozenset()))
    assert v.verdict == "reject" and v.feedback.startswith("NO_R8_EVIDENCE ")


def test_three_rejects_block():
    rows = [{"type": "submit_attempt"}, {"type": "pr_record"}, {"type": "submit_attempt"},
            {"type": "submit_attempt"}]
    assert count_prior_submits(rows) == 3
    assert evaluate(sub(), facts(prior_submits=3)).verdict == "blocked"


def test_prior_submits_counted_per_attempt():
    rows = [{"type": "submit_attempt", "fence": "a1:verify:submit"},
            {"type": "submit_attempt", "fence": "a2:verify:submit"},
            {"type": "submit_attempt", "fence": "a1:verify:submit"},
            {"type": "submit_attempt", "fence": "a10:verify:submit"}]
    assert count_prior_submits(rows, "a1") == 2
    assert count_prior_submits(rows, "a2") == 1
    assert count_prior_submits(rows) == 4  # task-wide count used by verify: rejects accumulate across attempts


def test_fail_closed_on_garbage():
    for bad in ({}, {"pr_url": 7}, {"claims": "x"}, {"__malformed__": True}):
        v = evaluate(bad, facts())
        assert v.verdict == "reject" and v.feedback and "\n" not in v.feedback


def test_submit_result_tool_body():
    r = submit_result("https://gh/pull/1", [{"criterion_id": "c"}], prior_rounds=1)
    assert r["round"] == 2
    with pytest.raises(RouvrError) as ei:
        submit_result("http://x", [], prior_rounds=0)
    assert ei.value.code is Code.CONTRACT
    with pytest.raises(RouvrError) as ei:
        submit_result("https://gh/pull/1", [{"evidence_ref": "e"}], prior_rounds=0)
    assert ei.value.code is Code.CONTRACT
