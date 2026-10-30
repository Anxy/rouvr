"""Stage-shape mapping (A1-live): envelope unwrap, canonical task, role pair. No network."""
import httpx
import pytest

from rouvr.errors import Code, RouvrError
from rouvr.muse import criterion_lines
from rouvr.personas import PersonaRegistry
from rouvr.workhub.client import WorkHubClient, canonical_task

STAGE_TASK = {"id": "01ABC", "task_number": 15, "title": "T", "description": "D", "status": "queued",
              "record_version": 1, "active_execution_contract_ref": "workhub://tasks/15/execution-contracts/1",
              "metadata_json": {"acceptance_criteria": ["first thing", "second thing"]}}


def test_canonical_task_stage_shape():
    t = canonical_task(STAGE_TASK)
    assert t["contract_rev"] == "1"  # record_version: the only revision on v1+v2 reads
    assert t["acceptance_criteria"] == [{"id": "c1", "text": "first thing"},
                                        {"id": "c2", "text": "second thing"}]
    assert t["title"] == "T" and t["record_version"] == 1


def test_canonical_task_v2_id_key():
    t = canonical_task({"task_id": "01ABC", "record_version": 2, "acceptance_criteria": ["x"]})
    assert (t["id"], t["contract_rev"]) == ("01ABC", "2")


def test_canonical_task_ledger_contract_rev_falls_back_to_contract_sha():
    t = canonical_task({"id": 7, "contracts": [{"sha256": "abc123", "body": "{}"}]})
    assert t["contract_rev"] == "abc123"


def test_canonical_task_uses_last_contract():
    t = canonical_task({"id": 7, "contracts": [
        {"sha256": "old", "body": '{"description": "old", "project_id": "oldp", "acceptance_criteria": ["old"]}'},
        {"sha256": "new", "body": '{"description": "new", "project_id": "newp", "acceptance_criteria": ["new"]}'}]})
    assert (t["contract_rev"], t["description"], t["project_id"]) == ("new", "new", "newp")


def test_canonical_task_sha_from_same_contract_as_body():
    t = canonical_task({"id": 7, "contracts": [
        {"sha256": "good", "body": '{"description": "good", "acceptance_criteria": ["g"]}'},
        {"sha256": "bad", "body": "not-json"}]})
    assert (t["contract_rev"], t["description"]) == ("good", "good")


def test_canonical_task_contract_criteria_beat_stale_top_level():
    t = canonical_task({"id": 7, "acceptance_criteria": ["stale"], "contracts": [
        {"sha256": "new", "body": '{"acceptance_criteria": ["fresh"]}'}]})
    assert t["contract_rev"] == "new"
    assert t["acceptance_criteria"] == [{"id": "c1", "text": "fresh"}]


def test_canonical_task_dict_criterion_without_id_gets_positional_id():
    t = canonical_task({"id": 7, "acceptance_criteria": [{"text": "do it"}, {"id": "cx", "text": "x"}]})
    assert t["acceptance_criteria"] == [{"id": "c1", "text": "do it"}, {"id": "cx", "text": "x"}]


def test_canonical_task_contract_without_sha_still_wins_over_stale_top_level():
    t = canonical_task({"id": 7, "acceptance_criteria": ["stale"], "contracts": [
        {"body": '{"description": "new", "acceptance_criteria": ["fresh"]}'}]})
    assert t["description"] == "new"
    assert t["acceptance_criteria"] == [{"id": "c1", "text": "fresh"}]


def test_canonical_task_tolerates_sparse_rows():
    t = canonical_task({"id": 7, "title": "x"})
    assert (t["id"], t["contract_rev"], t["acceptance_criteria"]) == ("7", "", [])


async def test_envelope_unwrap():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"success": True, "data": dict(STAGE_TASK)})

    wh = WorkHubClient("http://wh", "t", "p", "tok", transport=httpx.MockTransport(handler))
    try:
        t = await wh.get_task("01ABC")
    finally:
        await wh.aclose()
    assert seen["path"] == "/api/v2/workhub/tasks/01ABC"
    assert t["contract_rev"] == "1" and len(t["acceptance_criteria"]) == 2


async def test_evidence_normalization():
    rows = [{"id": "e1", "evidence_type": "pr_record",
             "evidence_json": {"evidence_ref": "f1", "url": "u"}}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "data": {"items": rows}})

    wh = WorkHubClient("http://wh", "t", "p", "tok", transport=httpx.MockTransport(handler))
    try:
        out = await wh.list_evidence("01ABC")
    finally:
        await wh.aclose()
    assert out == [{"id": "e1", "type": "pr_record", "fence": "f1", "payload": rows[0]["evidence_json"]}]


def reg(**personas):
    return PersonaRegistry({"tenants": {"t": {"personas": personas}}})


def person(role, principal="p"):
    return {"role": role, "principal": principal, "muse_model": "m", "credential_file": "/tmp/x.json"}


def test_role_pair():
    r = reg(coder=person("coder", "pb"), reviewer=person("reviewer", "pv"))
    b, v = r.role_pair("t")
    assert (b.name, v.name) == ("coder", "reviewer")


def test_role_pair_self_review_and_missing():
    with pytest.raises(RouvrError) as e:
        reg(coder=person("coder", "same"), reviewer=person("reviewer", "same")).role_pair("t")
    assert e.value.code is Code.SELF_REVIEW
    with pytest.raises(RouvrError) as e:
        reg(coder=person("coder")).role_pair("t")
    assert e.value.code is Code.SCOPE


def test_criterion_lines_both_shapes():
    assert criterion_lines([{"id": "c1", "text": "do it"}, "legacy"]) == ["- c1: do it", "- legacy"]
