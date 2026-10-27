"""AIX-GAEF-001: policy sits between ActionRequest and mutation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.aix_core.runtime.foundation.capability_registry import OperationSpec, resolve_capability
from backend.aix_core.runtime.objective.contracts import ActionRequestV1

if TYPE_CHECKING:
    from backend.aix_core.runtime.foundation.execution_context import ExecutionContextV1

POLICY_CODES = frozenset(
    {
        "CAPABILITY_NOT_AVAILABLE",
        "INVALID_ARGUMENTS",
        "PATH_OUTSIDE_SCOPE",
        "REPOSITORY_NOT_AUTHORIZED",
        "MUTATION_NOT_PERMITTED",
        "APPROVAL_REQUIRED",
        "POLICY_DENIED",
        "BUDGET_EXCEEDED",
    }
)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    code: str | None = None
    detail: str = ""
    capability: str = ""
    operation: str = ""
    spec: OperationSpec | None = None


def _normalize_rel(raw: object) -> str:
    text = str(raw or "").replace("\\", "/").strip().lstrip("/")
    return text


def _path_args(arguments: dict[str, Any]) -> tuple[str, ...]:
    keys = ("path", "source", "destination", "target")
    out: list[str] = []
    for key in keys:
        value = _normalize_rel(arguments.get(key))
        if value:
            out.append(value)
    paths = arguments.get("paths")
    if isinstance(paths, (list, tuple)):
        for item in paths:
            value = _normalize_rel(item)
            if value:
                out.append(value)
    return tuple(dict.fromkeys(out))


def confine_path_to_repository(raw: str, repository_root: Path) -> str | None:
    """Return a repo-relative posix path, or None when the path is a real escape.

    Absolute paths that resolve inside ``repository_root`` are configuration
    mistakes (CLI/objective pasted a Windows path), not security violations.
    ``..`` and paths that land outside the bound root stay rejected.
    """
    text = str(raw or "").replace("\\", "/").strip()
    if not text:
        return None
    root = repository_root.resolve()
    is_abs = text.startswith("/") or (len(text) >= 2 and text[1] == ":")
    if is_abs:
        try:
            confined = Path(text).resolve()
            rel = confined.relative_to(root).as_posix()
        except (OSError, ValueError):
            return None
        return rel or "."
    stripped = text.lstrip("/")
    if any(part == ".." for part in Path(stripped).parts):
        return None
    try:
        (root / stripped).resolve().relative_to(root)
    except (OSError, ValueError):
        return None
    return stripped


def authorize_action(
    request: ActionRequestV1,
    *,
    repository_id: str,
    repository_root: Path | None,
    allowed_files: tuple[str, ...] = (),
    apply_edits: bool = True,
    action_budget_remaining: int = 8,
    mutation_budget_remaining: int = 8,
    step_approval_required: bool = False,
    approved: bool = True,
    execution_context: "ExecutionContextV1 | None" = None,
) -> PolicyDecision:
    resolved = resolve_capability(request.capability, request.operation)
    if resolved is None:
        return PolicyDecision(
            allowed=False,
            code="CAPABILITY_NOT_AVAILABLE",
            detail=f"capability {request.capability!r} operation {request.operation!r} is not registered",
        )
    capability, operation, spec = resolved
    if action_budget_remaining <= 0:
        return PolicyDecision(
            allowed=False,
            code="BUDGET_EXCEEDED",
            detail="max actions exhausted",
            capability=capability,
            operation=operation,
            spec=spec,
        )
    if spec.mutates and mutation_budget_remaining <= 0:
        return PolicyDecision(
            allowed=False,
            code="BUDGET_EXCEEDED",
            detail="mutation budget exhausted",
            capability=capability,
            operation=operation,
            spec=spec,
        )
    missing = [name for name in spec.arguments if not str((request.arguments or {}).get(name) or "").strip()]
    if missing:
        return PolicyDecision(
            allowed=False,
            code="INVALID_ARGUMENTS",
            detail=f"missing required arguments: {missing}",
            capability=capability,
            operation=operation,
            spec=spec,
        )
    if not str(repository_id or "").strip() or repository_root is None:
        return PolicyDecision(
            allowed=False,
            code="REPOSITORY_NOT_AUTHORIZED",
            detail="no authorized repository root",
            capability=capability,
            operation=operation,
            spec=spec,
        )
    if spec.mutates and not apply_edits:
        return PolicyDecision(
            allowed=False,
            code="MUTATION_NOT_PERMITTED",
            detail="apply_edits is false",
            capability=capability,
            operation=operation,
            spec=spec,
        )
    # AIX-UGEF-001: when execution_context is provided, derive approval state
    # from the context rather than the caller-supplied default. This closes the
    # approved=True shortcut that previously let delete bypass confirmation.
    effective_approved = approved
    if execution_context is not None:
        effective_approved = execution_context.approved
    needs_approval = spec.approval_required or step_approval_required
    if needs_approval and not effective_approved:
        return PolicyDecision(
            allowed=False,
            code="APPROVAL_REQUIRED",
            detail=f"{capability}.{operation} requires approval",
            capability=capability,
            operation=operation,
            spec=spec,
        )
    allowed_norm = tuple(_normalize_rel(p) for p in allowed_files if _normalize_rel(p))
    for raw_path in _path_args(request.arguments or {}):
        relative = confine_path_to_repository(raw_path, repository_root)
        if relative is None:
            return PolicyDecision(
                allowed=False,
                code="PATH_OUTSIDE_SCOPE",
                detail=f"path {raw_path!r} escapes repository scope",
                capability=capability,
                operation=operation,
                spec=spec,
            )
        confined = (repository_root / relative).resolve() if relative not in {".", ""} else repository_root.resolve()
        try:
            confined.relative_to(repository_root.resolve())
        except ValueError:
            return PolicyDecision(
                allowed=False,
                code="PATH_OUTSIDE_SCOPE",
                detail=f"path {raw_path!r} resolves outside the repository",
                capability=capability,
                operation=operation,
                spec=spec,
            )
        if allowed_norm:
            ok = False
            for bound in allowed_norm:
                if relative == bound or relative.startswith(f"{bound.rstrip('/')}/") or bound.startswith(
                    f"{relative.rstrip('/')}/"
                ):
                    ok = True
                    break
                if Path(relative).name == Path(bound).name:
                    ok = True
                    break
            if not ok:
                return PolicyDecision(
                    allowed=False,
                    code="PATH_OUTSIDE_SCOPE",
                    detail=f"path {relative!r} is not in allowed_files {list(allowed_norm)}",
                    capability=capability,
                    operation=operation,
                    spec=spec,
                )
    # AIX-UGEF-001: consult the policy store when execution context is available.
    if execution_context is not None:
        from backend.aix_core.runtime.foundation.policy_store import get_policy_store

        store = get_policy_store()
        store_decision, store_rule = store.evaluate(
            capability=capability,
            operation=operation,
            project_id=execution_context.project_id,
            principal_role=execution_context.principal.role,
            principal_id=execution_context.principal.id,
            session_grants=execution_context.session_grants,
        )
        if store_decision == "DENY":
            return PolicyDecision(
                allowed=False,
                code="POLICY_DENIED",
                detail=store_rule.reason if store_rule else f"{capability}.{operation} denied by policy",
                capability=capability,
                operation=operation,
                spec=spec,
            )
        if store_decision == "CONFIRM" and not effective_approved:
            return PolicyDecision(
                allowed=False,
                code="APPROVAL_REQUIRED",
                detail=store_rule.reason if store_rule else f"{capability}.{operation} requires confirmation",
                capability=capability,
                operation=operation,
                spec=spec,
            )
        if store_decision == "ELEVATE":
            return PolicyDecision(
                allowed=False,
                code="ELEVATION_REQUIRED",
                detail=store_rule.reason if store_rule else f"{capability}.{operation} requires elevation",
                capability=capability,
                operation=operation,
                spec=spec,
            )

    return PolicyDecision(allowed=True, capability=capability, operation=operation, spec=spec)


__all__ = ["POLICY_CODES", "PolicyDecision", "authorize_action", "confine_path_to_repository"]
