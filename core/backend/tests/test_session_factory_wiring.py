"""Wiring tests: Depends-injected fakes, hermetic (no Redis, no DB).

Proves the authentic failure first: without ``configure_*`` every
route fails closed 501. Then with fakes: session create writes capsule
+ inserts the run, hydrate round-trips, 401/403/404 hold.
"""

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers.session_factory_router import router
from backend.session_factory.capsule import (
    CAPSULE_TTL_SECONDS,
    build_run_record,
)
from backend.session_factory.stores import (
    configure_redis,
    configure_run_store,
)


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, tuple[str, int | None]] = {}
        self.lists: dict[str, list[str]] = {}

    def set(self, k: str, v: str, ex: int | None = None) -> None:
        self.kv[k] = (v, ex)

    def get(self, k: str) -> str | None:
        return self.kv[k][0] if k in self.kv else None

    def lpush(self, k: str, v: str) -> None:
        self.lists.setdefault(k, []).insert(0, v)

    def ltrim(self, k: str, a: int, b: int) -> None:
        self.lists[k] = self.lists.get(k, [])[a : b + 1]

    def lrange(self, k: str, a: int, b: int) -> list[str]:
        return self.lists.get(k, [])[a or 0 :]


class FakeStore:
    def __init__(self, fail: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail = fail

    def insert_run(self, record: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.rows.append(record)


AUTH = {"Authorization": "Bearer test"}


def _bare_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _wired_app(store: FakeStore | None = None) -> tuple[FastAPI, FakeRedis, FakeStore]:
    app = _bare_app()
    redis = FakeRedis()
    real_store = store or FakeStore()
    configure_redis(app, redis)
    configure_run_store(app, real_store)
    return app, redis, real_store


def test_unwired_fails_closed_501() -> None:
    client = TestClient(_bare_app())
    body = {"tenant_id": "t", "intent": "i"}
    assert client.post("/v1/aix/sessions", json=body, headers=AUTH).status_code == 501
    assert client.post("/v1/aix/intent", json={"tenant_id": "t", "intent_text": "i"}, headers=AUTH).status_code == 501
    assert client.post("/v1/aix/sessions/x/hydrate", headers=AUTH).status_code == 501


def test_create_session_writes_capsule_and_run() -> None:
    app, redis, store = _wired_app()
    client = TestClient(app)
    r = client.post(
        "/v1/aix/sessions",
        json={"tenant_id": "t", "intent": "demo"},
        headers=AUTH,
    )
    assert r.status_code == 201
    envelope_id = r.json()["envelope_id"]
    assert redis.kv[f"aix:session:{envelope_id}"][1] == CAPSULE_TTL_SECONDS
    assert len(store.rows) == 1
    row = store.rows[0]
    assert row["run_id"] == row["objective_id"] == envelope_id
    assert row["phase"] == "STARTING"
    assert set(row) >= {"tenant_id", "trace_id", "payload"}


def test_store_failure_is_502_and_writes_nothing() -> None:
    app, redis, _ = _wired_app(FakeStore(fail=True))
    client = TestClient(app)
    r = client.post(
        "/v1/aix/sessions", json={"tenant_id": "t", "intent": "i"}, headers=AUTH
    )
    assert r.status_code == 502
    assert redis.kv == {}


def test_hydrate_roundtrip_and_guards() -> None:
    app, _, _ = _wired_app()
    client = TestClient(app)
    created = client.post(
        "/v1/aix/sessions", json={"tenant_id": "t", "intent": "i"}, headers=AUTH
    )
    envelope_id = created.json()["envelope_id"]
    ok = client.post(f"/v1/aix/sessions/{envelope_id}/hydrate", headers=AUTH)
    assert ok.status_code == 200
    assert ok.json()["capsule"]["session_id"] == envelope_id
    assert ok.json()["recent"] == [envelope_id]
    assert client.post("/v1/aix/sessions/nope/hydrate", headers=AUTH).status_code == 404
    mismatch = client.post(
        f"/v1/aix/sessions/{envelope_id}/hydrate?tenant_id=other", headers=AUTH
    )
    assert mismatch.status_code == 403
    assert client.post(f"/v1/aix/sessions/{envelope_id}/hydrate").status_code == 401


def test_recent_lists_capsules_and_guards() -> None:
    app, redis, _ = _wired_app()
    client = TestClient(app)
    ids = [
        client.post(
            "/v1/aix/sessions", json={"tenant_id": "t", "intent": f"i{i}"}, headers=AUTH
        ).json()["envelope_id"]
        for i in range(2)
    ]
    r = client.get("/v1/aix/sessions/recent?tenant_id=t", headers=AUTH)
    assert r.status_code == 200
    got = [s["session_id"] for s in r.json()["sessions"]]
    assert got == ids[::-1]
    assert all(s["capsule"] is not None for s in r.json()["sessions"])
    assert client.get("/v1/aix/sessions/recent", headers=AUTH).status_code == 422
    assert client.get("/v1/aix/sessions/recent?tenant_id=t").status_code == 401


def test_recent_shows_null_for_expired_capsule() -> None:
    app, redis, _ = _wired_app()
    client = TestClient(app)
    envelope_id = client.post(
        "/v1/aix/sessions", json={"tenant_id": "t", "intent": "i"}, headers=AUTH
    ).json()["envelope_id"]
    del redis.kv[f"aix:session:{envelope_id}"]  # TTL expiry simulation
    r = client.get("/v1/aix/sessions/recent?tenant_id=t", headers=AUTH)
    entry = r.json()["sessions"][0]
    assert entry["session_id"] == envelope_id
    assert entry["capsule"] is None


def test_hydrate_resolves_when_fetchers_configured() -> None:
    from backend.routers import session_factory_router as sfr

    async def ci(project_id, kind):
        assert project_id == "p"
        return [{"id": "a1", "artifact_kind": kind, "title": "t",
                 "tenant_id": "t"}] if kind == "context_pack_document" else []

    async def wh():
        return [{"id": "m1", "tenant_id": "t"}]

    sfr.configure_hydration_fetchers(codeintel=ci, workhub=wh)
    try:
        app, _, _ = _wired_app()
        client = TestClient(app)
        created = client.post(
            "/v1/aix/sessions", json={"tenant_id": "t", "intent": "i"},
            headers=AUTH,
        )
        envelope_id = created.json()["envelope_id"]
        r = client.post(
            f"/v1/aix/sessions/{envelope_id}/hydrate?project_id=p",
            headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["resolved"] == {
            "codeintel": [{"id": "a1", "kind": "context_pack_document",
                           "title": "t"}],
            "workhub": ["m1"],
        }
    finally:
        sfr.reset_hydration_fetchers()


def test_run_record_matches_not_null_columns() -> None:
    rec = build_run_record(
        envelope_id="env-1", tenant_id="t", intent="i", trace_id="tr-1"
    )
    for col in ("tenant_id", "run_id", "objective_id", "phase", "trace_id", "payload"):
        assert rec[col]
