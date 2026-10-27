from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from backend.services.devexecutor_pyright_lsp_service import shutdown_all_pyright_lsp_daemons

_TESTS_DIR = Path(__file__).resolve().parent
_AIX_ROOT = _TESTS_DIR.parents[2]
_MONOREPO_ROOT = _AIX_ROOT.parent


def _contract_engine_candidates() -> list[Path]:
    env = str(os.environ.get("HYEVE_TASK_CONTRACT_ENGINE_ROOT") or "").strip()
    paths: list[Path] = []
    if env:
        paths.append(Path(env))
    paths.append(_MONOREPO_ROOT / "hyeve" / "libs" / "hyeve_task_contract_engine")
    paths.append(_AIX_ROOT / "libs" / "hyeve_task_contract_engine")
    return paths


def _ensure_contract_engine_on_path() -> None:
    """AIX-CH-001: resolve sibling hyeve package. parents[5] previously pointed at D:\\."""
    for root in _contract_engine_candidates():
        if (root / "hyeve_task_contract_engine" / "__init__.py").is_file():
            text = str(root)
            if text not in sys.path:
                sys.path.insert(0, text)
            return
        if (root / "__init__.py").is_file() and root.name == "hyeve_task_contract_engine":
            text = str(root.parent)
            if text not in sys.path:
                sys.path.insert(0, text)
            return


_ensure_contract_engine_on_path()

try:
    from backend.shared.monorepo_imports import ensure_hyeve_core_import_path

    ensure_hyeve_core_import_path()
except ModuleNotFoundError:
    pass


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "enable_pyright_lsp: run this test with the live pyright LSP daemon enabled",
    )


@pytest.fixture(autouse=True)
def _pyright_lsp_test_mode(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    enable_live_lsp = request.node.get_closest_marker("enable_pyright_lsp") is not None
    monkeypatch.setenv("AIX_ENABLE_PYRIGHT_LSP", "1" if enable_live_lsp else "0")
    shutdown_all_pyright_lsp_daemons()
    yield
    shutdown_all_pyright_lsp_daemons()


# --- Rouvr public build: some legacy CRUD/service suites depend on the private
# `hyeve_core` framework package. Skip collecting them when it is not installed.
try:  # pragma: no cover
    import hyeve_core  # noqa: F401
    _HAS_HYEVE_CORE = True
except ImportError:  # pragma: no cover
    _HAS_HYEVE_CORE = False

_REQUIRES_CORE = {
    line.strip()
    for line in (_TESTS_DIR / "requires_hyeve_core.txt").read_text().splitlines()
    if line.strip()
}


def pytest_ignore_collect(collection_path, config):  # noqa: D401
    if _HAS_HYEVE_CORE:
        return None
    try:
        rel = Path(collection_path).resolve().relative_to(_TESTS_DIR).as_posix()
    except ValueError:
        return None
    return True if rel in _REQUIRES_CORE else None
