"""R9: CI mapping, verdict transport, CAS+readback close. Pure logic + fakes, no network."""
import base64
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from rouvr.activities import Activities
from rouvr.errors import Code, RouvrError
from rouvr.gitops import ci_status
from rouvr.muse import build_reviewer_prompt, read_verdict
from rouvr.receipts import SignedReceipt, build_links, verify_receipt


def test_ci_status():
    assert ci_status([]) == "success"  # no CI configured: nothing red
    assert ci_status([{"conclusion": "SUCCESS"}, {"conclusion": "SKIPPED"}]) == "success"
    assert ci_status([{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]) == "failure"
    assert ci_status([{"conclusion": None, "status": "IN_PROGRESS"}]) == "failure"


def test_read_verdict(tmp_path):
    (tmp_path / "verdict.json").write_text('{"verdict": "approve", "notes": "lgtm"}')
    assert read_verdict(tmp_path) == ("approve", "lgtm")
    (tmp_path / "verdict.json").write_text('{"verdict": "maybe"}')
    with pytest.raises(RouvrError) as e:
        read_verdict(tmp_path)
    assert e.value.code is Code.CONTRACT
    (tmp_path / "verdict.json").write_text('not json')
    with pytest.raises(RouvrError):
        read_verdict(tmp_path)
    (tmp_path / "verdict.json").unlink()
    with pytest.raises(RouvrError):
        read_verdict(tmp_path)


def test_reviewer_prompt_carries_criteria_diff_summary():
    p = build_reviewer_prompt({"id": "t", "title": "T", "acceptance_criteria": ["c1"]}, "DIFF", "1/1 pass")
    assert "c1" in p and "DIFF" in p and "1/1 pass" in p and "verdict.json" in p


class FakeWH:
    def __init__(self, states):
        self.states, self.batches = states, []

    async def get_task(self, task_id):
        return self.states.pop(0)

    async def transition_batch(self, items, key_parts):
        self.batches.append((items, key_parts))
        return {}


async def test_close_to_walks_hops_with_cas():
    fake = FakeWH([{"record_version": 3, "status": "queued"}, {"record_version": 4, "status": "running"},
                   {"record_version": 5, "status": "needs_review"}, {"record_version": 6, "status": "done"}])
    acts = Activities(SimpleNamespace(workhub_for=lambda p: fake))
    r = await acts._close_to("t1", "done", "approved on a1", ("close", "t1", "a1"))
    assert (r.final_state, r.record_version) == ("done", 6)
    assert [b[0][0]["status"] for b in fake.batches] == ["running", "needs_review", "done"]
    assert fake.batches[0][0][0]["expected_record_version"] == 3
    assert fake.batches[0][1] == ("close", "t1", "a1", 0)


async def test_close_already_there_is_success():
    fake = FakeWH([{"record_version": 9, "status": "blocked"}])
    acts = Activities(SimpleNamespace(workhub_for=lambda p: fake))
    r = await acts._close_to("t1", "blocked", "r", ("block", "t1", "r"))
    assert (r.final_state, r.record_version) == ("blocked", 9)
    assert fake.batches == []


async def test_close_no_legal_hop_fails():
    fake = FakeWH([{"record_version": 3, "status": "done"}])
    acts = Activities(SimpleNamespace(workhub_for=lambda p: fake))
    with pytest.raises(RouvrError) as e:
        await acts._close_to("t1", "blocked", "r", ("block", "t1", "r"))
    assert e.value.code is Code.EVIDENCE


def _write_key(tmp_path: Path, key: Ed25519PrivateKey | None = None, mode: int = 0o600,
               name: str = "receipt_ed25519.pem") -> tuple[Path, Ed25519PrivateKey]:
    key = key or Ed25519PrivateKey.generate()
    p = tmp_path / name
    p.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    os.chmod(p, mode)
    return p, key


def _acts(key_path: Path, key_id: str = "rouvr-receipt-1") -> Activities:
    settings = SimpleNamespace(receipt_key_path=key_path, receipt_key_id=key_id)
    return Activities(SimpleNamespace(settings=settings))


def _inputs():
    from rouvr.contracts import AssignResult, TaskWorkflowInput, TestResult, VerifyResult
    wf = TaskWorkflowInput("t", "p", "task1", "3", "dev")
    built = AssignResult("att1", 1, "sess1", "b", "https://pr/1", "sha1")
    tests = TestResult("tr1", True, "ok", "ev-t")
    v = VerifyResult("approve", "n", "svc-v", "ev-v")
    return wf, built, tests, v


async def test_sign_receipt_links_attempt_run_session_pr_tests(tmp_path: Path):
    key_path, key = _write_key(tmp_path)
    acts = _acts(key_path)
    wf, built, tests, v = _inputs()
    r1 = await acts.sign_receipt(wf, built, tests, v)
    assert set(r1) == {"digest", "signature", "key_id"}
    assert r1["key_id"] == "rouvr-receipt-1"
    assert len(r1["digest"]) == 64
    # deterministic digest + signature for fixed inputs/key
    assert await acts.sign_receipt(wf, built, tests, v) == r1
    # digest covers every link: changing verify evidence changes digest + signature
    from rouvr.contracts import VerifyResult
    v2 = VerifyResult("approve", "n", "svc-v", "ev-other")
    r2 = await acts.sign_receipt(wf, built, tests, v2)
    assert r2["digest"] != r1["digest"]
    # verifies against the published public key (AC-1)
    links = build_links(task_id="task1", contract_rev="3", attempt_id="att1",
                        muse_session_id="sess1", pr_url="https://pr/1", head_sha="sha1",
                        test_run_id="tr1", test_evidence_id="ev-t", verdict="approve",
                        reviewer="svc-v", verify_evidence_id="ev-v")
    signed = SignedReceipt(version="rouvr-receipt-v1", key_id=r1["key_id"],
                           digest=r1["digest"], signature=r1["signature"], links=links)
    assert verify_receipt(signed, key.public_key()) is True
    # independent check with cryptography only
    key.public_key().verify(base64.b64decode(r1["signature"]), bytes.fromhex(r1["digest"]))


async def test_sign_receipt_wrong_key_and_tamper_fail(tmp_path: Path):
    key_path, key = _write_key(tmp_path)
    acts = _acts(key_path)
    wf, built, tests, v = _inputs()
    r = await acts.sign_receipt(wf, built, tests, v)
    links = build_links(task_id="task1", contract_rev="3", attempt_id="att1",
                        muse_session_id="sess1", pr_url="https://pr/1", head_sha="sha1",
                        test_run_id="tr1", test_evidence_id="ev-t", verdict="approve",
                        reviewer="svc-v", verify_evidence_id="ev-v")
    signed = SignedReceipt(version="rouvr-receipt-v1", key_id=r["key_id"],
                           digest=r["digest"], signature=r["signature"], links=links)
    other = Ed25519PrivateKey.generate()
    assert verify_receipt(signed, other.public_key()) is False
    tampered = SignedReceipt(version=signed.version, key_id=signed.key_id, digest=signed.digest,
                             signature=signed.signature, links=dict(links, verdict="reject"))
    assert verify_receipt(tampered, key.public_key()) is False


async def test_sign_receipt_loose_perms_and_missing_key_fail_closed(tmp_path: Path):
    from temporalio.exceptions import ApplicationError

    loose_path, _ = _write_key(tmp_path, mode=0o644, name="loose.pem")
    acts = _acts(loose_path)
    wf, built, tests, v = _inputs()
    # _wrap converts RouvrError(E_EVIDENCE) to a non-retryable ApplicationError.
    with pytest.raises(ApplicationError) as e:
        await acts.sign_receipt(wf, built, tests, v)
    assert e.value.type == Code.EVIDENCE.value
    assert e.value.non_retryable is True
    missing = _acts(tmp_path / "nope.pem")
    with pytest.raises(ApplicationError) as e2:
        await missing.sign_receipt(wf, built, tests, v)
    assert e2.value.type == Code.EVIDENCE.value
