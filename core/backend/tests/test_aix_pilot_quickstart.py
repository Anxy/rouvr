from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "aix_pilot_quickstart.py"
    spec = importlib.util.spec_from_file_location("aix_pilot_quickstart", script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_steps_includes_start_by_default() -> None:
    mod = _load_module()
    steps = mod._build_steps(project_id="bh-migrated-7-aix", password="simple", skip_start=False)
    names = [step["name"] for step in steps]
    assert names[0] == "start_services"
    assert "test_slice" in names


def test_build_steps_skip_start() -> None:
    mod = _load_module()
    steps = mod._build_steps(project_id="bh-migrated-7-aix", password="simple", skip_start=True)
    names = [step["name"] for step in steps]
    assert "start_services" not in names
    assert names[0] == "bootstrap_operator"
