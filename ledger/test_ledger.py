from fastapi.testclient import TestClient
from ledger.app import make_app


def _client(tmp_path):
    return TestClient(make_app(f"sqlite:///{tmp_path}/l.db"))


def test_done_requires_contract_and_evidence_and_replay_is_idempotent(tmp_path):
    c = _client(tmp_path)
    t = c.post("/v1/tasks", json={"title": "morning report", "intent": "send ops summary"}).json()
    tid = t["id"]
    assert c.post(f"/v1/tasks/{tid}/transition", json={"to": "claimed", "idempotency_key": "k1"}).status_code == 200
    assert c.post(f"/v1/tasks/{tid}/transition", json={"to": "done", "idempotency_key": "k2"}).status_code == 409
    c.post(f"/v1/tasks/{tid}/contracts", json={"checks": ["report_sent"]})
    c.post(f"/v1/tasks/{tid}/evidence", json={"kind": "check", "body": {"report_sent": True}})
    r1 = c.post(f"/v1/tasks/{tid}/transition", json={"to": "done", "idempotency_key": "k3"}).json()
    r2 = c.post(f"/v1/tasks/{tid}/transition", json={"to": "done", "idempotency_key": "k3"}).json()
    assert r1["replayed"] is False and r2["replayed"] is True
    full = c.get(f"/v1/tasks/{tid}").json()
    assert full["state"] == "done" and len(full["receipts"]) == 2


def test_create_with_contract_is_atomic_and_evidence_returns_own_row(tmp_path):
    c = _client(tmp_path)
    t = c.post("/v1/tasks_with_contract",
               json={"title": "x", "intent": "y", "contract": {"checks": ["a"],
                                                                "acceptance_criteria": ["a"]}}).json()
    assert t["state"] == "open" and len(t["contracts"]) == 1
    e1 = c.post(f"/v1/tasks/{t['id']}/evidence", json={"kind": "k1", "body": {"n": 1}}).json()
    e2 = c.post(f"/v1/tasks/{t['id']}/evidence", json={"kind": "k2", "body": {"n": 2}}).json()
    assert (e1["kind"], e2["kind"]) == ("k1", "k2") and e1["id"] != e2["id"]


def test_illegal_transition_rejected(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert c.post(f"/v1/tasks/{tid}/transition", json={"to": "done", "idempotency_key": "a"}).status_code == 409


def _expire_lease(tmp_path):
    import json
    import sqlite3
    past = "2000-01-01T00:00:00+00:00"
    db = sqlite3.connect(tmp_path / "l.db")
    db.execute("UPDATE leases SET expires_at=?", (past,))
    for rid, body in db.execute("SELECT id, body FROM receipts").fetchall():
        def lapse(o):
            if isinstance(o, dict):
                if "expires_at" in o:
                    o["expires_at"] = past
                for v in o.values():
                    lapse(v)
        try:
            obj = json.loads(body)
        except ValueError:
            continue
        lapse(obj)
        db.execute("UPDATE receipts SET body=? WHERE id=?", (json.dumps(obj), rid))
    db.commit(); db.close()


def _claim(c, tid, key, principal="coder-1", ttl=120):
    return c.post(f"/v1/tasks/{tid}/assignments/claim",
                  json={"key": key, "principal": principal, "attempt_no": 1, "ttl_s": ttl})


def test_second_reclaim_cycle_reexecutes(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert _claim(c, tid, "claim-1").status_code == 200
    for cycle in range(2):
        _expire_lease(tmp_path)
        r = c.post(f"/v1/tasks/{tid}/assignments/reclaim", json={"key": "rec-1"})
        assert r.status_code == 200 and r.json()["reclaimed"] is True
        # lease+assignment actually released: a fresh claim succeeds each cycle
        assert _claim(c, tid, f"claim-after-{cycle}").status_code == 200


def test_claim_same_key_other_principal_conflicts(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert _claim(c, tid, "claim-1", "coder-1").status_code == 200
    assert _claim(c, tid, "claim-1", "coder-2").status_code == 409
    assert _claim(c, tid, "claim-1", "coder-1").json()["replayed"] is True  # same principal replays


def test_renew_replay_after_stored_lease_lapses_extends(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    _claim(c, tid, "claim-1")
    body = {"key": "ren-1", "holder": "coder-1", "expected_lease_epoch": 1, "ttl_s": 120}
    assert c.post(f"/v1/tasks/{tid}/leases/renew", json=body).status_code == 200
    _expire_lease(tmp_path)  # stored receipt lease now lapsed (resume-path retry)
    r = c.post(f"/v1/tasks/{tid}/leases/renew", json=body)
    assert r.status_code == 200 and r.json()["replayed"] is False
    assert r.json()["lease"]["expires_at"] > "2000-01-01"
    assert c.post(f"/v1/tasks/{tid}/leases/renew", json=body).json()["replayed"] is True


def test_stale_claim_preserves_original_receipt(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    _claim(c, tid, "claim-1", ttl=0)
    c.post(f"/v1/tasks/{tid}/assignments/reclaim", json={"key": "rec-1"})
    assert _claim(c, tid, "claim-1").status_code == 200  # stale: re-claim, no DELETE
    keys = [r["idempotency_key"] for r in c.get(f"/v1/tasks/{tid}").json()["receipts"]]
    assert "claim-1" in keys and any(k.startswith("claim-1\x1fgen") for k in keys)


def test_claim_replay_after_takeover_conflicts(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert _claim(c, tid, "claim-1", "coder-1").status_code == 200
    _expire_lease(tmp_path)
    assert c.post(f"/v1/tasks/{tid}/assignments/reclaim", json={"key": "rec-1"}).status_code == 200
    r2 = _claim(c, tid, "claim-2", "coder-2")
    assert r2.status_code == 200 and r2.json()["lease_epoch"] == 2  # epoch increments past tombstone
    assert _claim(c, tid, "claim-1", "coder-1").status_code == 409


def test_record_result_requires_live_lease_epoch(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    body = {"key": "res-0", "attempt_id": 1, "lease_epoch": 1, "verdict": "pass", "result": {}}
    assert c.post(f"/v1/tasks/{tid}/results", json=body).status_code == 409  # no lease at all
    _claim(c, tid, "claim-1")
    att = c.post(f"/v1/tasks/{tid}/attempts",
                 json={"key": "att-1", "attempt_no": 1, "lease_epoch": 1}).json()["attempt"]
    bad = {"key": "res-1", "attempt_id": att["id"], "lease_epoch": 999, "verdict": "pass", "result": {}}
    assert c.post(f"/v1/tasks/{tid}/results", json=bad).status_code == 409
    ok = {**bad, "key": "res-2", "lease_epoch": 1}
    assert c.post(f"/v1/tasks/{tid}/results", json=ok).status_code == 200


def test_literal_colon_key_is_not_a_generation(tmp_path):
    c = _client(tmp_path)
    t1 = c.post("/v1/tasks", json={"title": "a", "intent": "y"}).json()["id"]
    t2 = c.post("/v1/tasks", json={"title": "b", "intent": "y"}).json()["id"]
    assert _claim(c, t1, "k1:9", "coder-1").status_code == 200
    r = _claim(c, t2, "k1", "coder-1")
    assert r.status_code == 200 and r.json()["replayed"] is False
    assert r.json()["assignment"]["task_id"] == t2


def test_malformed_numeric_fields_are_422(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert c.post(f"/v1/tasks/{tid}/assignments/claim",
                  json={"key": "k", "principal": "p", "attempt_no": "abc"}).status_code == 422
    assert c.post(f"/v1/tasks/{tid}/attempts",
                  json={"key": "k", "attempt_no": 1, "lease_epoch": "abc"}).status_code == 422
    assert c.post(f"/v1/tasks/{tid}/results",
                  json={"key": "k", "attempt_id": 1, "lease_epoch": "abc", "verdict": "pass"}).status_code == 422
    assert c.post(f"/v1/tasks/{tid}/leases/renew",
                  json={"key": "k", "holder": "p", "expected_lease_epoch": "abc"}).status_code == 422


def test_tasks_with_contract_rejects_empty_contract(tmp_path):
    c = _client(tmp_path)
    for contract in (None, {}, {"acceptance_criteria": []}, {"acceptance_criteria": "just do it"},
                     {"acceptance_criteria": [""]}, {"acceptance_criteria": [123]}):
        body = {"title": "x", "intent": "y", **({"contract": contract} if contract is not None else {})}
        assert c.post("/v1/tasks_with_contract", json=body).status_code == 422
    assert c.get("/v1/tasks").json()["items"] == []  # no orphan task stored


def test_stale_claim_conflicts_when_other_assignment_active(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert _claim(c, tid, "claim-1").status_code == 200
    _expire_lease(tmp_path)
    assert c.post(f"/v1/tasks/{tid}/assignments/reclaim", json={"key": "rec-1"}).status_code == 200
    r2 = c.post(f"/v1/tasks/{tid}/assignments/claim",
                json={"key": "claim-2", "principal": "coder-1", "attempt_no": 2})
    assert r2.status_code == 200
    assert _claim(c, tid, "claim-1").status_code == 409
    assert c.get(f"/v1/tasks/{tid}/binding").json()["assignment"]["attempt_no"] == 2


def test_renew_after_reclaim_conflicts_and_replay_validates_holder(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    _claim(c, tid, "claim-1")
    body = {"key": "ren-1", "holder": "coder-1", "expected_lease_epoch": 1, "ttl_s": 120}
    assert c.post(f"/v1/tasks/{tid}/leases/renew", json=body).status_code == 200
    bad = {**body, "holder": "EVIL", "expected_lease_epoch": 999}
    assert c.post(f"/v1/tasks/{tid}/leases/renew", json=bad).status_code == 409
    _expire_lease(tmp_path)
    assert c.post(f"/v1/tasks/{tid}/assignments/reclaim", json={"key": "rec-1"}).status_code == 200
    assert c.post(f"/v1/tasks/{tid}/leases/renew", json=body).status_code == 409
    r = c.post(f"/v1/tasks/{tid}/assignments/claim",
               json={"key": "claim-2", "principal": "coder-1", "attempt_no": 2})
    assert r.status_code == 200  # failed renew did not resurrect/block the lease


def test_record_result_rejects_foreign_unknown_and_wrong_epoch_attempts(tmp_path):
    c = _client(tmp_path)
    t1 = c.post("/v1/tasks", json={"title": "a", "intent": "y"}).json()["id"]
    t2 = c.post("/v1/tasks", json={"title": "b", "intent": "y"}).json()["id"]
    _claim(c, t1, "claim-1"); _claim(c, t2, "claim-2")
    a1 = c.post(f"/v1/tasks/{t1}/attempts", json={"key": "att-1", "attempt_no": 1, "lease_epoch": 1}).json()["attempt"]
    def res(tid, key, aid, epoch):
        return c.post(f"/v1/tasks/{tid}/results",
                      json={"key": key, "attempt_id": aid, "lease_epoch": epoch, "verdict": "pass", "result": {}})
    assert res(t2, "res-foreign", a1["id"], 1).status_code == 409
    assert res(t2, "res-unknown", 9999, 1).status_code == 409
    # epoch-1 attempt must not accept an epoch-2 result after reclaim+reclaim
    _expire_lease(tmp_path)
    c.post(f"/v1/tasks/{t1}/assignments/reclaim", json={"key": "rec-1"})
    c.post(f"/v1/tasks/{t1}/assignments/claim", json={"key": "claim-1b", "principal": "coder-1", "attempt_no": 2})
    c.post(f"/v1/tasks/{t1}/attempts", json={"key": "att-2", "attempt_no": 2, "lease_epoch": 2})
    assert res(t1, "res-old-attempt", a1["id"], 2).status_code == 409


def test_float_fields_and_mixed_criteria_are_422(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert c.post(f"/v1/tasks/{tid}/assignments/claim",
                  json={"key": "k", "principal": "p", "attempt_no": 1.9}).status_code == 422
    assert c.post("/v1/tasks_with_contract",
                  json={"title": "x", "intent": "y",
                        "contract": {"acceptance_criteria": ["ok", 123]}}).status_code == 422


def test_same_key_replays_per_task_not_globally(tmp_path):
    c = _client(tmp_path)
    t1 = c.post("/v1/tasks", json={"title": "a", "intent": "y"}).json()["id"]
    t2 = c.post("/v1/tasks", json={"title": "b", "intent": "y"}).json()["id"]
    assert _claim(c, t1, "shared", "coder-1").status_code == 200
    r = _claim(c, t2, "shared", "coder-1")
    assert r.status_code == 200 and r.json()["replayed"] is False  # not t1's receipt
    assert r.json()["assignment"]["task_id"] == t2
    assert _claim(c, t1, "shared", "coder-1").json()["replayed"] is True
    assert _claim(c, t2, "shared", "coder-1").json()["assignment"]["task_id"] == t2


def test_generation_shaped_key_on_other_task_does_not_poison(tmp_path):
    c = _client(tmp_path)
    t1 = c.post("/v1/tasks", json={"title": "a", "intent": "y"}).json()["id"]
    t2 = c.post("/v1/tasks", json={"title": "b", "intent": "y"}).json()["id"]
    assert _claim(c, t1, "K::gen1", "coder-1").status_code == 200
    r = _claim(c, t2, "K", "coder-1")
    assert r.status_code == 200 and r.json()["replayed"] is False
    assert r.json()["assignment"]["task_id"] == t2


def test_claim_replay_with_other_attempt_no_conflicts(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert _claim(c, tid, "claim-1").status_code == 200
    r = c.post(f"/v1/tasks/{tid}/assignments/claim",
               json={"key": "claim-1", "principal": "coder-1", "attempt_no": 2})
    assert r.status_code == 409  # not attempt 1's assignment dressed as a replay
    assert _claim(c, tid, "claim-1").json()["replayed"] is True


def test_expired_lease_blocks_new_attempt_and_result(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert _claim(c, tid, "claim-1").status_code == 200
    att = c.post(f"/v1/tasks/{tid}/attempts",
                 json={"key": "att-1", "attempt_no": 1, "lease_epoch": 1}).json()["attempt"]
    _expire_lease(tmp_path)  # epoch still matches; only expiry can block below
    assert c.post(f"/v1/tasks/{tid}/attempts",
                  json={"key": "att-2", "attempt_no": 2, "lease_epoch": 1}).status_code == 409
    assert c.post(f"/v1/tasks/{tid}/results",
                  json={"key": "res-1", "attempt_id": att["id"], "lease_epoch": 1,
                        "verdict": "pass", "result": {}}).status_code == 409


def test_int_fields_reject_numeric_strings_and_bad_ttls(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]

    def claim(**over):
        body = {"key": "k", "principal": "p", "attempt_no": 1, "ttl_s": 120}
        body.update(over)
        return c.post(f"/v1/tasks/{tid}/assignments/claim", json=body)

    assert claim(attempt_no="1_0").status_code == 422  # int("1_0") == 10 must not slip through
    assert claim(attempt_no="1").status_code == 422
    assert claim(ttl_s="120").status_code == 422
    assert claim(ttl_s=-1).status_code == 422
    assert claim(ttl_s=86401).status_code == 422
    assert claim(key="k-max", ttl_s=86400).status_code == 200  # top of range is valid


def test_postgres_url_fails_fast():
    import pytest
    with pytest.raises(ValueError, match="SQLite only"):
        make_app("postgresql+psycopg://u:p@127.0.0.1:1/db")


def test_resumed_attempt_after_reclaim_records_result_at_new_epoch(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert _claim(c, tid, "claim-1").status_code == 200
    att = c.post(f"/v1/tasks/{tid}/attempts",
                 json={"key": "att-1", "attempt_no": 1, "lease_epoch": 1}).json()["attempt"]
    _expire_lease(tmp_path)
    assert c.post(f"/v1/tasks/{tid}/assignments/reclaim", json={"key": "rec-1"}).status_code == 200
    assert _claim(c, tid, "claim-2").json()["lease_epoch"] == 2
    att2 = c.post(f"/v1/tasks/{tid}/attempts",
                  json={"key": "att-1", "attempt_no": 1, "lease_epoch": 2}).json()["attempt"]
    assert att2["id"] == att["id"] and att2["lease_epoch"] == 2
    r = c.post(f"/v1/tasks/{tid}/results",
               json={"key": "res-1", "attempt_id": att["id"], "lease_epoch": 2,
                     "verdict": "pass", "result": {}})
    assert r.status_code == 200


def test_literal_gen_key_same_task_is_not_a_generation(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert _claim(c, tid, "K::gen1").status_code == 200
    r = c.post(f"/v1/tasks/{tid}/assignments/claim",
               json={"key": "K", "principal": "coder-1", "attempt_no": 2})
    assert r.status_code == 200 and r.json()["replayed"] is False
    assert r.json()["assignment"]["attempt_no"] == 2


def test_claim_replay_with_expired_stored_lease_reclaims_with_new_ttl(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert _claim(c, tid, "claim-1", ttl=120).status_code == 200
    _expire_lease(tmp_path)
    r = _claim(c, tid, "claim-1", ttl=120)
    assert r.status_code == 200 and r.json()["replayed"] is False
    assert r.json()["lease"]["expires_at"] > "2000-01-01"


def test_transition_replay_with_different_to_conflicts(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert c.post(f"/v1/tasks/{tid}/transition",
                  json={"to": "claimed", "idempotency_key": "t1"}).status_code == 200
    r = c.post(f"/v1/tasks/{tid}/transition", json={"to": "done", "idempotency_key": "t1"})
    assert r.status_code == 409
    assert c.get(f"/v1/tasks/{tid}").json()["state"] == "claimed"


def test_claim_replay_uses_live_lease_not_stored_snapshot(tmp_path):
    import json
    import sqlite3
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert _claim(c, tid, "claim-1").status_code == 200
    att = c.post(f"/v1/tasks/{tid}/attempts",
                 json={"key": "att-1", "attempt_no": 1, "lease_epoch": 1}).json()["attempt"]
    db = sqlite3.connect(tmp_path / "l.db")  # expire only the stored snapshot; live lease stays live
    for rid, body in db.execute("SELECT id, body FROM receipts").fetchall():
        obj = json.loads(body)
        def lapse(o):
            if isinstance(o, dict):
                if "expires_at" in o:
                    o["expires_at"] = "2000-01-01T00:00:00+00:00"
                for v in o.values():
                    lapse(v)
        lapse(obj)
        db.execute("UPDATE receipts SET body=? WHERE id=?", (json.dumps(obj), rid))
    db.commit(); db.close()
    r = _claim(c, tid, "claim-1")
    assert r.status_code == 200 and r.json()["replayed"] is True and r.json()["lease_epoch"] == 1
    assert c.post(f"/v1/tasks/{tid}/results",
                  json={"key": "res-1", "attempt_id": att["id"], "lease_epoch": 1,
                        "verdict": "pass", "result": {}}).status_code == 200


def test_literal_gen_key_after_generation_stored_is_fresh(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    assert _claim(c, tid, "K").status_code == 200
    _expire_lease(tmp_path)
    assert _claim(c, tid, "K").status_code == 200  # stale re-claim stores K\x1fgen1
    r = c.post(f"/v1/tasks/{tid}/assignments/claim",
               json={"key": "K::gen1", "principal": "coder-1", "attempt_no": 2})
    assert r.status_code == 200 and r.json()["replayed"] is False
    assert r.json()["assignment"]["attempt_no"] == 2


def test_preexisting_global_unique_receipts_db_is_migrated(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "l.db")
    db.execute("CREATE TABLE receipts (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL,"
               " idempotency_key TEXT NOT NULL UNIQUE, action TEXT NOT NULL,"
               " body TEXT NOT NULL, created_at TEXT NOT NULL)")
    db.execute("INSERT INTO receipts VALUES (1, 999, 'old', 'claim', '{}', '2024-01-01')")
    db.commit(); db.close()
    c = _client(tmp_path)
    t1 = c.post("/v1/tasks", json={"title": "a", "intent": "y"}).json()["id"]
    t2 = c.post("/v1/tasks", json={"title": "b", "intent": "y"}).json()["id"]
    assert _claim(c, t1, "shared", "coder-1").status_code == 200
    r = _claim(c, t2, "shared", "coder-1")
    assert r.status_code == 200 and r.json()["replayed"] is False
    rows = c.get(f"/v1/tasks/{t1}").json()["receipts"]
    assert any(x["idempotency_key"] == "shared" for x in rows)


def test_claim_moves_task_to_claimed_and_review_cycle(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/v1/tasks", json={"title": "x", "intent": "y"}).json()["id"]
    r = c.post(f"/v1/tasks/{tid}/assignments/claim",
               json={"key": "claim-1", "principal": "svc-b", "attempt_no": 1})
    assert r.status_code == 200
    assert c.get(f"/v1/tasks/{tid}").json()["state"] == "claimed"
    assert c.post(f"/v1/tasks/{tid}/transition",
                  json={"to": "needs_review", "idempotency_key": "nr1"}).status_code == 200
    assert c.get(f"/v1/tasks/{tid}").json()["state"] == "needs_review"
    # rework: claiming the next attempt returns the task to claimed
    r = c.post(f"/v1/tasks/{tid}/assignments/claim",
               json={"key": "claim-2", "principal": "svc-b", "attempt_no": 2})
    assert r.status_code == 200
    assert c.get(f"/v1/tasks/{tid}").json()["state"] == "claimed"
