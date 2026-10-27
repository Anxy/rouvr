"""Contract drift Wave 1 (Option B): dict-canonical adapter, test-only.

The production contract path is plain dicts validated by the existing
prod resolver
(:func:`backend.legacy.aix_orchestrator_v2.discipline.resolve_canonical_contract`,
consumed by recon at ``recon_service.py:309-312``). :class:`TaskContract`
is the typed test-only view. This module is the single convergence point:

- :func:`from_execution_dict` runs the *existing* prod resolver over a prod
  execution dict — no new validation logic — then wraps the normalized
  result in a :class:`TaskContract` view for tests.
- Rejections carry the *same code* prod uses (``CONTRACT_AMBIGUOUS`` from
  the resolver, ``CONTRACT_INCOMPLETE`` for zero targets as in
  ``recon_service.py:562-565``).

TEST-ONLY: nothing in production imports this module. The dict path stays
canonical; the object stays a view. See ``evidence/aix-contract-drift/``.
"""

from __future__ import annotations

from typing import Any

from backend.aix_core.contracts.task_contract import TaskContract
from backend.legacy.aix_orchestrator_v2.discipline import resolve_canonical_contract

#: Prod code for an execution dict with zero usable targets (recon blocks
#: the same condition with CONTRACT_INCOMPLETE before any model call).
CONTRACT_INCOMPLETE = "CONTRACT_INCOMPLETE"


class ContractNotAdmissible(ValueError):
    """Adapter rejection carrying the prod reason code (``.code``)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def from_execution_dict(contract: dict[str, Any]) -> TaskContract:
    """Validate a prod execution dict with the prod resolver; return a view.

    Args:
        contract: one prod execution dict (``target_surface`` /
            ``execution_slice`` shape, as carried in task payloads).

    Returns:
        A :class:`TaskContract` view over the prod-normalized fields.
        Test-only: production keeps using the dict.

    Raises:
        ContractNotAdmissible: with ``.code`` equal to the prod reason
            code (``CONTRACT_AMBIGUOUS`` on conflicting sources,
            ``CONTRACT_INCOMPLETE`` on zero targets).
        TypeError: when ``contract`` is not a dict.
    """
    if not isinstance(contract, dict):
        raise TypeError(
            "from_execution_dict expects a prod execution dict, "
            f"got {type(contract).__name__}"
        )
    outcome = resolve_canonical_contract(
        task_payload={"active_execution_contract": dict(contract)},
        execution_contracts=None,
    )
    if not outcome.ok:
        raise ContractNotAdmissible(
            str(outcome.reason_code or "CONTRACT_REJECTED"),
            str(outcome.message or "prod resolver rejected the execution dict"),
        )
    fields = dict((outcome.payload or {}).get("fields") or {})
    target_files = [str(item) for item in list(fields.get("target_files") or [])]
    if not target_files:
        raise ContractNotAdmissible(
            CONTRACT_INCOMPLETE,
            "execution dict carries zero target files; prod blocks "
            "CONTRACT_INCOMPLETE before any model call",
        )
    symbols = [str(item) for item in list(fields.get("owning_symbols") or [])]
    verify_commands = [
        str(item) for item in list(fields.get("validation_commands") or [])
    ]
    exit_criteria = [
        str(item) for item in list(fields.get("acceptance_criteria") or [])
    ]
    return TaskContract(
        objective_id=str(contract.get("objective_id") or "unspecified-objective"),
        tenant_id=str(contract.get("tenant_id") or "unspecified-tenant"),
        files_in_scope=target_files,
        symbols_in_scope=symbols,
        verify_commands=verify_commands,
        exit_criteria=exit_criteria,
        commit_sha=str(contract.get("commit_sha") or ""),
    )


__all__ = ["CONTRACT_INCOMPLETE", "ContractNotAdmissible", "from_execution_dict"]
