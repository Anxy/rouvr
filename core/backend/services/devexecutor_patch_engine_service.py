from __future__ import annotations

import ast
import hashlib
import subprocess
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.services.devexecutor_pyright_lsp_service import (
    resolve_pyright_cli_command,
    validate_python_change_via_lsp,
    validate_python_changes_via_lsp,
)
from backend.services.devexecutor_readonly_tools_service import _safe_resolve
from backend.services.repo_profile_resolver_service import resolve_effective_repo_profile

try:
    from aix.contracts.ayrpack_current import SpineEntryKind  # type: ignore[import-not-found]
except ModuleNotFoundError:
    import sys

    _AIX_SRC = Path(__file__).resolve().parents[3] / "src"
    if str(_AIX_SRC) not in sys.path:
        sys.path.insert(0, str(_AIX_SRC))
    from aix.contracts.ayrpack_current import SpineEntryKind  # type: ignore[import-not-found]

PatchStatus = Literal["applied", "failed", "conflict"]
PolicyHook = Callable[[str, dict[str, Any]], None]
PostApplyValidationScope = Literal["touched_files", "full_project"]
PyrightRunner = Callable[..., Any]
SemanticValidator = Callable[..., tuple[bool | None, str]]


class FileValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    passed: bool
    error_count: int = Field(0, ge=0)
    warning_count: int = Field(0, ge=0)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)


class PostApplyValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    scope: PostApplyValidationScope = "touched_files"
    touched_files: list[str] = Field(default_factory=list)
    validation_targets: list[str] = Field(default_factory=list)
    file_results: list[FileValidationResult] = Field(default_factory=list)
    output: str = ""


class PatchApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: PatchStatus
    reason_code: str = Field(..., min_length=1)
    patch_sha1: str = Field(..., min_length=1)
    changed_files: list[str] = Field(default_factory=list)
    patch_bytes: int = Field(..., ge=0)
    approx_diff_bytes: int = Field(..., ge=0)
    patch_attempt: int = Field(..., ge=1)
    policy_violations: list[str] = Field(default_factory=list)
    retry_count: int = Field(0, ge=0)
    retry_strategy: str = "none"
    stderr: str = ""
    stdout: str = ""
    pyright_passed: bool | None = None
    pyright_output: str = ""
    post_apply_validation_scope: PostApplyValidationScope = "touched_files"
    post_apply_validation_targets: list[str] = Field(default_factory=list)
    post_apply_validation_results: list[FileValidationResult] = Field(default_factory=list)
    semantic_precheck_passed: bool | None = None
    semantic_precheck_output: str = ""


class DriftGuardPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_files_changed: int = Field(25, ge=1, le=500)
    max_diff_bytes: int = Field(500_000, ge=1)
    blocked_path_prefixes: list[str] = Field(default_factory=list)


class DriftGuardPolicyError(PermissionError):
    pass


class PatchAttemptBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(10, ge=1, le=100)
    attempts_used: int = Field(0, ge=0)

    def consume(self) -> int:
        next_attempt = self.attempts_used + 1
        if next_attempt > self.max_attempts:
            raise RuntimeError(f"PATCH_ATTEMPT_LIMIT_EXCEEDED attempts={next_attempt} max_attempts={self.max_attempts}")
        self.attempts_used = next_attempt
        return next_attempt


def _emit(hook: PolicyHook | None, event: str, payload: dict[str, Any]) -> None:
    if hook is None:
        return
    hook(event, payload)


def classify_patch_failure(stderr: str, stdout: str = "") -> tuple[PatchStatus, str, bool]:
    err = f"{stderr}\n{stdout}".strip().lower()
    if "not a git repository" in err:
        return ("failed", "not_git_repo", False)
    if "corrupt patch" in err or "malformed patch" in err:
        return ("failed", "malformed_patch", False)
    if "does not exist in index" in err:
        return ("failed", "path_missing_in_index", False)
    if "already exists in working directory" in err:
        return ("failed", "path_conflict", False)
    if "patch failed" in err or "does not apply" in err:
        return ("conflict", "hunk_conflict", True)
    return ("failed", "apply_failed", False)


def _extract_changed_files(patch_text: str) -> list[str]:
    changed_files: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            changed_files.append(line.replace("+++ b/", "", 1).strip())
    return changed_files


def _estimate_diff_bytes(patch_text: str) -> int:
    total = 0
    for line in patch_text.splitlines():
        if line.startswith(("+++", "---", "@@", "diff --git")):
            continue
        if line.startswith("+") or line.startswith("-"):
            total += len(line.encode("utf-8"))
    return total


def _append_ayrpack_attestation(
    *,
    execution_context: dict[str, Any] | None,
    status: str,
    summary: str,
    changed_files: list[str],
    output: str,
    validation_result: PostApplyValidationResult | None = None,
) -> None:
    if not isinstance(execution_context, dict):
        return
    packet = execution_context.get("ayrpack")
    if not isinstance(packet, dict):
        return
    raw_spine = packet.get("spine") or []
    spine = list(raw_spine)
    next_index = max((int(item.get("index", 0)) for item in spine), default=-1) + 1
    spine.append(
        {
            "entry_id": f"spine-{uuid4().hex}",
            "index": next_index,
            "kind": SpineEntryKind.ATTESTATION,
            "timestamp_utc": datetime.now(UTC).isoformat(),
            "phase": "validation",
            "status": status,
            "actor": {"kind": "system", "id": "devexecutor.patch_engine.pyright"},
            "title": "pyright touched-file validation",
            "summary": summary,
            "artifact_ref": "local://pyright",
            "evidence_ref": output[:4000] if output else "",
            "details": (
                {
                    "touched_files": list(changed_files),
                    "validation_scope": validation_result.scope,
                    "validation_targets": list(validation_result.validation_targets),
                    "validation_results": [item.model_dump(mode="python") for item in validation_result.file_results],
                }
                if validation_result is not None
                else {"touched_files": list(changed_files)}
            ),
        }
    )
    packet.update({"spine": spine})
    execution_context["ayrpack"] = packet


def _run_pyright_lsp_precheck(
    *,
    root: Path,
    prospective_files: dict[str, str],
) -> tuple[bool | None, str]:
    python_files = {path: content for path, content in prospective_files.items() if str(path).endswith(".py")}
    if not python_files:
        return True, ""
    errors: list[str] = []
    available = False
    updates: list[tuple[str | Path, str, str]] = []
    relative_by_resolved_path: dict[str, str] = {}
    for relative_path, updated_text in python_files.items():
        target = _safe_resolve(root, relative_path)
        original_text = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        updates.append((target, original_text, updated_text))
        relative_by_resolved_path[str(target.resolve())] = relative_path
    if len(updates) == 1:
        single_target, original_text, updated_text = updates[0]
        target = Path(single_target)
        batch_results = {
            str(target.resolve()): validate_python_change_via_lsp(
                workspace_root=root,
                file_path=target,
                original_text=original_text,
                updated_text=updated_text,
            )
        }
    else:
        batch_results = validate_python_changes_via_lsp(workspace_root=root, updates=updates)
    for resolved_path, result in batch_results.items():
        if not result.available:
            continue
        available = True
        if not result.passed:
            relative_path = relative_by_resolved_path.get(resolved_path, Path(resolved_path).name)
            errors.append(f"{relative_path}: {result.output}".strip())
    shadow_validation = _run_shadow_pyright_precheck(root=root, prospective_files=python_files)
    if shadow_validation is not None:
        available = True
        if not shadow_validation.passed:
            errors.append(shadow_validation.output.strip())
    if not available:
        return None, "pyright_lsp_unavailable"
    return (not errors), "\n".join(item for item in errors if item).strip()


def _run_shadow_pyright_precheck(
    *,
    root: Path,
    prospective_files: dict[str, str],
) -> PostApplyValidationResult | None:
    touched_python_files = [
        path.replace("\\", "/") for path in prospective_files.keys() if str(path).strip().endswith(".py")
    ]
    if not touched_python_files or not resolve_pyright_cli_command():
        return None
    with tempfile.TemporaryDirectory(prefix="aix-pyright-precheck-") as temp_dir:
        shadow_root = Path(temp_dir)
        shadow_files = _build_shadow_python_workspace(root=root, prospective_files=prospective_files)
        for relative_path, content in shadow_files.items():
            shadow_target = shadow_root / relative_path
            shadow_target.parent.mkdir(parents=True, exist_ok=True)
            shadow_target.write_text(content, encoding="utf-8")
        for config_name in ("pyrightconfig.json", "pyproject.toml", "setup.cfg"):
            source = root / config_name
            if source.exists() and source.is_file():
                target = shadow_root / config_name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
        command = [*resolve_pyright_cli_command(), "--outputjson", *touched_python_files]
        proc = subprocess.run(
            command,
            cwd=str(shadow_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        combined = "\n".join(
            part for part in [(proc.stdout or "").strip(), (proc.stderr or "").strip()] if part
        ).strip()
        return _parse_pyright_validation_output(
            output=combined,
            validation_targets=touched_python_files,
            scope="touched_files",
            touched_files=touched_python_files,
            passed=proc.returncode == 0,
        )


def _build_shadow_python_workspace(
    *,
    root: Path,
    prospective_files: dict[str, str],
) -> dict[str, str]:
    included: dict[str, str] = {}
    pending = [path.replace("\\", "/") for path in prospective_files.keys() if str(path).strip().endswith(".py")]
    while pending:
        relative_path = pending.pop()
        normalized_path = relative_path.replace("\\", "/")
        if normalized_path in included:
            continue
        if normalized_path in prospective_files:
            source_text = prospective_files[normalized_path]
        else:
            target = _safe_resolve(root, normalized_path)
            if not target.exists() or not target.is_file():
                continue
            source_text = target.read_text(encoding="utf-8", errors="replace")
        included[normalized_path] = source_text
        for dependency in _discover_local_python_dependencies(
            root=root, relative_path=normalized_path, source_text=source_text
        ):
            if dependency not in included:
                pending.append(dependency)
    return included


def _discover_local_python_dependencies(
    *,
    root: Path,
    relative_path: str,
    source_text: str,
) -> list[str]:
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []
    discovered: list[str] = []
    current_parent = Path(relative_path).parent
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                candidate = _resolve_local_module_to_path(
                    root=root, current_parent=current_parent, module_name=alias.name, level=0
                )
                if candidate:
                    discovered.append(candidate)
        elif isinstance(node, ast.ImportFrom):
            module_name = str(node.module or "").strip()
            candidate = _resolve_local_module_to_path(
                root=root,
                current_parent=current_parent,
                module_name=module_name,
                level=int(node.level or 0),
            )
            if candidate:
                discovered.append(candidate)
            for alias in node.names:
                alias_name = str(alias.name or "").strip()
                if not alias_name or alias_name == "*":
                    continue
                submodule = ".".join(part for part in (module_name, alias_name) if part)
                candidate = _resolve_local_module_to_path(
                    root=root,
                    current_parent=current_parent,
                    module_name=submodule,
                    level=int(node.level or 0),
                )
                if candidate:
                    discovered.append(candidate)
    return list(dict.fromkeys(item for item in discovered if item))


def _resolve_local_module_to_path(
    *,
    root: Path,
    current_parent: Path,
    module_name: str,
    level: int,
) -> str:
    module_parts = [part for part in str(module_name or "").split(".") if part]
    candidate_bases: list[Path] = []
    if level > 0:
        base = current_parent
        for _ in range(max(level - 1, 0)):
            base = base.parent
        candidate_bases.append(base)
    candidate_bases.append(Path())
    for base in candidate_bases:
        module_path = (base / Path(*module_parts)) if module_parts else base
        for candidate in (module_path.with_suffix(".py"), module_path / "__init__.py"):
            if not candidate.parts:
                continue
            resolved = (root / candidate).resolve()
            try:
                relative = resolved.relative_to(root.resolve())
            except ValueError:
                continue
            if resolved.exists() and resolved.is_file():
                return relative.as_posix()
    return ""


def _parse_pyright_validation_output(
    *,
    output: str,
    validation_targets: list[str],
    scope: PostApplyValidationScope,
    touched_files: list[str],
    passed: bool,
) -> PostApplyValidationResult:
    diagnostics_by_file: dict[str, list[dict[str, Any]]] = {path: [] for path in validation_targets}
    if output.strip():
        try:
            import json

            payload = json.loads(output)
            for diagnostic in list(payload.get("generalDiagnostics") or []):
                raw_path = str(diagnostic.get("file") or "").replace("\\", "/").strip()
                normalized_path = raw_path
                if raw_path:
                    try:
                        candidate = Path(raw_path)
                        if candidate.is_absolute():
                            normalized_path = candidate.as_posix()
                    except (TypeError, ValueError):
                        normalized_path = raw_path
                matched_path = ""
                for target in validation_targets:
                    if normalized_path == target or normalized_path.endswith(f"/{target}"):
                        matched_path = target
                        break
                bucket = matched_path or normalized_path or "<pyright>"
                diagnostics_by_file.setdefault(bucket, []).append(dict(diagnostic))
        except Exception:
            diagnostics_by_file.setdefault("<pyright>", []).append(
                {"severity": "error" if not passed else "information", "message": output.strip()}
            )

    file_results: list[FileValidationResult] = []
    ordered_paths = list(dict.fromkeys([*validation_targets, *diagnostics_by_file.keys()]))
    for path in ordered_paths:
        diagnostics = list(diagnostics_by_file.get(path) or [])
        error_count = 0
        warning_count = 0
        for item in diagnostics:
            severity = str(item.get("severity") or "").lower()
            if severity == "warning":
                warning_count += 1
                continue
            if severity in {"information", "info", "hint"}:
                continue
            error_count += 1
        file_results.append(
            FileValidationResult(
                path=path,
                passed=error_count == 0,
                error_count=error_count,
                warning_count=warning_count,
                diagnostics=diagnostics,
            )
        )

    return PostApplyValidationResult(
        passed=passed,
        scope=scope,
        touched_files=list(touched_files),
        validation_targets=list(validation_targets),
        file_results=file_results,
        output=output,
    )


def _run_pyright_on_files(
    *,
    root: Path,
    changed_files: list[str],
    validation_scope: PostApplyValidationScope = "touched_files",
    additional_targets: list[str] | None = None,
) -> PostApplyValidationResult:
    touched_files = list(dict.fromkeys(path.replace("\\", "/") for path in changed_files if str(path).strip()))
    extra_targets = [path.replace("\\", "/") for path in list(additional_targets or []) if str(path).strip()]
    validation_targets = (
        [] if validation_scope == "full_project" else list(dict.fromkeys([*touched_files, *extra_targets]))
    )
    if validation_scope == "touched_files" and not validation_targets:
        return PostApplyValidationResult(
            passed=True,
            scope=validation_scope,
            touched_files=touched_files,
            validation_targets=[],
            file_results=[],
            output="",
        )
    command = [*resolve_pyright_cli_command(), "--outputjson"]
    if validation_scope == "touched_files":
        command.extend(validation_targets)
    proc = subprocess.run(
        command,
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    combined = "\n".join(part for part in [(proc.stdout or "").strip(), (proc.stderr or "").strip()] if part).strip()
    return _parse_pyright_validation_output(
        output=combined,
        validation_targets=validation_targets,
        scope=validation_scope,
        touched_files=touched_files,
        passed=proc.returncode == 0,
    )


def _restore_files_from_git(*, root: Path, changed_files: list[str]) -> None:
    if not changed_files:
        return
    subprocess.run(
        ["git", "-C", str(root), "checkout", "--", *changed_files],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _capture_file_bytes(*, root: Path, changed_files: list[str]) -> dict[str, bytes | None]:
    captured: dict[str, bytes | None] = {}
    for relative_path in changed_files:
        target = _safe_resolve(root, relative_path)
        captured[relative_path] = target.read_bytes() if target.exists() else None
    return captured


def _assert_files_restored(
    *,
    root: Path,
    original_bytes: dict[str, bytes | None],
) -> None:
    for relative_path, expected in original_bytes.items():
        target = _safe_resolve(root, relative_path)
        current = target.read_bytes() if target.exists() else None
        if current != expected:
            raise AssertionError(
                f"SEMANTIC_REJECT_LEFT_MUTATED_BYTES path={relative_path} expected_bytes_changed_back={expected is not None}"
            )


def _build_prospective_file_contents(*, root: Path, patch_text: str) -> dict[str, str]:
    per_file_patches = _split_patch_by_file(patch_text)
    prospective: dict[str, str] = {}
    for relative_path, file_patch in per_file_patches.items():
        target = _safe_resolve(root, relative_path)
        original_text = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        prospective[relative_path] = _apply_unified_patch_to_text(original_text=original_text, patch_text=file_patch)
    return prospective


def _split_patch_by_file(patch_text: str) -> dict[str, str]:
    files: dict[str, list[str]] = {}
    current_path = ""
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            current_path = ""
            continue
        if line.startswith("+++ b/"):
            current_path = line.replace("+++ b/", "", 1).strip()
            files.setdefault(current_path, [])
        if current_path:
            files[current_path].append(line)
    return {path: "\n".join(lines) + "\n" for path, lines in files.items() if path}


def _apply_unified_patch_to_text(*, original_text: str, patch_text: str) -> str:
    lines = original_text.splitlines(keepends=True)
    output: list[str] = []
    source_index = 0
    patch_lines = patch_text.splitlines()
    i = 0
    while i < len(patch_lines):
        line = patch_lines[i]
        if not line.startswith("@@"):
            i += 1
            continue
        old_start = _parse_hunk_old_start(line)
        while source_index < max(old_start - 1, 0):
            output.append(lines[source_index])
            source_index += 1
        i += 1
        while i < len(patch_lines):
            hunk_line = patch_lines[i]
            if hunk_line.startswith("@@"):
                break
            if hunk_line.startswith("\\ No newline at end of file"):
                i += 1
                continue
            prefix = hunk_line[:1]
            payload = hunk_line[1:]
            if prefix == " ":
                output.append(lines[source_index] if source_index < len(lines) else f"{payload}\n")
                source_index += 1
            elif prefix == "-":
                source_index += 1
            elif prefix == "+":
                output.append(f"{payload}\n")
            i += 1
    output.extend(lines[source_index:])
    return "".join(output)


def _parse_hunk_old_start(header: str) -> int:
    old_range = header.split(" ", 2)[1]
    old_start = old_range[1:].split(",", 1)[0]
    return max(int(old_start or "1"), 1)


def build_drift_policy_from_repo_profile(
    repo_profile: dict[str, Any] | None,
    *,
    default_max_files_changed: int = 25,
    default_max_diff_bytes: int = 500_000,
) -> DriftGuardPolicy:
    profile = dict(repo_profile or {})
    raw_node = profile.get("drift_guards")
    node: dict[str, Any] = raw_node if isinstance(raw_node, dict) else {}
    return DriftGuardPolicy(
        max_files_changed=int(node.get("max_files_changed", default_max_files_changed)),
        max_diff_bytes=int(node.get("max_diff_bytes", default_max_diff_bytes)),
        blocked_path_prefixes=[str(x).strip("/\\") for x in list(node.get("blocked_path_prefixes", [])) if str(x)],
    )


def evaluate_drift_guards(
    *,
    patch_text: str,
    drift_policy: DriftGuardPolicy,
) -> dict[str, Any]:
    changed_files = _extract_changed_files(patch_text)
    approx_diff_bytes = _estimate_diff_bytes(patch_text)
    violations: list[str] = []
    if len(set(changed_files)) > drift_policy.max_files_changed:
        violations.append(
            f"MAX_FILES_CHANGED_EXCEEDED files={len(set(changed_files))} max={drift_policy.max_files_changed}"
        )
    if approx_diff_bytes > drift_policy.max_diff_bytes:
        violations.append(f"MAX_DIFF_BYTES_EXCEEDED diff_bytes={approx_diff_bytes} max={drift_policy.max_diff_bytes}")

    blocked_prefixes = [p.lower() for p in drift_policy.blocked_path_prefixes if p]
    for path in changed_files:
        normalized = path.strip("/\\").lower()
        for blocked in blocked_prefixes:
            if normalized == blocked or normalized.startswith(f"{blocked}/") or normalized.startswith(f"{blocked}\\"):
                violations.append(f"BLOCKED_PATH path={path} blocked_prefix={blocked}")
                break

    return {
        "changed_files": changed_files,
        "approx_diff_bytes": approx_diff_bytes,
        "violations": violations,
    }


def apply_patch_with_outcome(
    *,
    workspace_root: str | Path,
    patch_text: str,
    drift_policy: DriftGuardPolicy | None = None,
    repo_profile: dict[str, Any] | None = None,
    repo_key: str | None = None,
    explicit_profile_id: str | None = None,
    profiles_dir: str | Path | None = None,
    patch_budget: PatchAttemptBudget | None = None,
    max_conflict_retries: int = 1,
    policy_hook: PolicyHook | None = None,
    execution_context: dict[str, Any] | None = None,
    pyright_runner: PyrightRunner | None = None,
    semantic_validator: SemanticValidator | None = None,
    post_apply_validation_scope: PostApplyValidationScope = "touched_files",
    contract_validation_targets: list[str] | None = None,
) -> PatchApplyResult:
    root = _safe_resolve(workspace_root, ".")
    patch_sha1 = hashlib.sha1(patch_text.encode("utf-8")).hexdigest()
    budget = patch_budget or PatchAttemptBudget(max_attempts=10, attempts_used=0)
    effective_repo_profile = repo_profile
    if drift_policy is None and effective_repo_profile is None and repo_key and profiles_dir:
        profile, _diag = resolve_effective_repo_profile(
            repo_key=repo_key,
            explicit_profile_id=explicit_profile_id,
            profiles_dir=profiles_dir,
        )
        effective_repo_profile = profile.model_dump(mode="python")
    active_policy = drift_policy or build_drift_policy_from_repo_profile(effective_repo_profile)
    guard_result = evaluate_drift_guards(patch_text=patch_text, drift_policy=active_policy)
    changed_files = list(guard_result["changed_files"])
    original_file_bytes = _capture_file_bytes(root=root, changed_files=changed_files)
    approx_diff_bytes = int(guard_result["approx_diff_bytes"])
    violations = list(guard_result["violations"])
    if violations:
        guard_attempt = max(budget.attempts_used + 1, 1)
        _emit(
            policy_hook,
            "patch_blocked_by_drift_guard",
            {"patch_attempt": guard_attempt, "patch_sha1": patch_sha1, "violations": violations},
        )
        return PatchApplyResult(
            status="failed",
            reason_code="drift_guard_violation",
            patch_sha1=patch_sha1,
            changed_files=changed_files,
            patch_bytes=len(patch_text.encode("utf-8")),
            approx_diff_bytes=approx_diff_bytes,
            patch_attempt=guard_attempt,
            policy_violations=violations,
            retry_count=0,
            stderr="; ".join(violations),
            stdout="",
            post_apply_validation_scope=post_apply_validation_scope,
        )
    prospective_files = _build_prospective_file_contents(root=root, patch_text=patch_text)
    validator = semantic_validator or _run_pyright_lsp_precheck
    semantic_passed, semantic_output = validator(root=root, prospective_files=prospective_files)
    if semantic_passed is False:
        _append_ayrpack_attestation(
            execution_context=execution_context,
            status="failed",
            summary="pyright LSP pre-apply semantic validation failed",
            changed_files=changed_files,
            output=semantic_output,
        )
        return PatchApplyResult(
            status="failed",
            reason_code="pyright_precheck_failed",
            patch_sha1=patch_sha1,
            changed_files=changed_files,
            patch_bytes=len(patch_text.encode("utf-8")),
            approx_diff_bytes=approx_diff_bytes,
            patch_attempt=max(budget.attempts_used + 1, 1),
            retry_count=0,
            stderr=semantic_output,
            stdout="",
            semantic_precheck_passed=False,
            semantic_precheck_output=semantic_output,
            post_apply_validation_scope=post_apply_validation_scope,
        )
    if semantic_passed is True:
        _append_ayrpack_attestation(
            execution_context=execution_context,
            status="passed",
            summary="pyright LSP pre-apply semantic validation passed",
            changed_files=changed_files,
            output=semantic_output,
        )

    max_retries = max(0, int(max_conflict_retries))
    retry_count = 0
    attempts_total = max_retries + 1
    strategy = "apply_then_3way" if max_retries > 0 else "single_apply"
    for local_attempt in range(attempts_total):
        try:
            attempt = budget.consume()
        except RuntimeError as exc:
            return PatchApplyResult(
                status="failed",
                reason_code="attempt_budget_exceeded",
                patch_sha1=patch_sha1,
                patch_bytes=len(patch_text.encode("utf-8")),
                approx_diff_bytes=approx_diff_bytes,
                patch_attempt=max(budget.attempts_used + 1, 1),
                policy_violations=[str(exc)],
                changed_files=changed_files,
                stderr=str(exc),
                stdout="",
                retry_count=retry_count,
                retry_strategy=strategy,
                post_apply_validation_scope=post_apply_validation_scope,
            )

        command = [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.safecrlf=false",
            "-C",
            str(root),
            "apply",
        ]
        if local_attempt > 0:
            command.append("--3way")
        command.extend(["--ignore-space-change", "--ignore-whitespace", "--whitespace=nowarn", "-"])
        _emit(
            policy_hook,
            "pre_apply_patch",
            {
                "patch_attempt": attempt,
                "retry_index": local_attempt,
                "patch_sha1": patch_sha1,
                "patch_bytes": len(patch_text.encode("utf-8")),
                "changed_files": changed_files,
                "approx_diff_bytes": approx_diff_bytes,
                "command": command,
            },
        )
        proc = subprocess.run(
            command,
            input=patch_text,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            runner = pyright_runner or _run_pyright_on_files
            validation_result_raw = runner(
                root=root,
                changed_files=changed_files,
                validation_scope=post_apply_validation_scope,
                additional_targets=contract_validation_targets,
            )
            if isinstance(validation_result_raw, PostApplyValidationResult):
                validation_result = validation_result_raw
            elif isinstance(validation_result_raw, tuple):
                legacy_passed, legacy_output = validation_result_raw
                validation_targets = (
                    []
                    if post_apply_validation_scope == "full_project"
                    else list(
                        dict.fromkeys(
                            [
                                *(path.replace("\\", "/") for path in changed_files if str(path).strip()),
                                *(
                                    path.replace("\\", "/")
                                    for path in list(contract_validation_targets or [])
                                    if str(path).strip()
                                ),
                            ]
                        )
                    )
                )
                validation_result = _parse_pyright_validation_output(
                    output=str(legacy_output or ""),
                    validation_targets=validation_targets,
                    scope=post_apply_validation_scope,
                    touched_files=list(
                        dict.fromkeys(path.replace("\\", "/") for path in changed_files if str(path).strip())
                    ),
                    passed=bool(legacy_passed),
                )
            else:
                raise TypeError("pyright_runner must return PostApplyValidationResult or tuple[bool, str]")
            pyright_passed = validation_result.passed
            pyright_output = validation_result.output
            if not pyright_passed:
                _restore_files_from_git(root=root, changed_files=changed_files)
                _assert_files_restored(root=root, original_bytes=original_file_bytes)
                _append_ayrpack_attestation(
                    execution_context=execution_context,
                    status="failed",
                    summary="pyright reported errors on touched files; patch was reverted",
                    changed_files=changed_files,
                    output=pyright_output,
                    validation_result=validation_result,
                )
                return PatchApplyResult(
                    status="failed",
                    reason_code="pyright_failed",
                    patch_sha1=patch_sha1,
                    changed_files=changed_files,
                    patch_bytes=len(patch_text.encode("utf-8")),
                    approx_diff_bytes=approx_diff_bytes,
                    patch_attempt=attempt,
                    stderr=pyright_output,
                    stdout=proc.stdout or "",
                    retry_count=retry_count,
                    retry_strategy=strategy,
                    semantic_precheck_passed=semantic_passed,
                    semantic_precheck_output=semantic_output,
                    pyright_passed=False,
                    pyright_output=pyright_output,
                    post_apply_validation_scope=validation_result.scope,
                    post_apply_validation_targets=validation_result.validation_targets,
                    post_apply_validation_results=validation_result.file_results,
                )
            _append_ayrpack_attestation(
                execution_context=execution_context,
                status="passed",
                summary="pyright reported no errors on touched files",
                changed_files=changed_files,
                output=pyright_output,
                validation_result=validation_result,
            )
            _emit(
                policy_hook,
                "post_apply_patch",
                {"patch_attempt": attempt, "patch_sha1": patch_sha1, "changed_files": changed_files},
            )
            return PatchApplyResult(
                status="applied",
                reason_code="ok" if retry_count == 0 else "ok_after_retry",
                patch_sha1=patch_sha1,
                changed_files=changed_files,
                patch_bytes=len(patch_text.encode("utf-8")),
                approx_diff_bytes=approx_diff_bytes,
                patch_attempt=attempt,
                stderr=proc.stderr or "",
                stdout=proc.stdout or "",
                retry_count=retry_count,
                retry_strategy=strategy,
                semantic_precheck_passed=semantic_passed,
                semantic_precheck_output=semantic_output,
                pyright_passed=True,
                pyright_output=pyright_output,
                post_apply_validation_scope=validation_result.scope,
                post_apply_validation_targets=validation_result.validation_targets,
                post_apply_validation_results=validation_result.file_results,
            )

        status, reason_code, retryable = classify_patch_failure(proc.stderr or "", proc.stdout or "")
        terminal = (not retryable) or (retry_count >= max_retries)
        _emit(
            policy_hook,
            "patch_failed",
            {
                "patch_attempt": attempt,
                "retry_index": local_attempt,
                "patch_sha1": patch_sha1,
                "status": status,
                "reason_code": reason_code,
                "retryable": retryable,
                "terminal": terminal,
                "stderr": proc.stderr or "",
                "stdout": proc.stdout or "",
            },
        )
        if terminal:
            terminal_reason = reason_code if status != "conflict" else f"{reason_code}_terminal"
            return PatchApplyResult(
                status=status,
                reason_code=terminal_reason,
                patch_sha1=patch_sha1,
                changed_files=changed_files,
                patch_bytes=len(patch_text.encode("utf-8")),
                approx_diff_bytes=approx_diff_bytes,
                patch_attempt=attempt,
                stderr=proc.stderr or "",
                stdout=proc.stdout or "",
                retry_count=retry_count,
                retry_strategy=strategy,
                semantic_precheck_passed=semantic_passed,
                semantic_precheck_output=semantic_output,
                post_apply_validation_scope=post_apply_validation_scope,
            )
        retry_count += 1

    return PatchApplyResult(
        status="failed",
        reason_code="apply_failed",
        patch_sha1=patch_sha1,
        changed_files=changed_files,
        patch_bytes=len(patch_text.encode("utf-8")),
        approx_diff_bytes=approx_diff_bytes,
        patch_attempt=max(budget.attempts_used, 1),
        stderr="retry loop exhausted unexpectedly",
        stdout="",
        retry_count=retry_count,
        retry_strategy=strategy,
        semantic_precheck_passed=semantic_passed,
        semantic_precheck_output=semantic_output,
        post_apply_validation_scope=post_apply_validation_scope,
    )
