"""Tests asserting zero production imports of retired verification_engine.py (F5-P4)."""

from __future__ import annotations

import os
from pathlib import Path


def test_zero_production_imports_of_verification_engine():
    backend_dir = Path(__file__).resolve().parents[3]
    aix_core_dir = backend_dir / "aix_core"

    offending_lines: list[str] = []

    for root, _, files in os.walk(aix_core_dir):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = Path(root) / f
            # Skip the shim itself and test files
            if path.name == "verification_engine.py" or "tests" in path.parts:
                continue
            
            with open(path, "r", encoding="utf-8", errors="ignore") as pyf:
                for line_no, line in enumerate(pyf, 1):
                    # Check active imports
                    stripped = line.strip()
                    if (
                        stripped.startswith("from ") or stripped.startswith("import ")
                    ) and "verification_engine" in stripped:
                        offending_lines.append(f"{path.relative_to(backend_dir)}:{line_no} -> {stripped}")

    assert not offending_lines, f"Found production imports of retired verification_engine:\n" + "\n".join(offending_lines)


def test_verification_engine_shim_backward_compatibility():
    import backend.aix_core.runtime.verification.verification_engine as shim
    import backend.aix_core.framework.verification as fw

    assert shim.VerificationCheck is fw.VerificationCheck
    assert shim.VerificationOutcome is fw.VerificationOutcome
    assert shim.VERIFIER_VERSION == fw.VERIFIER_VERSION
    assert shim.VERIFICATION_TYPES == fw.VERIFICATION_TYPES
    assert shim.build_checks_from_read_verification is fw.build_checks_from_read_verification
    assert shim.build_checks_from_completion_gates is fw.build_checks_from_completion_gates
    assert shim.aggregate_outcome is fw.aggregate_outcome
