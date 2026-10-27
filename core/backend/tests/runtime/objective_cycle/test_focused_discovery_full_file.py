from pathlib import Path

from backend.aix_core.runtime.objective.discovery import run_focused_file_discovery
from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolbox


def test_focused_discovery_reads_full_web_php_not_64kb_truncated() -> None:
    root = Path(__file__).resolve().parents[5]
    fixture = root / "archive" / "legacy-stray-files-20260822" / "web.php"
    assert fixture.is_file(), f"missing web.php fixture at {fixture}"
    toolbox = RepositoryToolbox(repository_root=fixture.parent)
    report = run_focused_file_discovery(
        toolbox=toolbox, target_path="web.php", objective="refactor web.php"
    )
    assert "web.php" in report.config_files
    content = report.config_files["web.php"]
    assert len(content) > 100_000
    assert len(content.splitlines()) > 3000
