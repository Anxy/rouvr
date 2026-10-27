"""AIX-GAEF-001: boring capability executor. Performs exactly the authorized operation."""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.aix_core.intelligence.repository.repository_location import (
    RepositoryLocationError,
    resolve_repository_root,
)
from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolbox, RepositoryToolError
from backend.aix_core.runtime.executor.filesystem_primitives import (
    FilesystemPrimitiveError,
    apply_rename_path,
)
from backend.aix_core.runtime.executor.patch_validate import validate_write_file_policy
from backend.aix_core.runtime.foundation.capability_registry import resolve_capability
from backend.aix_core.runtime.foundation.policy_gateway import PolicyDecision, authorize_action
from backend.aix_core.runtime.objective.contracts import ActionObservationV1, ActionRequestV1, StepContractV1
from backend.aix_core.runtime.verification.failure_taxonomy import (
    FailureClassificationV1,
    classify as classify_failure,
)
from backend.aix_core.runtime.verification.verification import VerificationResultV1

if TYPE_CHECKING:
    from backend.aix_core.runtime.foundation.execution_context import ExecutionContextV1


class CapabilityExecutorError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


def actions_from_step_contract(step_contract: StepContractV1 | None) -> tuple[ActionRequestV1, ...]:
    if step_contract is None:
        return ()
    return tuple(step_contract.actions or ())


def observation_to_dict(obs: ActionObservationV1) -> dict[str, Any]:
    return obs.model_dump(mode="json")


def _failed(request: ActionRequestV1, code: str, message: str) -> ActionObservationV1:
    return ActionObservationV1(
        action_id=request.action_id,
        status="BLOCKED" if code in {"APPROVAL_REQUIRED", "POLICY_DENIED", "BUDGET_EXCEEDED"} else "FAILED",
        changed=False,
        observation={},
        evidence=(),
        error_code=code,
        error_message=message,
    )


def _ok(request: ActionRequestV1, *, changed: bool, observation: dict[str, Any], evidence: tuple[str, ...] = ()) -> ActionObservationV1:
    return ActionObservationV1(
        action_id=request.action_id,
        status="SUCCESS",
        changed=changed,
        observation=observation,
        evidence=evidence,
        error_code=None,
        error_message=None,
    )


def _confine_rel(root: Path, relative: str) -> str:
    from backend.aix_core.runtime.foundation.policy_gateway import confine_path_to_repository

    confined = confine_path_to_repository(relative, root)
    if confined is None:
        raise ValueError(f"path {relative!r} escapes repository scope")
    return confined


def _write_gitkeep(root: Path, path_arg: str, *, marker: bool = True) -> dict[str, Any]:
    posix = _confine_rel(root, path_arg)
    target = root / posix
    if posix.endswith(".gitkeep") or target.suffix:
        directory = target.parent
        marker_path = target
    else:
        directory = target
        marker_path = directory / ".gitkeep" if marker else directory
    directory.mkdir(parents=True, exist_ok=True)
    created_marker = False
    if marker and (marker_path.suffix == ".gitkeep" or marker_path.name == ".gitkeep"):
        policy = validate_write_file_policy(marker_path, "", create_only=True)
        if not marker_path.is_file():
            if not policy.allowed:
                raise CapabilityExecutorError(policy.reason_code or "CREATE_ONLY_VIOLATION", policy.reason_detail)
            marker_path.write_text("", encoding="utf-8")
            created_marker = True
        rel_marker = marker_path.resolve().relative_to(root.resolve()).as_posix()
        return {
            "directory": directory.resolve().relative_to(root.resolve()).as_posix(),
            "marker": rel_marker,
            "created_marker": created_marker,
            "exists": True,
        }
    rel_dir = directory.resolve().relative_to(root.resolve()).as_posix()
    return {"directory": rel_dir, "marker": None, "created_marker": False, "exists": directory.is_dir()}


def execute_action(
    request: ActionRequestV1,
    *,
    repository_id: str,
    settings: object | None = None,
    allowed_files: tuple[str, ...] = (),
    apply_edits: bool = True,
    action_budget_remaining: int = 8,
    mutation_budget_remaining: int = 8,
    approved: bool = True,
    step_approval_required: bool = False,
    toolbox: RepositoryToolbox | None = None,
    execution_context: "ExecutionContextV1 | None" = None,
) -> ActionObservationV1:
    """Execute one governed action.

    When ``AIX_FRAMEWORK_CAPABILITY_FACADE`` is true, resolution/policy run through
    CapabilityRegistryFacade + CapabilityProviderResolver, then ToolCapabilityAdapter
    delegates to the legacy core executor (same SoR). Flag false = legacy path only.
    """
    from backend.aix_core.framework.capability import capability_facade_enabled

    if capability_facade_enabled(settings):
        return _execute_action_via_framework(
            request,
            repository_id=repository_id,
            settings=settings,
            allowed_files=allowed_files,
            apply_edits=apply_edits,
            action_budget_remaining=action_budget_remaining,
            mutation_budget_remaining=mutation_budget_remaining,
            approved=approved,
            step_approval_required=step_approval_required,
            toolbox=toolbox,
            execution_context=execution_context,
        )
    return _execute_action_legacy(
        request,
        repository_id=repository_id,
        settings=settings,
        allowed_files=allowed_files,
        apply_edits=apply_edits,
        action_budget_remaining=action_budget_remaining,
        mutation_budget_remaining=mutation_budget_remaining,
        approved=approved,
        step_approval_required=step_approval_required,
        toolbox=toolbox,
        execution_context=execution_context,
    )


def _execute_action_legacy(
    request: ActionRequestV1,
    *,
    repository_id: str,
    settings: object | None = None,
    allowed_files: tuple[str, ...] = (),
    apply_edits: bool = True,
    action_budget_remaining: int = 8,
    mutation_budget_remaining: int = 8,
    approved: bool = True,
    step_approval_required: bool = False,
    toolbox: RepositoryToolbox | None = None,
    execution_context: "ExecutionContextV1 | None" = None,
) -> ActionObservationV1:
    from backend.aix_core.runtime.foundation.audit import (
        audit_capability_requested,
        audit_authorization_decision,
        audit_execution_started,
        audit_execution_completed,
    )

    audit_capability_requested(request, execution_context)

    try:
        root = resolve_repository_root(repository_id, settings=settings)
    except RepositoryLocationError as exc:
        obs = _failed(request, exc.code, exc.detail)
        audit_execution_completed(request, obs, execution_context)
        return obs

    decision: PolicyDecision = authorize_action(
        request,
        repository_id=repository_id,
        repository_root=root,
        allowed_files=allowed_files,
        apply_edits=apply_edits,
        action_budget_remaining=action_budget_remaining,
        mutation_budget_remaining=mutation_budget_remaining,
        step_approval_required=step_approval_required,
        approved=approved,
        execution_context=execution_context,
    )
    audit_authorization_decision(request, decision, execution_context)
    if not decision.allowed:
        return _failed(request, decision.code or "POLICY_DENIED", decision.detail)

    capability = decision.capability
    operation = decision.operation
    args = dict(request.arguments or {})
    box = toolbox or RepositoryToolbox(repository_root=root)
    audit_execution_started(request, execution_context)

    obs = _execute_capability(request, capability, operation, args, root, box, apply_edits)
    audit_execution_completed(request, obs, execution_context)
    return obs


def _execute_action_via_framework(
    request: ActionRequestV1,
    *,
    repository_id: str,
    settings: object | None = None,
    allowed_files: tuple[str, ...] = (),
    apply_edits: bool = True,
    action_budget_remaining: int = 8,
    mutation_budget_remaining: int = 8,
    approved: bool = True,
    step_approval_required: bool = False,
    toolbox: RepositoryToolbox | None = None,
    execution_context: "ExecutionContextV1 | None" = None,
) -> ActionObservationV1:
    """F2 wired path: facade resolve → policy → ToolCapabilityAdapter → legacy core."""
    from backend.aix_core.framework.capability.models import CapabilityExecutionRequest
    from backend.aix_core.framework.capability.provider import CapabilityProviderResolver
    from backend.aix_core.framework.capability.tool_wiring import ToolCapabilityAdapter
    from backend.aix_core.runtime.foundation.audit import (
        audit_capability_requested,
        audit_authorization_decision,
        audit_execution_completed,
    )

    audit_capability_requested(request, execution_context)

    try:
        root = resolve_repository_root(repository_id, settings=settings)
    except RepositoryLocationError as exc:
        obs = _failed(request, exc.code, exc.detail)
        audit_execution_completed(request, obs, execution_context)
        return obs

    resolver = CapabilityProviderResolver()
    resolution = resolver.resolve(
        request,
        repository_id=repository_id,
        repository_root=root,
        allowed_files=allowed_files,
        apply_edits=apply_edits,
        action_budget_remaining=action_budget_remaining,
        mutation_budget_remaining=mutation_budget_remaining,
        step_approval_required=step_approval_required,
        approved=approved,
        execution_context=execution_context,
    )
    if resolution.policy is not None:
        audit_authorization_decision(request, resolution.policy, execution_context)
    if not resolution.allowed:
        code = resolution.reason_codes[0] if resolution.reason_codes else "POLICY_DENIED"
        obs = _failed(request, code, resolution.detail or "framework capability resolution denied")
        audit_execution_completed(request, obs, execution_context)
        return obs

    adapter = ToolCapabilityAdapter(
        repository_id=repository_id,
        settings=settings,
        allowed_files=allowed_files,
        apply_edits=apply_edits,
        action_budget_remaining=action_budget_remaining,
        mutation_budget_remaining=mutation_budget_remaining,
        approved=approved,
        step_approval_required=step_approval_required,
        toolbox=toolbox,
        execution_context=execution_context,
        execute_fn=_execute_action_legacy,
    )
    exec_req = CapabilityExecutionRequest(
        capability_id=resolution.capability_id,
        operation=resolution.operation,
        caller_role=str(getattr(getattr(execution_context, "principal", None), "role", None) or "executor"),
        arguments=dict(request.arguments or {}),
        request_id=str(request.action_id or ""),
    )
    result = adapter.execute_sync(exec_req)
    if result.success:
        return _ok(
            request,
            changed=bool(result.changed),
            observation=dict(result.output or {}),
            evidence=tuple(result.evidence_paths or ()),
        )
    return _failed(
        request,
        result.error_code or "FRAMEWORK_TOOL_FAILED",
        result.error_message or "tool adapter failed",
    )

def _execute_capability(
    request: ActionRequestV1,
    capability: str,
    operation: str,
    args: dict[str, Any],
    root: Path,
    box: RepositoryToolbox,
    apply_edits: bool,
) -> ActionObservationV1:
    try:
        if capability == "filesystem" and operation == "read":
            result = box.read_file(_confine_rel(root, str(args["path"])))
            return _ok(request, changed=False, observation={"path": args["path"], "read": result})
        if capability == "filesystem" and operation == "inspect":
            path = _confine_rel(root, str(args.get("path") or "."))
            result = box.list_directory(path)
            return _ok(request, changed=False, observation={"path": path, "listing": result})
        if capability == "filesystem" and operation == "search":
            path = _confine_rel(root, str(args.get("path") or "."))
            result = box.search_files(str(args["query"]), path)
            return _ok(
                request,
                changed=False,
                observation={"query": args["query"], "path": path, "hits": result.get("hits") or []},
            )
        if capability == "filesystem" and operation in {"exists", "stat"}:
            rel = _confine_rel(root, str(args["path"]))
            target = (root / rel).resolve()
            target.relative_to(root.resolve())
            payload = {"path": rel, "exists": target.exists()}
            if operation == "stat" and target.exists():
                stat = target.stat()
                payload.update({"is_dir": target.is_dir(), "is_file": target.is_file(), "size": stat.st_size})
            return _ok(request, changed=False, observation=payload)
        if capability == "filesystem" and operation == "mkdir":
            marker = args.get("marker", True)
            payload = _write_gitkeep(root, str(args["path"]), marker=bool(marker))
            return _ok(request, changed=True, observation=payload)
        if capability == "filesystem" and operation == "rename":
            result = apply_rename_path(
                repository_root=root,
                source_rel=str(args["source"]),
                dest_rel=str(args["destination"]),
                apply=apply_edits,
            )
            return _ok(request, changed=bool(result.get("applied")), observation=dict(result))
        if capability == "filesystem" and operation == "copy":
            src_rel = _confine_rel(root, str(args["source"]))
            dst_rel = _confine_rel(root, str(args["destination"]))
            src = (root / src_rel).resolve()
            dst = (root / dst_rel).resolve()
            src.relative_to(root.resolve())
            dst.relative_to(root.resolve())
            if not src.exists():
                return _failed(request, "PATH_NOT_FOUND", src_rel)
            if dst.exists():
                return _failed(request, "DESTINATION_EXISTS", dst_rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            return _ok(
                request,
                changed=True,
                observation={"source": src_rel, "destination": dst_rel, "copied": True},
            )
        if capability == "filesystem" and operation == "create":
            rel = _confine_rel(root, str(args["path"]))
            target = root / rel
            policy = validate_write_file_policy(target, str(args.get("content") or ""), create_only=True)
            if not policy.allowed:
                return _failed(request, policy.reason_code or "CREATE_ONLY_VIOLATION", policy.reason_detail or "")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(args.get("content") or ""), encoding="utf-8")
            return _ok(request, changed=True, observation={"path": rel, "created": True})
        if capability == "filesystem" and operation == "delete":
            rel = _confine_rel(root, str(args["path"]))
            target = (root / rel).resolve()
            target.relative_to(root.resolve())
            if not target.exists():
                return _failed(request, "PATH_NOT_FOUND", rel)
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return _ok(request, changed=True, observation={"path": rel, "deleted": True})
        if capability == "repository" and operation == "resolve_symbol":
            from backend.aix_core.intelligence.repository.symbol_resolve import SymbolResolver

            resolution = SymbolResolver().resolve_symbol(
                symbol_name=str(args["symbol_name"]),
                target_file=str(args["target_file"]) if args.get("target_file") else None,
                workspace_root=root,
            )
            return _ok(
                request,
                changed=False,
                observation={
                    "status": resolution.status,
                    "symbol_name": resolution.symbol_name,
                    "locations": [
                        {"file_path": loc.file_path, "start_line": loc.start_line, "end_line": loc.end_line}
                        for loc in resolution.locations
                    ],
                },
            )
        if capability in {"tests", "search"} and operation in {"run", "code"}:
            if capability == "search" or (capability == "tests" and operation == "code"):
                result = box.search_files(str(args.get("query") or args.get("command") or ""), ".")
                return _ok(request, changed=False, observation={"hits": result.get("hits") or []})
            # Planner emits tests.run with arguments.command (string) or path/filter.
            # RepositoryToolbox.run_tests requires an argv list — never call it bare
            # (live cyc_01M0FG780D0Z: TypeError missing test_command → empty evidence).
            raw_cmd = args.get("command") or args.get("test_command")
            if isinstance(raw_cmd, (list, tuple)) and raw_cmd:
                test_command = [str(part) for part in raw_cmd if str(part).strip()]
            elif isinstance(raw_cmd, str) and raw_cmd.strip():
                test_command = shlex.split(raw_cmd, posix=os.name != "nt")
            else:
                path = str(args.get("path") or "").strip()
                filt = str(args.get("filter") or "").strip()
                test_command = ["pytest", "-q"]
                if path:
                    test_command.append(path)
                if filt:
                    test_command.extend(["-k", filt])
            if not test_command:
                return _failed(request, "INVALID_ARGUMENTS", "tests.run requires command, path, or filter")
            timeout = float(args.get("timeout_seconds") or 300.0)
            result = box.run_tests(test_command, timeout_seconds=timeout)
            return _ok(request, changed=False, observation={"tests": result, "command": test_command})
        if capability == "filesystem" and operation == "patch":
            return _ok(
                request,
                changed=False,
                observation={
                    "deferred": True,
                    "reason": "filesystem.patch authorized; execution delegated to multi-step applicator",
                },
            )

        # ── process.* ────────────────────────────────────────────────────
        if capability == "process" and operation in {"run", "run_mutating"}:
            return _execute_process(request, args, root, apply_edits)

        # ── git.* ────────────────────────────────────────────────────────
        if capability == "git":
            return _execute_git(request, operation, args, root, apply_edits)

        # ── network.* ────────────────────────────────────────────────────
        if capability == "network" and operation in {"http_get", "http_post"}:
            return _failed(request, "CAPABILITY_DEFERRED", "network operations require sandbox implementation")

        return _failed(request, "CAPABILITY_NOT_AVAILABLE", f"{capability}.{operation} has no executor")
    except (FilesystemPrimitiveError, RepositoryToolError, CapabilityExecutorError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        detail = getattr(exc, "detail", str(exc))
        return _failed(request, str(code), str(detail))
    except ValueError as exc:
        return _failed(request, "PATH_OUTSIDE_SCOPE", str(exc))


def _execute_process(
    request: ActionRequestV1,
    args: dict[str, Any],
    root: Path,
    apply_edits: bool,
) -> ActionObservationV1:
    """Execute a sandboxed process command within the repository.

    Uses a genuinely blocking `subprocess.run` (up to 120s) -- correct only because
    every production caller reaches this exclusively through `execute_action_loop`
    wrapped in `asyncio.to_thread` (see `real_execute.py`'s two call sites). A future
    caller that invokes `execute_action`/`execute_action_loop`/`_execute_capability`
    directly from an async function without `asyncio.to_thread` would block the whole
    shared uvicorn event loop (single-process, no --workers) -- including unrelated
    `/health` and other cycles' requests -- for the length of this call. Do not remove
    the `asyncio.to_thread` wrapping at the call sites without moving the async
    boundary in here instead.
    """
    import subprocess

    command = str(args.get("command") or "")
    if not command.strip():
        return _failed(request, "EMPTY_COMMAND", "process.run requires a non-empty command")

    cwd = root
    if args.get("cwd"):
        cwd_rel = _confine_rel(root, str(args["cwd"]))
        cwd = root / cwd_rel
        if not cwd.is_dir():
            return _failed(request, "CWD_NOT_FOUND", str(cwd_rel))

    timeout = min(int(args.get("timeout_seconds", 30)), 120)

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return _ok(
            request,
            changed=False,
            observation={
                "command": command,
                "exit_code": result.returncode,
                "stdout": result.stdout[:10000],
                "stderr": result.stderr[:5000],
            },
        )
    except subprocess.TimeoutExpired:
        return _failed(request, "TIMEOUT", f"command timed out after {timeout}s")
    except OSError as exc:
        return _failed(request, "PROCESS_ERROR", str(exc))


def _execute_git(
    request: ActionRequestV1,
    operation: str,
    args: dict[str, Any],
    root: Path,
    apply_edits: bool,
) -> ActionObservationV1:
    """Execute git operations within the repository.

    Same blocking-`subprocess.run` caveat as `_execute_process` above: safe only
    because production callers reach this via `execute_action_loop` inside
    `asyncio.to_thread`. Calling it directly from async code without that wrapping
    blocks the shared event loop for up to 30s per git command.
    """
    import subprocess

    def _git(*cmd_args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", *cmd_args],
            cwd=str(root),
            capture_output=True,
            text=False,
            timeout=30,
            check=check,
        )
        def _decode(raw: bytes | None) -> str:
            if not raw:
                return ""
            return raw.decode("utf-8", errors="replace")

        return subprocess.CompletedProcess(
            args=completed.args,
            returncode=completed.returncode,
            stdout=_decode(completed.stdout),
            stderr=_decode(completed.stderr),
        )

    try:
        if operation == "status":
            r = _git("status", "--porcelain")
            return _ok(request, changed=False, observation={
                "status": r.stdout.strip(), "clean": not r.stdout.strip(),
            })

        if operation == "diff":
            cmd = ["diff"]
            if args.get("staged"):
                cmd.append("--staged")
            if args.get("ref"):
                cmd.append(str(args["ref"]))
            r = _git(*cmd)
            return _ok(request, changed=False, observation={
                "diff": r.stdout[:20000], "has_changes": bool(r.stdout.strip()),
            })

        if operation == "log":
            count = min(int(args.get("count", 10)), 50)
            cmd = ["log", f"--max-count={count}", "--oneline"]
            if args.get("ref"):
                cmd.append(str(args["ref"]))
            r = _git(*cmd)
            return _ok(request, changed=False, observation={
                "log": r.stdout.strip(), "count": count,
            })

        if operation == "branch":
            if args.get("name"):
                r = _git("rev-parse", "--abbrev-ref", "HEAD")
                return _ok(request, changed=False, observation={"branch": r.stdout.strip()})
            r = _git("branch", "--list")
            return _ok(request, changed=False, observation={"branches": r.stdout.strip()})

        if operation == "stage":
            if not apply_edits:
                return _failed(request, "EDITS_DISABLED", "staging requires apply_edits=True")
            paths = args.get("paths", [])
            if isinstance(paths, str):
                paths = [paths]
            for p in paths:
                _confine_rel(root, p)
            _git("add", "--", *[str(p) for p in paths])
            return _ok(request, changed=True, observation={"staged": list(paths)})

        if operation == "commit":
            if not apply_edits:
                return _failed(request, "EDITS_DISABLED", "committing requires apply_edits=True")
            message = str(args.get("message") or "")
            if not message.strip():
                return _failed(request, "EMPTY_COMMIT_MESSAGE", "commit requires a message")
            cmd = ["commit", "-m", message]
            if args.get("author"):
                cmd.extend(["--author", str(args["author"])])
            r = _git(*cmd)
            if r.returncode != 0:
                return _failed(request, "COMMIT_FAILED", r.stderr.strip()[:500])
            return _ok(request, changed=True, observation={
                "committed": True, "message": message, "output": r.stdout.strip()[:500],
            })

        if operation == "checkout":
            if not apply_edits:
                return _failed(request, "EDITS_DISABLED", "checkout requires apply_edits=True")
            ref = str(args.get("ref") or "")
            if not ref:
                return _failed(request, "EMPTY_REF", "checkout requires a ref")
            r = _git("checkout", ref)
            if r.returncode != 0:
                return _failed(request, "CHECKOUT_FAILED", r.stderr.strip()[:500])
            return _ok(request, changed=True, observation={"ref": ref, "checked_out": True})

        if operation == "create_branch":
            if not apply_edits:
                return _failed(request, "EDITS_DISABLED", "branch creation requires apply_edits=True")
            name = str(args.get("name") or "")
            if not name:
                return _failed(request, "EMPTY_BRANCH_NAME", "create_branch requires a name")
            cmd = ["checkout", "-b", name]
            if args.get("start_point"):
                cmd.append(str(args["start_point"]))
            r = _git(*cmd)
            if r.returncode != 0:
                return _failed(request, "BRANCH_CREATE_FAILED", r.stderr.strip()[:500])
            return _ok(request, changed=True, observation={"branch": name, "created": True})

        return _failed(request, "CAPABILITY_NOT_AVAILABLE", f"git.{operation} has no executor")

    except subprocess.TimeoutExpired:
        return _failed(request, "TIMEOUT", "git command timed out")
    except OSError as exc:
        return _failed(request, "GIT_ERROR", str(exc))


def execute_action_loop(
    requests: tuple[ActionRequestV1, ...] | list[ActionRequestV1],
    *,
    repository_id: str,
    settings: object | None = None,
    allowed_files: tuple[str, ...] = (),
    apply_edits: bool = True,
    max_actions: int = 8,
    max_mutations: int = 8,
    approved: bool = True,
    step_approval_required: bool = False,
    execution_context: "ExecutionContextV1 | None" = None,
) -> list[ActionObservationV1]:
    observations: list[ActionObservationV1] = []
    mutations = 0
    for index, request in enumerate(requests):
        if index >= max_actions:
            observations.append(
                _failed(request, "BUDGET_EXCEEDED", f"max actions {max_actions} exhausted")
            )
            break
        resolved = resolve_capability(request.capability, request.operation)
        mutates = bool(resolved and resolved[2].mutates)
        obs = execute_action(
            request,
            repository_id=repository_id,
            settings=settings,
            allowed_files=allowed_files,
            apply_edits=apply_edits,
            action_budget_remaining=max_actions - index,
            mutation_budget_remaining=max_mutations - mutations,
            approved=approved,
            step_approval_required=step_approval_required,
            execution_context=execution_context,
        )
        observations.append(obs)
        if obs.changed:
            mutations += 1
        if obs.status != "SUCCESS":
            break
        if mutates:
            mutations += 0  # already counted via changed
    return observations


def classify_action_failure(
    observation: ActionObservationV1,
    *,
    attempt_number: int = 1,
    max_attempts: int = 3,
    replans_used: int = 0,
    max_replans: int = 2,
) -> FailureClassificationV1 | None:
    """Classify a failed action observation through the deterministic failure
    taxonomy. Returns None if the observation is not a failure."""
    if observation.status == "SUCCESS":
        return None
    reason_codes: list[str] = []
    if observation.error_code:
        reason_codes.append(observation.error_code)
    if not reason_codes:
        reason_codes.append("UNKNOWN_FAILURE")
    verification = VerificationResultV1(
        verdict="FAILED",
        reason_codes=tuple(reason_codes),
    )
    return classify_failure(
        verification,
        attempt_number=attempt_number,
        max_attempts=max_attempts,
        replans_used=replans_used,
        max_replans=max_replans,
    )


__all__ = [
    "CapabilityExecutorError",
    "actions_from_step_contract",
    "observation_to_dict",
    "execute_action",
    "execute_action_loop",
    "classify_action_failure",
]
