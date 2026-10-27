"""Contract drift Wave 1 (Option B): dict-canonical parity tests.

Proves the prod dict path and the TaskContract test-only view converge
through :func:`from_execution_dict`: same inputs, same verdicts,
same codes. Zero prod risk: production is untouched; only the adapter
(new, test-only) and this file are new.

Run from hyeve-aix-service/:  pytest backend/tests/test_contract_parity.py -q
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from backend.aix_core.contract_adapter import (
    ContractNotAdmissible,
    from_execution_dict,
)
from backend.aix_core.contracts.task_contract import check_filesystem_scope
from backend.aix_core import contract_adapter as _adapter_mod
from backend.legacy.aix_orchestrator_v2.discipline import resolve_canonical_contract


def _prod_dict() -> dict:
    """Prod-shaped execution dict mirroring the Wave 1 object fixture."""
    return {
        "target_surface": {"target_files": ["owned.py"], "owning_symbols": ["pkg.mod.fn"]},
        "execution_slice": {"target_files": ["owned.py"], "validation_commands": ["pytest -q"]},
        "acceptance_criteria": ["green"],
        "commit_sha": "abc123",
    }


def test_pass_dict_view_agrees_with_prod_fields() -> None:
    view = from_execution_dict(_prod_dict())
    prod = resolve_canonical_contract(
        task_payload={"active_execution_contract": _prod_dict()},
        execution_contracts=None,
    )
    assert prod.ok is True
    fields = dict(prod.payload["fields"])
    assert view.files_in_scope == list(fields["target_files"]) == ["owned.py"]
    assert view.symbols_in_scope == list(fields["owning_symbols"]) == ["pkg.mod.fn"]
    assert view.verify_commands == list(fields["validation_commands"]) == ["pytest -q"]
    assert view.exit_criteria == ["green"]
    assert view.commit_sha == "abc123"


def test_empty_dict_is_contract_incomplete_like_prod() -> None:
    with pytest.raises(ContractNotAdmissible) as excinfo:
        from_execution_dict({})
    assert excinfo.value.code == "CONTRACT_INCOMPLETE"


def test_zero_targets_is_contract_incomplete_like_prod() -> None:
    with pytest.raises(ContractNotAdmissible) as excinfo:
        from_execution_dict({"target_surface": {}, "execution_slice": {}})
    assert excinfo.value.code == "CONTRACT_INCOMPLETE"


def test_resolver_code_surfaced_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ambiguous sources -> adapter surfaces the prod code code-for-code."""
    from backend.legacy.aix_orchestrator_v2.discipline import ValidationOutcome

    monkeypatch.setattr(
        _adapter_mod,
        "resolve_canonical_contract",
        lambda **kwargs: ValidationOutcome(
            ok=False, reason_code="CONTRACT_AMBIGUOUS", message="disagree"
        ),
    )
    with pytest.raises(ContractNotAdmissible) as excinfo:
        from_execution_dict(_prod_dict())
    assert excinfo.value.code == "CONTRACT_AMBIGUOUS"


def test_non_dict_rejected() -> None:
    with pytest.raises(TypeError):
        from_execution_dict(["owned.py"])  # type: ignore[arg-type]


def test_view_passes_object_filesystem_gate() -> None:
    """Adapter view converges with the object's FS gate on the same files."""
    tmp = Path(tempfile.mkdtemp(prefix="aix-parity-"))
    try:
        (tmp / "owned.py").write_text("x = 1\n", encoding="utf-8")
        view = from_execution_dict(_prod_dict())
        ok, missing = check_filesystem_scope(view, workspace_root=tmp)
        assert (ok, missing) == (True, [])
        ghost = from_execution_dict(
            {
                **_prod_dict(),
                "target_surface": {"target_files": ["ghost.py"]},
                "execution_slice": {"target_files": ["ghost.py"]},
            }
        )
        ok, missing = check_filesystem_scope(ghost, workspace_root=tmp)
        assert ok is False and missing == ["ghost.py"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
