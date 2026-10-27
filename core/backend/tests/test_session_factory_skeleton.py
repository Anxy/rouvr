"""Skeleton tests: capsule pure builders only (no Redis, no DB)."""

from backend.session_factory.capsule import (
    CAPSULE_TTL_SECONDS,
    RECENT_LIST_MAX,
    build_capsule,
    capsule_key,
    recent_list_key,
)


def test_key_layout() -> None:
    assert capsule_key("env-abc") == "aix:session:env-abc"
    assert (
        recent_list_key("hyeve-lane-worker-aix")
        == "aix:sessions:recent:hyeve-lane-worker-aix"
    )
    assert CAPSULE_TTL_SECONDS == 30 * 24 * 3600
    assert RECENT_LIST_MAX == 3


def test_build_capsule_pointers_not_copies() -> None:
    cap = build_capsule(
        session_id="env-1",
        tenant_id="t",
        intent="demo",
        lane_checkpoint="1296a6a",
        lane_tag="checkpoint-wave-14",
        codeintel_artifacts=["art-1"],
        workhub_mandate_ids=["01M25X"],
        persona_bindings=["worker-aix"],
        guardrails_hash="g",
    )
    assert cap["capsule_version"] == 1
    assert cap["codeintel_snapshot"]["artifacts"] == ["art-1"]
    assert cap["workhub_snapshot"]["open_mandate_ids"] == ["01M25X"]
    assert cap["persona_bindings"] == ["worker-aix"]
    assert "created_at" in cap


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    def set(self, k: str, v: str, ex: int | None = None) -> None:
        assert ex == CAPSULE_TTL_SECONDS
        self.kv[k] = v

    def get(self, k: str) -> str | None:
        return self.kv.get(k)

    def lpush(self, k: str, v: str) -> None:
        self.lists.setdefault(k, []).insert(0, v)

    def ltrim(self, k: str, a: int, b: int) -> None:
        self.lists[k] = self.lists.get(k, [])[a : b + 1]

    def lrange(self, k: str, a: int, b: int) -> list[str]:
        return self.lists.get(k, [])[a or 0 :]


def test_write_read_recent_roundtrip() -> None:
    from backend.session_factory.capsule import (
        read_capsule,
        recent_sessions,
        write_capsule,
    )

    r = _FakeRedis()
    for sid in ("env-1", "env-2", "env-3", "env-4"):
        write_capsule(
            r,
            build_capsule(
                session_id=sid,
                tenant_id="t",
                intent="i",
                lane_checkpoint="c",
                lane_tag="g",
            ),
        )
    assert recent_sessions(r, "t") == ["env-4", "env-3", "env-2"]
    assert read_capsule(r, "env-4")["session_id"] == "env-4"
    assert read_capsule(r, "missing") is None
