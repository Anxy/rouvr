"""F0 baseline-lock documentation contracts."""

from __future__ import annotations

import importlib.util
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
AIX_ROOT = BACKEND_ROOT.parents[1]
ROADMAP_ROOT = AIX_ROOT / "docs" / "current" / "AIX-Framework-Roadmap"
VALIDATOR = ROADMAP_ROOT / "scripts" / "validate_f0_baseline.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_f0_baseline", VALIDATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_f0_documentation_baseline_resolves_against_repository() -> None:
    report = _load_validator().validate()
    assert report["status"] == "PASS", report


def test_f0_packet_deliverables_exist() -> None:
    required = (
        ROADMAP_ROOT / "invariants" / "FRAMEWORK-INVARIANTS.md",
        ROADMAP_ROOT / "specs" / "EQUIVALENCE-HARNESS-SPEC.md",
        ROADMAP_ROOT / "specs" / "AUTHORITY-MATRIX-EXTENSION.md",
    )
    assert all(path.is_file() for path in required)
