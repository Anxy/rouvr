"""Class-hint → repository file binding for natural engineering objectives."""

from __future__ import annotations

from pathlib import Path

from backend.aix_core.intelligence.repository.class_path_resolve import (
    extract_class_name_hints,
    resolve_class_hints_in_repository,
)


def test_extract_cron_job_controller_hint() -> None:
    objective = (
        "In the Visions repository, CronJobController has grown into a god class. "
        "Refactor it into domain services."
    )
    assert "CronJobController" in extract_class_name_hints(objective)


def test_resolve_class_hint_finds_php_file(tmp_path: Path) -> None:
    (tmp_path / "CronJobController.php").write_text("<?php\nclass CronJobController {}\n", encoding="utf-8")
    (tmp_path / "vendor" / "pkg").mkdir(parents=True)
    (tmp_path / "vendor" / "pkg" / "CronJobController.php").write_text("ignore\n", encoding="utf-8")
    hits = resolve_class_hints_in_repository(tmp_path, ["CronJobController"])
    assert hits == ["CronJobController.php"]
