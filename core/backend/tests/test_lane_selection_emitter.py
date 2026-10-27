from __future__ import annotations

import pytest

from backend.services.lane_selection_emitter import (
    FORBIDDEN_TOKENS,
    build_mandate,
    idempotency_key,
    validate_selection,
)


def _unit(**overrides):
    base = {
        "lane_class": "docs-registration",
        "title": "Register worker-ops runbook",
        "scope": "Add the runbook page under docs/ops",
        "acceptance": "Page renders and links from the index",
        "max_attempts": 2,
    }
    base.update(overrides)
    return base


def test_valid_selection_passes():
    verdict = validate_selection(_unit())
    assert verdict == {"ok": True, "lane_class": "docs-registration", "reasons": []}


def test_build_mandate_payload_schema_keys():
    out = build_mandate(_unit(), correlation_id="corr-123")
    assert set(out) == {"mandate", "governor_selection"}
    assert set(out["mandate"]) == {
        "project",
        "kind",
        "code",
        "title",
        "lane_class",
        "scope",
        "acceptance",
        "max_attempts",
        "correlation_id",
        "idempotency_key",
    }
    assert out["mandate"]["project"] == "lane-ops"
    assert out["mandate"]["kind"] == "AUDIT"
    assert out["mandate"]["code"] == "code"
    assert set(out["governor_selection"]) == {
        "policy",
        "lane_class",
        "decision",
        "bounds",
        "correlation_id",
    }
    assert out["governor_selection"]["policy"] == "lanes/aix-selection-policy.md"
    assert out["governor_selection"]["bounds"]["max_attempts"] == 3


@pytest.mark.parametrize("token", list(FORBIDDEN_TOKENS))
def test_each_forbidden_token_refused(token):
    verdict = validate_selection(_unit(scope=f"Please {token} the cluster now"))
    assert verdict["ok"] is False
    assert any(token in reason for reason in verdict["reasons"])
    with pytest.raises(ValueError):
        build_mandate(_unit(scope=f"Please {token} the cluster now"), correlation_id="c1")


def test_missing_scope_refused():
    verdict = validate_selection(_unit(scope=""))
    assert verdict["ok"] is False
    assert any("scope" in reason for reason in verdict["reasons"])


def test_service_proper_names_not_refused():
    # Live false positive (packreg-config-roots-01): repo proper names and
    # SERVICE_* identifiers are not service changes.
    verdict = validate_selection(_unit(
        scope="hyeve-pack-registry-service plan_release_rollback.py "
              "DEFAULT_SERVICE_ROOT/DEFAULT_PLATFORM_ROOT bare paths"))
    assert verdict["ok"] is True, verdict["reasons"]


@pytest.mark.parametrize("phrase", ["service change", "restart the runtime",
                                    "runtime fix", "production mutation"])
def test_action_phrases_still_refused(phrase):
    verdict = validate_selection(_unit(scope=f"Please {phrase} now"))
    assert verdict["ok"] is False


def test_idempotency_determinism():
    out_a = build_mandate(_unit(), correlation_id="corr-xyz")
    out_b = build_mandate(_unit(), correlation_id="corr-xyz")
    out_c = build_mandate(_unit(), correlation_id="corr-other")
    assert out_a["mandate"]["idempotency_key"] == out_b["mandate"]["idempotency_key"]
    assert out_a["mandate"]["idempotency_key"] != out_c["mandate"]["idempotency_key"]
    assert idempotency_key("corr-xyz") == out_a["mandate"]["idempotency_key"]
