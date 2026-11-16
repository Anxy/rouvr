"""demo_runner generation-aware done keys: reclaim must not stale-replay."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "docker" / "demo_runner.py"
    spec = importlib.util.spec_from_file_location("demo_runner", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_done_key_changes_when_updated_at_changes():
    mod = _load()
    a = mod._done_key(3, {"updated_at": "2026-11-13T12:00:00+00:00"})
    b = mod._done_key(3, {"updated_at": "2026-11-16T05:32:00+00:00"})
    assert a != b
    assert a.startswith("demo-done:3:")


def test_done_key_uses_lease_epoch_when_present():
    mod = _load()
    k = mod._done_key(9, {"lease_epoch": 2, "updated_at": "x"})
    assert k == "demo-done:9:2"
