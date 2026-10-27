"""Wave 5 operator layer: browser allowlist, promotion ledger, AgentShield rules."""

from __future__ import annotations

import pytest

from backend.aix_core.runtime.boundaries.browser import (
    browser_allowlist_from_settings,
    browser_url_allowed,
)
from backend.aix_core.runtime.executor.memory_promotion_ledger import (
    PromotionRejected,
    append_promotion_record,
    deprecate_memory,
    promote_memory,
    propose_memory,
    replay_promotion_state,
)
from backend.codeintel_v2.agent_shield import RULES, rule_ids, scan_diff, scan_text


def test_browser_deny_by_default() -> None:
    assert browser_url_allowed("https://example.com/x", allowlist=[]) is False
    assert browser_url_allowed("https://example.com/x", allowlist=None) is False
    assert browser_allowlist_from_settings() == []


def test_browser_allowlist_matching() -> None:
    allow = ["https://docs.internal.example/*"]
    assert browser_url_allowed("https://docs.internal.example/a/b", allowlist=allow) is True
    assert browser_url_allowed("https://evil.example/a", allowlist=allow) is False
    assert browser_url_allowed("https://docs.internal.example.evil.com/", allowlist=allow) is False
    assert browser_url_allowed("not a url", allowlist=allow) is False


def test_promotion_requires_evidence_and_lane_approval(tmp_path) -> None:
    with pytest.raises(PromotionRejected):
        propose_memory(entry_id="m1", tenant_id="t", evidence_refs=[])
    record = propose_memory(entry_id="m1", tenant_id="t", evidence_refs=["evidence/x.md"])
    assert record.state == "proposed"
    with pytest.raises(PromotionRejected):
        promote_memory(record=record, lane_approval_id="")
    accepted = promote_memory(record=record, lane_approval_id="lane-1")
    assert accepted.state == "accepted"
    assert record.state == "proposed"  # input untouched
    with pytest.raises(PromotionRejected):
        promote_memory(record=accepted, lane_approval_id="lane-2")  # only proposed promotes
    log = tmp_path / "promo.jsonl"
    append_promotion_record(log_path=log, record=record)
    append_promotion_record(log_path=log, record=accepted)
    retired = deprecate_memory(record=accepted, reason="superseded")
    append_promotion_record(log_path=log, record=retired)
    state = replay_promotion_state(log_path=log)
    assert state["m1"].state == "deprecated"
    with pytest.raises(PromotionRejected):
        deprecate_memory(record=accepted, reason="")


def test_shield_rule_count_and_unique_ids() -> None:
    assert 20 <= len(RULES) <= 40
    assert len(set(rule_ids())) == len(RULES)
    assert all(rule.severity in {"P0", "P1"} for rule in RULES)


def test_shield_fires_on_authority_leak_specimens() -> None:
    findings = scan_text("target_file = target_file or anchor.get(\"file_path\")\n")
    assert any(f.rule_id == "SH-INF-05" for f in findings)
    findings = scan_text("for f in [target, *adjacent_files]:\n")
    assert any(f.rule_id == "SH-SCP-01" for f in findings)
    findings = scan_text("heartbeat_timeout_seconds=30\n")
    assert any(f.rule_id == "SH-DUR-01" for f in findings)
    findings = scan_text("x = 1  # innocent line\n")
    assert findings == []


def test_shield_scans_diff_additions_only() -> None:
    diff = (
        "--- a/f.py\n+++ b/f.py\n"
        "@@ -1 +1 @@\n"
        "-heartbeat_timeout_seconds=30\n"
        "+heartbeat_timeout_seconds=None\n"
        "+target_file = target_file or anchor.get(\"file_path\")\n"
    )
    hits = {f.rule_id for f in scan_diff(diff)}
    assert "SH-DUR-01" not in hits  # removed line ignored
    assert "SH-INF-05" in hits  # added line flagged
