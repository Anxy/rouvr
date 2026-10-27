"""Planner excerpts for large files stay under Integration prompt caps."""

from __future__ import annotations

from types import SimpleNamespace

from backend.aix_core.runtime.objective.durable_api_backend import DurableObjectiveCycleApiBackend


def test_planner_excerpts_cap_god_file(tmp_path, monkeypatch) -> None:
    methods = "\n".join(f"  public function m{i}($a,$b,$c) {{ return $a; }}" for i in range(2500))
    huge = f"<?php\nnamespace App;\nclass GodController {{\n{methods}\n}}\n"
    (tmp_path / "GodController.php").write_text(huge, encoding="utf-8")
    assert len(huge) > 80_000

    backend = DurableObjectiveCycleApiBackend.__new__(DurableObjectiveCycleApiBackend)
    backend._settings = SimpleNamespace()
    monkeypatch.setattr(
        "backend.aix_core.intelligence.repository.repository_location.resolve_repository_root",
        lambda repo_id, settings=None: tmp_path,
    )
    obj = SimpleNamespace(repository_id="visions", allowed_files=("GodController.php",))
    understanding = SimpleNamespace(files_read=("GodController.php",))
    excerpts = DurableObjectiveCycleApiBackend._planner_file_excerpts(backend, obj, understanding)
    assert "GodController.php" in excerpts
    assert len(excerpts["GodController.php"]) <= 900
