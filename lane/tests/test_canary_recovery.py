import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1] / "tools"


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C, R = load("canary_continuation"), load("canary_recovery")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    cfg = {"wh_base": "wh", "auth_base": "auth", "project": "test"}
    halt = "verifier-material-missing:reconciliation-readback.json"
    lease = {
        "task_id": "task",
        "task_number": 10,
        "attempt_id": "local",
        "workhub_attempt_id": "attempt",
        "assignment_id": "assignment",
        "result_filed": True,
        "worker_pid": 42,
        "worker_ticks": "123",
        "formal_result_id": "result",
        "canonical_review_pending": {
            "halted": halt,
            "task": {"id": "task"},
            "attempt_id": "local",
            "route": {},
            "cfg": cfg,
            "operations": {},
        },
    }
    slot = {
        "completed": [],
        "halted": "canonical-review-pending:" + halt,
        "slot": {"root_task_id": "task", "phase": "await-parent", "operations": {}},
    }
    lp = tmp_path / "lease.json"
    C.persist(lp, lease)
    C.persist(tmp_path / "continuous-slot.json", slot)
    task = {"id": "task", "task_number": 10, "status": "done", "record_version": 20}
    binding = {
        "http": 404,
        "body": {"detail": {"code": "assignment_binding_not_found"}},
    }
    rows = []

    def evidence(cfg, jwt, tid, etype, ref, payload, key):
        row = {
            "id": "evidence",
            "task_id": tid,
            "evidence_type": etype,
            "evidence_ref": ref,
            "evidence_json": {
                **payload,
                "schema_version": "workhub.evidence.v1",
                "submitted_by": "owner",
            },
        }
        if not rows:
            rows.append(row)
        assert rows[0] == row
        return row

    wh = SimpleNamespace(
        get_task=lambda *a: ({"http": 200}, task),
        wh_call=lambda *a: binding,
        refusal_reason=lambda r: None if r["http"] == 200 else "refused",
        list_evidence=lambda *a: ({"http": 200}, rows),
        json_contains=lambda actual, expected: all(
            actual.get(k) == v for k, v in expected.items()
        ),
    )
    lane = SimpleNamespace(
        STATE_DIR=tmp_path,
        LEASE_FILE=lp,
        TENANT="tenant",
        WH=wh,
        load_json=lambda p, d: json.loads(p.read_text()) if p.exists() else d,
        alive=lambda *a: False,
        recipe_session=lambda r: (None, cfg, "builder-token", "builder"),
        cfg_project=lambda c: c["project"],
        file_evidence_or_exact_replay=evidence,
    )
    monkeypatch.setattr(C, "settings", lambda l: {})
    monkeypatch.setattr(C, "session", lambda *a: ("owner-token", "owner"))
    return lane, task, binding, rows, lease, slot


def test_recover_preserves_originals_and_does_not_count_success(setup):
    lane, _, _, rows, _lease, _slot = setup
    original = lane.LEASE_FILE.read_bytes()
    out = R.recover(lane, C)
    assert out["action"] == "completed-halt-recovered"
    assert (
        lane.STATE_DIR / "completed-halt-recovery/lease.json"
    ).read_bytes() == original
    current = C.state(lane)
    assert (
        current["completed"] == []
        and current["slot"] is None
        and "halted" not in current
    )
    assert current["external_recoveries"][0]["unattended_success"] is False
    assert len(rows) == 1
    assert R.recover(lane, C) == out  # full replay after reset


@pytest.mark.parametrize(
    "case",
    [
        "live",
        "running",
        "assignment",
        "404other",
        "403",
        "effects",
        "different_halt",
        "review",
        "lineage",
        "missing_ticks",
    ],
)
def test_refuses_uncertain_or_active_state_without_reset(setup, case):
    lane, task, binding, rows, lease, slot = setup
    if case == "live":
        lane.alive = lambda *a: True
    if case == "running":
        task["status"] = "running"
    if case == "assignment":
        binding["http"] = 200
    if case == "404other":
        binding["body"]["detail"]["code"] = "not_found"
    if case == "403":
        binding["http"] = 403
    if case == "effects":
        slot["slot"]["operations"]["merge"] = {}
    if case == "different_halt":
        slot["halted"] = "other"
    if case == "review":
        lease["canonical_review_pending"]["operations"]["review"] = {}
    if case == "lineage":
        slot["slot"]["root_task_id"] = "other"
    if case == "missing_ticks":
        lease.pop("worker_ticks")
    C.persist(lane.LEASE_FILE, lease)
    C.save(lane, slot)
    with pytest.raises(RuntimeError):
        R.recover(lane, C)
    assert C.state(lane) == slot
    assert json.loads(lane.LEASE_FILE.read_text()) == lease
    assert rows == []


def test_evidence_readback_refusal_keeps_both_halts(setup):
    lane, _, _, _, lease, slot = setup
    lane.WH.list_evidence = lambda *a: ({"http": 403}, [])
    with pytest.raises(RuntimeError, match="readback"):
        R.recover(lane, C)
    assert C.state(lane) == slot and json.loads(lane.LEASE_FILE.read_text()) == lease


def test_crash_between_journal_updates_can_replay(setup, monkeypatch):
    lane, _, _, _, _, slot = setup
    save = C.save
    monkeypatch.setattr(C, "save", lambda *a: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        R.recover(lane, C)
    assert C.state(lane) == slot
    monkeypatch.setattr(C, "save", save)
    assert R.recover(lane, C)["action"] == "completed-halt-recovered"
    assert C.state(lane)["completed"] == []


def test_archive_tamper_refused(setup):
    lane, *_ = setup
    R.recover(lane, C)
    (lane.STATE_DIR / "completed-halt-recovery/lease.json").write_text("{}")
    with pytest.raises(RuntimeError, match="archive-drift"):
        R.recover(lane, C)


def test_cli_recovery_runs_before_continuous_halt_and_never_launches(
    setup, monkeypatch
):
    import sys

    sys.path.insert(0, str(ROOT))
    lane, *_ = setup
    module = load("canary_lane")
    for name in (
        "STATE_DIR",
        "LEASE_FILE",
        "TENANT",
        "WH",
        "load_json",
        "alive",
        "recipe_session",
        "cfg_project",
        "file_evidence_or_exact_replay",
    ):
        monkeypatch.setattr(module, name, getattr(lane, name))
    monkeypatch.setattr(module, "validate_startup_governance", lambda: None)
    monkeypatch.setattr(module.CONTINUATION, "validate_source", lambda *a: None)
    monkeypatch.setattr(module.CONTINUATION, "settings", lambda *a: {})
    monkeypatch.setattr(
        module.CONTINUATION, "session", lambda *a: ("owner-token", "owner")
    )
    monkeypatch.setattr(
        module, "run_once", lambda: pytest.fail("recovery launched controller")
    )
    monkeypatch.setattr(sys, "argv", ["canary_lane.py", "--recover-completed-halt"])
    assert module.main() == 0
    assert C.state(lane)["slot"] is None


def test_altered_audit_field_refuses_reset(setup):
    lane, _, _, rows, lease, slot = setup
    append = lane.file_evidence_or_exact_replay

    def altered(*args):
        receipt = append(*args)
        rows[0]["evidence_json"]["assignment_id"] = "different-assignment"
        return receipt

    lane.file_evidence_or_exact_replay = altered
    with pytest.raises(RuntimeError, match="readback"):
        R.recover(lane, C)
    assert C.state(lane) == slot
    assert json.loads(lane.LEASE_FILE.read_text()) == lease
