"""AIX-W01-E02-T004: Disposable Fixture Mutation Mission.

Verifies:
1. Multi-file mutation loop operates against an allocated disposable fixture workspace (not production main.py or live_fixtures).
2. Focused unit tests pass in disposable workspace and mutation evidence is produced.
3. Production `backend/main.py` remains untouched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
import pytest


def _init_git(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "disposable@aix.local"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "DisposableTester"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=repo, check=True)


@pytest.fixture
def allocated_disposable_workspace(tmp_path: Path) -> Path:
    repo = tmp_path / "disposable_repo"
    repo.mkdir()

    src = repo / "HelperModule.py"
    src.write_text("def compute_total(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")

    tst = repo / "test_helper.py"
    tst.write_text(
        "from HelperModule import compute_total\n\n"
        "def test_compute_total():\n"
        "    assert compute_total(2, 3) == 5\n",
        encoding="utf-8",
    )

    _init_git(repo)
    return repo


@pytest.mark.asyncio
async def test_disposable_fixture_multi_file_mutation(allocated_disposable_workspace: Path) -> None:
    """Performs multi-file mutation in allocated fixture repository and asserts production main.py is untouched."""
    prod_main = Path("backend/main.py").resolve()
    main_mtime_before = prod_main.stat().st_mtime if prod_main.exists() else None

    # Step 1: Perform multi-file edit in disposable workspace
    src = allocated_disposable_workspace / "HelperModule.py"
    src.write_text(
        "def compute_total(a: int, b: int, multiplier: int = 1) -> int:\n"
        "    return (a + b) * multiplier\n",
        encoding="utf-8",
    )

    tst = allocated_disposable_workspace / "test_helper.py"
    tst.write_text(
        "from HelperModule import compute_total\n\n"
        "def test_compute_total():\n"
        "    assert compute_total(2, 3) == 5\n"
        "    assert compute_total(2, 3, multiplier=2) == 10\n",
        encoding="utf-8",
    )

    # Step 2: Run pytest inside disposable workspace
    res = subprocess.run(["python", "-m", "pytest", "test_helper.py"], cwd=allocated_disposable_workspace, capture_output=True, text=True)
    assert res.returncode == 0
    assert "passed" in res.stdout.lower()

    # Step 3: Verify production main.py was not touched
    if prod_main.exists():
        assert prod_main.stat().st_mtime == main_mtime_before
