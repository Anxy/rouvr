import importlib.util
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOOLS = Path(__file__).parents[1] / "tools"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PACKET = _load("waker_packet")
CREDS = _load("waker_creds")

NOW = datetime(2026, 9, 29, 8, 0, 0, tzinfo=timezone.utc)

DEV_IDENTITY = "lane-ops-334-dev"  # test placeholder, not a real identity


def _fields(**over):
    base = {
        "schema": "waker-dispatch-v1",
        "task_number": 335,
        "worktree": "/tmp/waker-test",
        "pin_commit": "abc123",
        "issued_at": "2026-09-29T07:59:00Z",
    }
    base.update(over)
    return base


# --- packet-assembler: digest round-trip + tamper rejection ---

def test_digest_round_trip():
    packet = PACKET.assemble(_fields())
    ok, reason = PACKET.verify(packet)
    assert ok, reason


def test_digest_stable_canonical_form():
    a = PACKET.assemble({"b": 1, "a": 2})
    b = PACKET.assemble({"a": 2, "b": 1})
    assert a[PACKET.DIGEST_FIELD] == b[PACKET.DIGEST_FIELD]


def test_tamper_rejected():
    packet = PACKET.assemble(_fields())
    packet["task_number"] = 999
    ok, reason = PACKET.verify(packet)
    assert not ok and reason == "digest-mismatch"


def test_missing_and_non_dict_digest_rejected():
    assert PACKET.verify({}) == (False, "missing-digest")
    assert PACKET.verify({"payload_digest": ""}) == (False, "missing-digest")
    assert PACKET.verify([1, 2]) == (False, "not-a-dict")
    assert PACKET.verify(None) == (False, "not-a-dict")


def test_assemble_recomputes_untrusted_digest():
    packet = PACKET.assemble({**_fields(), "payload_digest": "forged"})
    ok, _ = PACKET.verify(packet)
    assert ok and packet["payload_digest"] != "forged"


# --- packet freshness / binding ---

def test_fresh_bound_packet_accepted():
    packet = PACKET.assemble(_fields())
    ok, reason = PACKET.verify_before_use(
        packet, expected_binding={"task_number": 335}, now=NOW)
    assert ok, reason


def test_stale_packet_refused():
    packet = PACKET.assemble(_fields(issued_at="2026-09-29T05:00:00Z"))
    ok, reason = PACKET.verify_before_use(packet, now=NOW, max_age_s=3600)
    assert not ok and reason == "stale-packet"


def test_future_packet_refused():
    packet = PACKET.assemble(_fields(issued_at="2026-09-29T09:00:00Z"))
    ok, reason = PACKET.verify_before_use(packet, now=NOW)
    assert not ok and reason == "issued-in-future"


def test_missing_issued_at_refused():
    fields = _fields()
    del fields["issued_at"]
    packet = PACKET.assemble(fields)
    ok, reason = PACKET.verify_before_use(packet, now=NOW)
    assert not ok and reason == "missing-issued-at"


def test_binding_mismatch_refused():
    packet = PACKET.assemble(_fields())
    ok, reason = PACKET.verify_before_use(
        packet, expected_binding={"pin_commit": "deadbee"}, now=NOW)
    assert not ok and reason == "binding-mismatch:pin_commit"


def test_tampered_packet_fails_verify_before_use():
    packet = PACKET.assemble(_fields())
    packet["pin_commit"] = "deadbee"
    ok, _ = PACKET.verify_before_use(packet, now=NOW)
    assert not ok


# --- credential broker: allow-only dev identity, fail closed ---

def test_broker_resolves_allowlisted_identity():
    broker = CREDS.CredentialBroker(DEV_IDENTITY)
    assert broker.is_configured()
    assert broker.resolve(DEV_IDENTITY, lambda: "in-memory-secret") == \
        "in-memory-secret"


def test_broker_refuses_other_identities():
    broker = CREDS.CredentialBroker(DEV_IDENTITY)
    for other in ["lane-ops-335-dev", "prod", "admin", "", None, 123]:
        with pytest.raises(CREDS.CredentialRefused):
            broker.resolve(other, lambda: "in-memory-secret")


def test_broker_unconfigured_denies_all():
    broker = CREDS.CredentialBroker()
    assert not broker.is_configured()
    with pytest.raises(CREDS.CredentialRefused):
        broker.resolve(DEV_IDENTITY, lambda: "in-memory-secret")
    with pytest.raises(CREDS.CredentialRefused):
        broker.resolve("anything", lambda: "in-memory-secret")


def test_broker_provider_called_only_on_allow_path():
    broker = CREDS.CredentialBroker(DEV_IDENTITY)
    calls = []
    broker.resolve(DEV_IDENTITY, lambda: calls.append(1) or "s")
    assert calls == [1]
    with pytest.raises(CREDS.CredentialRefused):
        broker.resolve("intruder", lambda: calls.append(1) or "s")
    assert calls == [1]  # provider never invoked on the refuse path


def test_broker_non_callable_provider_refused():
    broker = CREDS.CredentialBroker(DEV_IDENTITY)
    with pytest.raises(CREDS.CredentialRefused):
        broker.resolve(DEV_IDENTITY, "not-a-provider")


def test_broker_repr_leaks_nothing():
    text = repr(CREDS.CredentialBroker(DEV_IDENTITY))
    assert DEV_IDENTITY not in text
