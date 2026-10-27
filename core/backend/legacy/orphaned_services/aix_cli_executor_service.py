from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Protocol

import httpx

from backend.hyeve_aix_service.config import HyeveAixServiceSettings, settings
from backend.legacy.orphaned_services.aix_command_identity_service import command_from_argv, command_from_shell
from backend.services.aix_model_ingress_artifact_service import persist_model_ingress_presend_artifacts
from backend.services.aix_model_ingress_gate_service import (
    CLI_EXECUTOR_INGRESS_SCHEMA,
    MODEL_INGRESS_MAX_PROMPT_TOKENS,
    ModelIngressGateError,
    compile_model_ingress_request,
    estimate_prompt_tokens,
)

DOCS_OUTPUT_SUFFIXES = {".md", ".mdx", ".txt"}


class CliExecutorError(RuntimeError):
    pass


class CliBackendNotFoundError(CliExecutorError):
    pass


@dataclass(frozen=True)
class CliExecutorConfig:
    enabled: bool = True
    backend: str = "codex"
    command: str = "codex"
    sandbox_mode: str = "workspace-write"
    working_dir_policy: str = "proposal_workspace"
    require_clean_git_before_start: bool = True
    require_diff_after_run: bool = True
    timeout_seconds: int = 1800
    max_attempts: int = 1

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> CliExecutorConfig:
        data = dict(payload or {})
        return cls(
            enabled=bool(data.get("enabled", True)),
            backend=str(data.get("backend") or "codex").strip().lower() or "codex",
            command=str(data.get("command") or "codex").strip() or "codex",
            sandbox_mode=str(data.get("sandbox_mode") or data.get("sandbox") or "workspace-write").strip()
            or "workspace-write",
            working_dir_policy=str(data.get("working_dir_policy") or "proposal_workspace").strip()
            or "proposal_workspace",
            require_clean_git_before_start=bool(data.get("require_clean_git_before_start", True)),
            require_diff_after_run=bool(data.get("require_diff_after_run", True)),
            timeout_seconds=max(1, int(data.get("timeout_seconds") or 1800)),
            max_attempts=max(1, int(data.get("max_attempts") or 1)),
        )

    @classmethod
    def from_settings(cls) -> CliExecutorConfig:
        return cls.from_mapping(HyeveAixServiceSettings().cli_executor)


@dataclass(frozen=True)
class CliExecutionRequest:
    prompt: str
    working_dir: str | Path
    runtime_context: Mapping[str, Any] = field(default_factory=dict)
    dry_run: bool = False


@dataclass(frozen=True)
class CliExecutionResult:
    status: str
    backend: str
    command: list[str]
    working_dir: str
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    timed_out: bool = False
    duration_ms: int = 0
    attempts: int = 1
    dry_run: bool = False
    failure_class: str | None = None
    summary: str | None = None
    runtime_context: dict[str, Any] = field(default_factory=dict)
    starting_commit: str | None = None
    changed_files: list[str] = field(default_factory=list)
    diff: str = ""
    validation_results: list[dict[str, Any]] = field(default_factory=list)
    linter_results: list[dict[str, Any]] = field(default_factory=list)
    test_results: list[dict[str, Any]] = field(default_factory=list)
    execution_timestamps: dict[str, str] = field(default_factory=dict)


class CliBackendAdapter(Protocol):
    backend_id: str

    def build_command(
        self,
        *,
        command: str,
        working_dir: Path,
        prompt: str,
        runtime_context: Mapping[str, Any],
    ) -> list[str]:
        ...

    def parse_result(
        self,
        *,
        command: Sequence[str],
        working_dir: Path,
        stdout: str,
        stderr: str,
        returncode: int | None,
        timed_out: bool,
        duration_ms: int,
        attempts: int,
        dry_run: bool,
        runtime_context: Mapping[str, Any],
    ) -> CliExecutionResult:
        ...


class CodexCliBackend:
    backend_id = "codex"

    def build_command(
        self,
        *,
        command: str,
        working_dir: Path,
        prompt: str,
        runtime_context: Mapping[str, Any],
    ) -> list[str]:
        if not str(prompt or "").strip():
            raise CliExecutorError("CLI_PROMPT_REQUIRED")
        if CLI_EXECUTOR_INGRESS_SCHEMA not in str(prompt):
            raise CliExecutorError("CLI_STDIN_SCHEMA_REQUIRED schema=aix.cli_executor_ingress.current")
        provider = str(runtime_context.get("provider") or "").strip().lower()
        if provider == "openrouter" and not bool(runtime_context.get("allow_utf_tool_calls")):
            raise CliExecutorError(
                "CLI_AGENT_RUNTIME_MODEL_INGRESS_BLOCKED "
                "reason=codex_exec_expands_compact_packet_into_tool_agent_runtime "
                "required=allow_utf_tool_calls"
            )
        estimated_tokens = estimate_prompt_tokens(prompt)
        if estimated_tokens > MODEL_INGRESS_MAX_PROMPT_TOKENS:
            raise CliExecutorError(
                "CLI_PROMPT_TOKEN_CAP_EXCEEDED "
                f"estimated_prompt_tokens={estimated_tokens} "
                f"max_prompt_tokens={MODEL_INGRESS_MAX_PROMPT_TOKENS}"
            )
        model = _codex_model_name(
            provider=provider,
            model=str(runtime_context.get("model") or "").strip(),
        )
        argv = [
            str(command or "codex").strip() or "codex",
            "--ask-for-approval",
            str(runtime_context.get("approval_policy") or "never"),
            "exec",
            "--cd",
            str(working_dir),
            "--sandbox",
            str(runtime_context.get("sandbox") or "workspace-write"),
            "--json",
            "-",
        ]
        provider_config = _codex_provider_config_args(provider=provider)
        if provider_config:
            argv[4:4] = provider_config
        if model:
            argv[4:4] = ["--model", model]
        profile = str(runtime_context.get("profile") or "").strip()
        if profile:
            argv[4:4] = ["--profile", profile]
        return argv

    def parse_result(
        self,
        *,
        command: Sequence[str],
        working_dir: Path,
        stdout: str,
        stderr: str,
        returncode: int | None,
        timed_out: bool,
        duration_ms: int,
        attempts: int,
        dry_run: bool,
        runtime_context: Mapping[str, Any],
    ) -> CliExecutionResult:
        status, failure_class = _parse_cli_status(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            timed_out=timed_out,
        )
        return CliExecutionResult(
            status=status,
            backend=self.backend_id,
            command=list(command),
            working_dir=str(working_dir),
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            timed_out=timed_out,
            duration_ms=duration_ms,
            attempts=attempts,
            dry_run=dry_run,
            failure_class=failure_class,
            summary=_codex_summary(stdout, stderr),
            runtime_context=dict(runtime_context),
        )


class DirectAixModelBackend:
    backend_id = "direct_aix"

    def build_command(
        self,
        *,
        command: str,
        working_dir: Path,
        prompt: str,
        runtime_context: Mapping[str, Any],
    ) -> list[str]:
        if not str(prompt or "").strip():
            raise CliExecutorError("CLI_PROMPT_REQUIRED")
        if CLI_EXECUTOR_INGRESS_SCHEMA not in str(prompt):
            raise CliExecutorError("CLI_STDIN_SCHEMA_REQUIRED schema=aix.cli_executor_ingress.current")
        return ["aix-direct-model", "execute", str(working_dir)]

    def parse_result(
        self,
        *,
        command: Sequence[str],
        working_dir: Path,
        stdout: str,
        stderr: str,
        returncode: int | None,
        timed_out: bool,
        duration_ms: int,
        attempts: int,
        dry_run: bool,
        runtime_context: Mapping[str, Any],
    ) -> CliExecutionResult:
        status, failure_class = _parse_cli_status(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            timed_out=timed_out,
        )
        return CliExecutionResult(
            status=status,
            backend=self.backend_id,
            command=list(command),
            working_dir=str(working_dir),
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            timed_out=timed_out,
            duration_ms=duration_ms,
            attempts=attempts,
            dry_run=dry_run,
            failure_class=failure_class,
            summary=_codex_summary(stdout, stderr),
            runtime_context=dict(runtime_context),
        )


def create_cli_backend_adapter(backend: str) -> CliBackendAdapter:
    backend_id = str(backend or "").strip().lower()
    if backend_id == "codex":
        return CodexCliBackend()
    if backend_id in {"direct", "direct_aix", "aix_direct"}:
        return DirectAixModelBackend()
    raise CliBackendNotFoundError(f"UNKNOWN_CLI_BACKEND backend={backend_id or '<empty>'}")


class CliExecutorService:
    def __init__(
        self,
        *,
        config: CliExecutorConfig | Mapping[str, Any] | None = None,
        backend: CliBackendAdapter | None = None,
    ) -> None:
        if isinstance(config, CliExecutorConfig):
            self.config = config
        elif config is None:
            self.config = CliExecutorConfig.from_settings()
        else:
            self.config = CliExecutorConfig.from_mapping(config)
        self.backend = backend or create_cli_backend_adapter(self.config.backend)

    def execute(self, request: CliExecutionRequest) -> CliExecutionResult:
        if not self.config.enabled:
            raise CliExecutorError("CLI_EXECUTOR_DISABLED")
        working_dir = Path(request.working_dir).resolve()
        self._validate_working_dir(working_dir)
        runtime_context = dict(request.runtime_context or {})
        runtime_context.setdefault("sandbox", self.config.sandbox_mode)
        stdin_payload = self._compile_stdin_payload(
            prompt=request.prompt,
            working_dir=working_dir,
            runtime_context=runtime_context,
        )
        command = self.backend.build_command(
            command=self.config.command,
            working_dir=working_dir,
            prompt=stdin_payload,
            runtime_context=runtime_context,
        )
        if request.dry_run:
            return self.backend.parse_result(
                command=command,
                working_dir=working_dir,
                stdout="",
                stderr="",
                returncode=0,
                timed_out=False,
                duration_ms=0,
                attempts=0,
                dry_run=True,
                runtime_context=runtime_context,
            )
        self._validate_proposal_workspace(working_dir)
        if self.config.require_clean_git_before_start:
            self._require_clean_git(working_dir)
        starting_commit = self._git(working_dir, "rev-parse", "HEAD")
        if self.backend.backend_id == "direct_aix":
            result = self._execute_direct_aix_model(
                command=command,
                working_dir=working_dir,
                runtime_context=runtime_context,
                started_at_utc=_utc_timestamp(),
            )
            validation_results = []
            if result.status == "COMPLETE":
                encoding_results = self._enforce_declared_docs_utf8(
                    working_dir=working_dir,
                    allowed_target_files=runtime_context.get("allowed_target_files"),
                )
                if encoding_results:
                    runtime_context["encoding_validation_results"] = encoding_results
                    result = _replace_result(result, runtime_context=runtime_context)
                if any(item.get("status") == "rejected" for item in encoding_results):
                    result = _replace_result(
                        result,
                        status="FAILED",
                        failure_class="output_encoding_invalid",
                        stderr=_append_line(result.stderr, "OUTPUT_ENCODING_INVALID"),
                    )
                else:
                    shape_results = self._validate_declared_docs_output_shape(
                        working_dir=working_dir,
                        allowed_target_files=runtime_context.get("allowed_target_files"),
                        stdout=result.stdout,
                        stderr=result.stderr,
                    )
                    if shape_results:
                        runtime_context["docs_output_shape_validation_results"] = shape_results
                        result = _replace_result(result, runtime_context=runtime_context)
                    if any(item.get("status") == "rejected" for item in shape_results):
                        result = _replace_result(
                            result,
                            status="FAILED",
                            failure_class="docs_output_shape_invalid",
                            stderr=_append_line(result.stderr, "DOCS_OUTPUT_SHAPE_INVALID"),
                        )
                    else:
                        validation_results = self._run_validation_commands(
                            working_dir=working_dir,
                            commands=runtime_context.get("validation_commands"),
                        )
            result = _with_execution_evidence(
                result,
                validation_results=validation_results,
                execution_timestamps={
                    "started_at_utc": str(result.execution_timestamps.get("started_at_utc") or ""),
                    "ended_at_utc": _utc_timestamp(),
                },
            )
            if any(int(item.get("exit_code") or 0) != 0 for item in validation_results):
                result = _replace_result(
                    result,
                    status="FAILED",
                    failure_class="validation_failed",
                    stderr=_append_line(result.stderr, "VALIDATION_FAILED"),
                )
            review = self._capture_git_review(working_dir)
            result = _with_git_review(result, starting_commit=starting_commit, review=review)
            outside_targets = _outside_target_edits(
                changed_files=result.changed_files,
                allowed_target_files=runtime_context.get("allowed_target_files"),
            )
            if outside_targets:
                return _replace_result(
                    result,
                    status="FAILED",
                    failure_class="outside_target_edit",
                    stderr=_append_line(result.stderr, f"OUTSIDE_TARGET_EDIT files={','.join(outside_targets)}"),
                )
            if (
                self.config.require_diff_after_run
                and bool(runtime_context.get("mutation_required", True))
                and result.status == "COMPLETE"
                and not result.diff.strip()
            ):
                return _replace_result(
                    result,
                    status="FAILED",
                    failure_class="no_diff_after_run",
                    stderr=_append_line(result.stderr, "NO_DIFF_AFTER_MUTATION_RUN"),
                )
            return result
        if shutil.which(command[0]) is None:
            return self.backend.parse_result(
                command=command,
                working_dir=working_dir,
                stdout="",
                stderr=f"CLI_BINARY_NOT_FOUND command={command[0]}",
                returncode=None,
                timed_out=False,
                duration_ms=0,
                attempts=0,
                dry_run=False,
                runtime_context=runtime_context,
            )

        stdout = ""
        stderr = ""
        returncode: int | None = None
        timed_out = False
        started_at_utc = _utc_timestamp()
        started = monotonic()
        attempts = 0
        for attempt in range(self.config.max_attempts):
            attempts = attempt + 1
            executor_env = dict(os.environ)
            executor_env["PYTHONDONTWRITEBYTECODE"] = "1"
            try:
                proc = subprocess.run(
                    command,
                    cwd=str(working_dir),
                    input=stdin_payload,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=executor_env,
                    timeout=float(self.config.timeout_seconds),
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                stdout = _normalize_output(exc.stdout)
                stderr = _normalize_output(exc.stderr)
                returncode = None
                timed_out = True
                break
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            returncode = int(proc.returncode)
            break

        duration_ms = int((monotonic() - started) * 1000)
        result = self.backend.parse_result(
            command=command,
            working_dir=working_dir,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            timed_out=timed_out,
            duration_ms=duration_ms,
            attempts=attempts,
            dry_run=False,
            runtime_context=runtime_context,
        )
        validation_results = []
        if result.status == "COMPLETE":
            encoding_results = self._enforce_declared_docs_utf8(
                working_dir=working_dir,
                allowed_target_files=runtime_context.get("allowed_target_files"),
            )
            if encoding_results:
                runtime_context["encoding_validation_results"] = encoding_results
                result = _replace_result(result, runtime_context=runtime_context)
            if any(item.get("status") == "rejected" for item in encoding_results):
                result = _replace_result(
                    result,
                    status="FAILED",
                    failure_class="output_encoding_invalid",
                    stderr=_append_line(result.stderr, "OUTPUT_ENCODING_INVALID"),
                )
            else:
                shape_results = self._validate_declared_docs_output_shape(
                    working_dir=working_dir,
                    allowed_target_files=runtime_context.get("allowed_target_files"),
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
                if shape_results:
                    runtime_context["docs_output_shape_validation_results"] = shape_results
                    result = _replace_result(result, runtime_context=runtime_context)
                if any(item.get("status") == "rejected" for item in shape_results):
                    result = _replace_result(
                        result,
                        status="FAILED",
                        failure_class="docs_output_shape_invalid",
                        stderr=_append_line(result.stderr, "DOCS_OUTPUT_SHAPE_INVALID"),
                    )
                else:
                    validation_results = self._run_validation_commands(
                        working_dir=working_dir,
                        commands=runtime_context.get("validation_commands"),
                    )
        ended_at_utc = _utc_timestamp()
        result = _with_execution_evidence(
            result,
            validation_results=validation_results,
            execution_timestamps={
                "started_at_utc": started_at_utc,
                "ended_at_utc": ended_at_utc,
            },
        )
        if any(int(item.get("exit_code") or 0) != 0 for item in validation_results):
            result = _replace_result(
                result,
                status="FAILED",
                failure_class="validation_failed",
                stderr=_append_line(result.stderr, "VALIDATION_FAILED"),
            )
        review = self._capture_git_review(working_dir)
        result = _with_git_review(result, starting_commit=starting_commit, review=review)
        if _executor_claimed_validation(stdout=result.stdout, stderr=result.stderr) and not bool(
            runtime_context.get("allow_executor_side_validation")
        ):
            return _replace_result(
                result,
                status="FAILED",
                failure_class="executor_side_validation_forbidden",
                stderr=_append_line(result.stderr, "EXECUTOR_SIDE_VALIDATION_FORBIDDEN"),
            )
        outside_targets = _outside_target_edits(
            changed_files=result.changed_files,
            allowed_target_files=runtime_context.get("allowed_target_files"),
        )
        if outside_targets:
            return _replace_result(
                result,
                status="FAILED",
                failure_class="outside_target_edit",
                stderr=_append_line(result.stderr, f"OUTSIDE_TARGET_EDIT files={','.join(outside_targets)}"),
            )
        mutation_required = bool(runtime_context.get("mutation_required", True))
        if (
            self.config.require_diff_after_run
            and mutation_required
            and result.status == "COMPLETE"
            and not result.diff.strip()
        ):
            return _replace_result(
                result,
                status="FAILED",
                failure_class="no_diff_after_run",
                stderr=_append_line(result.stderr, "NO_DIFF_AFTER_MUTATION_RUN"),
            )
        return result

    def _compile_stdin_payload(
        self,
        *,
        prompt: str,
        working_dir: Path,
        runtime_context: dict[str, Any],
    ) -> str:
        raw_prompt = str(prompt or "").strip()
        if not raw_prompt:
            raise CliExecutorError("CLI_PROMPT_REQUIRED")
        direct_model = self.backend.backend_id == "direct_aix"
        if self.backend.backend_id not in {"codex", "direct_aix"}:
            return raw_prompt
        system_instruction = (
            "Return only compact JSON with keys status, summary, and actions. "
            "status must be COMPLETE, NEEDS_INPUT, or FAILED. "
            "Supported actions: "
            '{"type":"write_file","path":"repo-relative path","content":"text"} or '
            '{"type":"append_file","path":"repo-relative path","content":"text"}. '
            "Use only declared target files and include no markdown. "
            "Do not run validation commands or report inability to run them; AIX runs validation after applying "
            "returned file actions."
            if direct_model
            else (
                "Execute only the compact CLI executor JSON task. Do not expand hidden context. "
                "For .md, .mdx, and .txt outputs, use a UTF-8-safe file writer such as Python pathlib "
                "Path.write_text(..., encoding=\"utf-8\"). Do not use shell echo, Set-Content, Out-File, "
                "redirection operators, or PowerShell default redirection for docs outputs. Preserve normal "
                "sentence and paragraph shape; do not split prose into one word per line."
            )
        )
        context: dict[str, Any] = {
            "sandbox": str(runtime_context.get("sandbox") or self.config.sandbox_mode),
            "working_dir_policy": self.config.working_dir_policy,
        }
        allowed_target_files = [
            str(item).strip() for item in list(runtime_context.get("allowed_target_files") or []) if str(item).strip()
        ]
        validation_commands = [
            str(item).strip() for item in list(runtime_context.get("validation_commands") or []) if str(item).strip()
        ]
        if allowed_target_files:
            context["allowed_target_files"] = allowed_target_files
        if validation_commands:
            context["post_execution_check_owner"] = "AIX_ONLY"
        actions = ["mutate_declared_scope"]
        params = {
            "instruction": raw_prompt,
            "working_dir": working_dir.as_posix(),
        }
        if direct_model:
            params = {
                "instruction": "Mutate only the declared target files to satisfy the task objective.",
                "working_dir": working_dir.as_posix(),
            }
            for key in ("task_number", "project_id", "task_title", "task_description"):
                value = str(runtime_context.get(key) or "").strip()
                if value:
                    params[key] = value
        try:
            compiled = compile_model_ingress_request(
                kind="cli_executor",
                task_id=str(runtime_context.get("task_id") or runtime_context.get("task_number") or "cli-executor"),
                task_type="cli_executor_mutation",
                params=params,
                actions=actions,
                context=context,
                provider=str(runtime_context.get("provider") or "local_cli"),
                model=str(runtime_context.get("model") or "") or None,
                system_instruction=system_instruction,
                max_tokens=4096,
                temperature=0,
                allow_tool_calls=bool(runtime_context.get("allow_utf_tool_calls")),
                metadata={
                    "cli_executor": self.backend.backend_id,
                    "working_dir": working_dir.as_posix(),
                },
            )
        except ModelIngressGateError as exc:
            raise CliExecutorError(str(exc)) from exc
        metadata = dict(compiled.body.get("metadata") or {})
        artifact_refs = persist_model_ingress_presend_artifacts(
            source="cli_executor",
            request_id=compiled.request_id,
            request_body=dict(compiled.body),
            artifact=compiled.artifact,
        )
        runtime_context["cli_stdin_schema"] = CLI_EXECUTOR_INGRESS_SCHEMA
        runtime_context["cli_stdin_request_id"] = compiled.request_id
        runtime_context["cli_stdin_fingerprint"] = compiled.fingerprint
        runtime_context["cli_stdin_prompt_token_breakdown"] = metadata.get("prompt_token_breakdown")
        runtime_context["cli_stdin_schema_validation"] = "passed"
        runtime_context["cli_stdin_artifact_refs"] = artifact_refs
        runtime_context["cli_stdin_request_body"] = dict(compiled.body)
        messages = compiled.body.get("messages")
        if not isinstance(messages, list) or len(messages) < 2 or not isinstance(messages[1], dict):
            raise CliExecutorError("CLI_STDIN_COMPILE_FAILED")
        return str(messages[1].get("content") or "").strip()

    def _execute_direct_aix_model(
        self,
        *,
        command: list[str],
        working_dir: Path,
        runtime_context: dict[str, Any],
        started_at_utc: str,
    ) -> CliExecutionResult:
        request_body = dict(runtime_context.get("cli_stdin_request_body") or {})
        if not request_body:
            raise CliExecutorError("DIRECT_AIX_MODEL_REQUEST_BODY_MISSING")
        request_body = _prepare_direct_model_request(request_body, source="aix_direct_cli_executor")
        base_url = str(settings.RUNTIME_PLANNER_API_URL or "http://integration:8020").strip().rstrip("/")
        target_url = f"{base_url}/api/integrations/llm/chat"
        token = str(
            settings.RUNTIME_PLANNER_AUTH_TOKEN or settings.WORKHUB_API_TOKEN or os.getenv("HYEVE_OPERATOR_TOKEN", "")
        ).strip()
        headers = {
            "Content-Type": "application/json",
            "X-Tenant-ID": "default",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        started = monotonic()
        stdout = ""
        stderr = ""
        returncode = 0
        try:
            last_error = ""
            with httpx.Client(timeout=float(self.config.timeout_seconds)) as client:
                for attempt_index, candidate_body in enumerate(_direct_model_request_attempts(request_body)):
                    response = client.post(target_url, headers=headers, json=candidate_body)
                    if response.status_code >= 400:
                        returncode = 1
                        stderr = f"DIRECT_AIX_MODEL_HTTP_{response.status_code}: {response.text[:2000]}"
                        break
                    payload = response.json()
                    try:
                        output_text = _extract_direct_model_output_text(payload)
                        action_payload = _extract_direct_action_payload(output_text)
                    except ValueError as exc:
                        last_error = _direct_model_empty_response_summary(payload, exc)
                        if attempt_index == 0 and _should_retry_direct_model_with_slavx(payload, exc):
                            runtime_context["direct_aix_model_retry"] = {
                                "from_provider": candidate_body.get("provider"),
                                "from_model": candidate_body.get("model"),
                                "to_provider": "slavx",
                                "reason": last_error,
                            }
                            continue
                        raise ValueError(last_error) from exc
                    applied = self._apply_direct_actions(
                        working_dir=working_dir,
                        payload=action_payload,
                        allowed_target_files=runtime_context.get("allowed_target_files"),
                    )
                    status = str(action_payload.get("status") or "COMPLETE").strip().upper() or "COMPLETE"
                    summary = str(action_payload.get("summary") or "").strip() or f"applied_actions={applied}"
                    stdout = json.dumps(
                        {
                            "type": "agent_message",
                            "text": f"{status}: {summary}",
                            "direct_actions_applied": applied,
                        },
                        separators=(",", ":"),
                    )
                    if status == "FAILED":
                        returncode = 1
                    elif status == "NEEDS_INPUT":
                        returncode = 0
                    else:
                        returncode = 0
                    break
                else:
                    raise ValueError(last_error or "DIRECT_AIX_MODEL_EMPTY_RESPONSE")
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            returncode = 1
            stderr = f"DIRECT_AIX_MODEL_FAILED {type(exc).__name__}: {exc}"
        duration_ms = int((monotonic() - started) * 1000)
        result = self.backend.parse_result(
            command=command,
            working_dir=working_dir,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            timed_out=False,
            duration_ms=duration_ms,
            attempts=1,
            dry_run=False,
            runtime_context=runtime_context,
        )
        return _with_execution_evidence(
            result,
            validation_results=[],
            execution_timestamps={"started_at_utc": started_at_utc, "ended_at_utc": _utc_timestamp()},
        )

    def _apply_direct_actions(
        self,
        *,
        working_dir: Path,
        payload: dict[str, Any],
        allowed_target_files: Any,
    ) -> int:
        allowed = {
            str(item).strip().replace("\\", "/") for item in list(allowed_target_files or []) if str(item).strip()
        }
        actions = payload.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("DIRECT_AIX_MODEL_ACTIONS_REQUIRED")
        applied = 0
        for action in actions:
            if not isinstance(action, dict):
                raise ValueError("DIRECT_AIX_MODEL_ACTION_INVALID")
            action_type = str(action.get("type") or "").strip()
            if action_type not in {"write_file", "append_file"}:
                raise ValueError(f"DIRECT_AIX_MODEL_ACTION_UNSUPPORTED type={action_type or '<empty>'}")
            relative_path = str(action.get("path") or "").strip().replace("\\", "/")
            if not relative_path or relative_path.startswith("/") or ".." in Path(relative_path).parts:
                raise ValueError(f"DIRECT_AIX_MODEL_PATH_INVALID path={relative_path or '<empty>'}")
            if allowed and relative_path not in allowed:
                raise ValueError(f"DIRECT_AIX_MODEL_OUTSIDE_TARGET path={relative_path}")
            target = (working_dir / relative_path).resolve()
            try:
                target.relative_to(working_dir)
            except ValueError as exc:
                raise ValueError(f"DIRECT_AIX_MODEL_PATH_ESCAPE path={relative_path}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            content = str(action.get("content") or "")
            if action_type == "append_file":
                existing = target.read_text(encoding="utf-8") if target.exists() else ""
                prefix = "" if not existing or existing.endswith(("\n", "\r")) else "\n"
                target.write_text(f"{existing}{prefix}{content}", encoding="utf-8")
            else:
                target.write_text(content, encoding="utf-8")
            applied += 1
        return applied

    def _run_validation_commands(self, *, working_dir: Path, commands: Any) -> list[dict[str, Any]]:
        validation_commands = [
            command for command in (command_from_shell(item) for item in list(commands or [])) if command is not None
        ]
        results: list[dict[str, Any]] = []
        validation_env = dict(os.environ)
        validation_env["PYTHONDONTWRITEBYTECODE"] = "1"
        for command_identity in validation_commands:
            argv = [str(item) for item in command_identity["argv"]]
            started = monotonic()
            try:
                proc = subprocess.run(
                    argv,
                    cwd=str(working_dir),
                    shell=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=validation_env,
                    timeout=float(self.config.timeout_seconds),
                    check=False,
                )
                stdout = proc.stdout or ""
                stderr = proc.stderr or ""
                exit_code = int(proc.returncode)
            except subprocess.TimeoutExpired as exc:
                stdout = _normalize_output(exc.stdout)
                stderr = _append_line(_normalize_output(exc.stderr), "VALIDATION_TIMEOUT")
                exit_code = 124
            executed_command = command_from_argv(argv)
            results.append(
                {
                    "command": executed_command["shell"],
                    "argv": executed_command["argv"],
                    "shell": executed_command["shell"],
                    "command_text": executed_command["shell"],
                    "hash": executed_command["hash"],
                    "exit_code": exit_code,
                    "stdout": stdout,
                    "stderr": stderr,
                    "duration_ms": int((monotonic() - started) * 1000),
                }
            )
        return results

    def _validate_working_dir(self, working_dir: Path) -> None:
        if not working_dir.exists() or not working_dir.is_dir():
            raise CliExecutorError(f"CLI_WORKING_DIR_MISSING path={working_dir}")
        if self.config.working_dir_policy != "proposal_workspace":
            raise CliExecutorError(f"UNSUPPORTED_CLI_WORKING_DIR_POLICY policy={self.config.working_dir_policy}")

    def _validate_proposal_workspace(self, working_dir: Path) -> None:
        if self.config.working_dir_policy != "proposal_workspace":
            return
        marker = working_dir / ".devexecutor_proposal_workspace.json"
        if not marker.exists():
            raise CliExecutorError(f"CLI_PROPOSAL_WORKSPACE_REQUIRED marker={marker}")

    def _require_clean_git(self, working_dir: Path) -> None:
        status = self._git(working_dir, "status", "--short", allow_empty=True)
        if status.strip():
            raise CliExecutorError(f"CLI_DIRTY_WORKTREE status={status.strip()}")

    def _capture_git_review(self, working_dir: Path) -> dict[str, Any]:
        status = self._git(working_dir, "status", "--short", allow_empty=True)
        changed_files: list[str] = []
        seen: set[str] = set()
        for line in status.splitlines():
            if str(line or "").startswith("?? "):
                continue
            path = _status_path(line)
            if path and path not in seen:
                seen.add(path)
                changed_files.append(path)
        untracked_files = [
            path.strip().replace("\\", "/")
            for path in self._git(
                working_dir,
                "ls-files",
                "--others",
                "--exclude-standard",
                allow_empty=True,
            ).splitlines()
            if path.strip()
        ]
        for path in untracked_files:
            if path not in seen:
                seen.add(path)
                changed_files.append(path)
        diff_parts = [self._git(working_dir, "diff", "--binary", allow_empty=True)]
        for path in untracked_files:
            untracked_diff = self._git_diff_untracked_file(working_dir=working_dir, relative_path=path)
            if untracked_diff.strip():
                diff_parts.append(untracked_diff)
        diff = "\n".join(part.rstrip("\r\n") for part in diff_parts if part.strip())
        return {"changed_files": changed_files, "diff": diff}

    def _git_diff_untracked_file(self, *, working_dir: Path, relative_path: str) -> str:
        proc = subprocess.run(
            ["git", "diff", "--no-index", "--binary", "--", os.devnull, relative_path],
            cwd=str(working_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode not in {0, 1}:
            detail = (proc.stderr or proc.stdout or f"git diff --no-index exited {proc.returncode}").strip()
            raise CliExecutorError(f"CLI_GIT_COMMAND_FAILED git diff --no-index detail={detail}")
        return proc.stdout or ""

    def _git(self, working_dir: Path, *args: str, allow_empty: bool = False) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(working_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or f"git exited {proc.returncode}").strip()
            raise CliExecutorError(f"CLI_GIT_COMMAND_FAILED git {' '.join(args)} detail={detail}")
        output = proc.stdout or ""
        if allow_empty:
            return output.rstrip("\r\n")
        if output:
            return output.strip()
        return ""

    def _enforce_declared_docs_utf8(self, *, working_dir: Path, allowed_target_files: Any) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for raw_path in list(allowed_target_files or []):
            relative_path = str(raw_path or "").strip().replace("\\", "/").strip("/")
            if not relative_path or Path(relative_path).suffix.lower() not in DOCS_OUTPUT_SUFFIXES:
                continue
            target = (working_dir / relative_path).resolve()
            try:
                target.relative_to(working_dir)
            except ValueError:
                results.append({"path": relative_path, "status": "rejected", "failure_class": "output_path_escape"})
                continue
            if not target.exists():
                results.append({"path": relative_path, "status": "missing"})
                continue
            before = target.read_bytes()
            before_hash = _sha256_bytes(before)
            try:
                text, detected_encoding, normalized = _decode_declared_docs_text(before)
            except UnicodeDecodeError as exc:
                results.append(
                    {
                        "path": relative_path,
                        "status": "rejected",
                        "failure_class": "output_encoding_invalid",
                        "detected_encoding": "unknown",
                        "error": str(exc),
                        "before_sha256": f"sha256:{before_hash}",
                    }
                )
                continue
            if normalized:
                target.write_bytes(text.encode("utf-8"))
            after = target.read_bytes()
            results.append(
                {
                    "path": relative_path,
                    "status": "normalized_to_utf8" if normalized else "valid_utf8",
                    "detected_encoding": detected_encoding,
                    "output_encoding": "utf-8",
                    "bom_removed": detected_encoding in {"utf-8-sig", "utf-16le-bom", "utf-16be-bom"},
                    "before_sha256": f"sha256:{before_hash}",
                    "after_sha256": f"sha256:{_sha256_bytes(after)}",
                    "content_preserved_by_strict_decode": True,
                }
            )
        return results

    def _validate_declared_docs_output_shape(
        self,
        *,
        working_dir: Path,
        allowed_target_files: Any,
        stdout: str = "",
        stderr: str = "",
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for raw_path in list(allowed_target_files or []):
            relative_path = str(raw_path or "").strip().replace("\\", "/").strip("/")
            if not relative_path or Path(relative_path).suffix.lower() not in DOCS_OUTPUT_SUFFIXES:
                continue
            target = (working_dir / relative_path).resolve()
            try:
                target.relative_to(working_dir)
            except ValueError:
                results.append({"path": relative_path, "status": "rejected", "failure_class": "output_path_escape"})
                continue
            if _docs_output_unsafe_shell_redirection_detected(
                relative_path=relative_path,
                stdout=stdout,
                stderr=stderr,
            ):
                results.append(
                    {
                        "path": relative_path,
                        "status": "rejected",
                        "failure_class": "docs_output_unsafe_shell_redirection",
                        "policy": "docs outputs must use an explicit UTF-8 file writer, not shell redirection",
                    }
                )
                continue
            if not target.exists():
                results.append({"path": relative_path, "status": "missing"})
                continue
            try:
                text = target.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                results.append(
                    {
                        "path": relative_path,
                        "status": "rejected",
                        "failure_class": "output_encoding_invalid",
                        "error": str(exc),
                    }
                )
                continue
            summary = _docs_output_shape_summary(text)
            if summary["malformed_one_word_per_line"]:
                results.append(
                    {
                        "path": relative_path,
                        "status": "rejected",
                        "failure_class": "docs_output_shape_one_word_per_line",
                        **summary,
                    }
                )
            else:
                results.append({"path": relative_path, "status": "valid_shape", **summary})
        return results


def _normalize_output(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _decode_declared_docs_text(data: bytes) -> tuple[str, str, bool]:
    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16"), "utf-16le-bom", True
    if data.startswith(b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16be-bom", True
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig", True
    return data.decode("utf-8"), "utf-8", False


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _docs_output_shape_summary(text: str) -> dict[str, Any]:
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    nonempty_lines = [line.strip() for line in lines if line.strip()]
    single_token_lines = [line for line in nonempty_lines if len(line.split()) == 1]
    longest_line_words = max((len(line.split()) for line in nonempty_lines), default=0)
    single_token_ratio = len(single_token_lines) / len(nonempty_lines) if nonempty_lines else 0.0
    malformed_one_word_per_line = (
        len(nonempty_lines) >= 6
        and single_token_ratio >= 0.8
        and longest_line_words <= 2
    )
    return {
        "nonempty_line_count": len(nonempty_lines),
        "single_token_line_count": len(single_token_lines),
        "single_token_line_ratio": round(single_token_ratio, 3),
        "longest_line_words": longest_line_words,
        "malformed_one_word_per_line": malformed_one_word_per_line,
    }


def _docs_output_unsafe_shell_redirection_detected(*, relative_path: str, stdout: str, stderr: str) -> bool:
    path = str(relative_path or "").strip().lower().replace("\\", "/").strip("/")
    if not path:
        return False
    for command in _docs_output_command_candidates(stdout=stdout, stderr=stderr):
        normalized = command.lower().replace("\\\\", "/").replace("\\", "/")
        if path not in normalized:
            continue
        shell_writer = re.search(r"\b(?:powershell(?:\.exe)?|pwsh(?:\.exe)?|set-content|out-file)\b", normalized)
        echo_redirection = re.search(r"\becho\b.*(?:>>|>|1>|2>)", normalized)
        shell_redirection = shell_writer and re.search(r"(?:>>|>|1>|2>)", normalized)
        if echo_redirection or shell_redirection:
            return True
    return False


def _docs_output_command_candidates(*, stdout: str, stderr: str) -> list[str]:
    candidates: list[str] = []
    for line in f"{stdout}\n{stderr}".splitlines():
        text = str(line or "").strip()
        if not text:
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            candidates.append(text)
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if isinstance(item, dict) and isinstance(item.get("command"), str):
            candidates.append(item["command"])
        elif isinstance(event.get("command"), str):
            candidates.append(event["command"])
    return candidates


def _extract_direct_model_output_text(payload: dict[str, Any]) -> str:
    candidates: list[Any] = [payload]
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        candidates.insert(0, data)

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        output_text = candidate.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        content = candidate.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

        response_json = candidate.get("response_json")
        if isinstance(response_json, dict):
            nested = _extract_direct_model_output_text(response_json)
            if nested:
                return nested

        choices = candidate.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    text = _extract_direct_text_content(message.get("content"))
                    if text:
                        return text

        output = candidate.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                text = _extract_direct_text_content(item.get("content"))
                if text:
                    return text
    return ""


def _extract_direct_text_content(content: Any) -> str:
    if isinstance(content, str) and content.strip():
        return content.strip()
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())
    return "".join(chunks).strip()


def _prepare_direct_model_request(body: dict[str, Any], *, source: str) -> dict[str, Any]:
    request = dict(body or {})
    request["response_format"] = {"type": "json_object"}
    request["max_tokens"] = max(4096, int(request.get("max_tokens") or 4096))
    request.setdefault("reasoning_effort", "low")
    request.pop("tools", None)
    request.pop("tool_choice", None)
    request.pop("parallel_tool_calls", None)
    request.setdefault("metadata", {})
    if isinstance(request["metadata"], dict):
        request["metadata"]["source"] = source
        request["metadata"]["direct_aix_response_policy"] = "retry_slavx_on_empty_length"
    return request


def _direct_model_request_attempts(primary: dict[str, Any]) -> list[dict[str, Any]]:
    fallback = dict(primary)
    fallback["provider"] = "slavx"
    fallback.pop("model", None)
    fallback["max_tokens"] = min(2048, int(primary.get("max_tokens") or 2048))
    fallback["reasoning_effort"] = "low"
    fallback.pop("tools", None)
    fallback.pop("tool_choice", None)
    fallback.pop("parallel_tool_calls", None)
    metadata = dict(fallback.get("metadata") or {})
    metadata["direct_aix_retry_provider"] = "slavx"
    fallback["metadata"] = metadata
    return [primary, fallback]


def _direct_model_empty_response_summary(payload: dict[str, Any], exc: Exception) -> str:
    finish_reason = _direct_payload_finish_reason(payload)
    tool_call_count = len(_direct_payload_tool_calls(payload))
    suffix: list[str] = []
    if finish_reason:
        suffix.append(f"finish_reason={finish_reason}")
    if tool_call_count:
        suffix.append(f"tool_calls={tool_call_count}")
    if suffix:
        return f"{exc} {' '.join(suffix)}"
    return str(exc)


def _should_retry_direct_model_with_slavx(payload: dict[str, Any], exc: Exception) -> bool:
    if "DIRECT_AIX_MODEL_EMPTY_RESPONSE" not in str(exc):
        return False
    finish_reason = _direct_payload_finish_reason(payload)
    return finish_reason in {"length", "tool_calls"} or bool(_direct_payload_tool_calls(payload))


def _direct_payload_finish_reason(payload: dict[str, Any]) -> str | None:
    candidates: list[Any] = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data)
        response_json = data.get("response_json")
        if isinstance(response_json, dict):
            candidates.append(response_json)
    response_json = payload.get("response_json")
    if isinstance(response_json, dict):
        candidates.append(response_json)
    for candidate in candidates:
        finish_reason = candidate.get("finish_reason") if isinstance(candidate, dict) else None
        if isinstance(finish_reason, str) and finish_reason.strip():
            return finish_reason.strip()
        choices = candidate.get("choices") if isinstance(candidate, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            finish_reason = choices[0].get("finish_reason")
            if isinstance(finish_reason, str) and finish_reason.strip():
                return finish_reason.strip()
    return None


def _direct_payload_tool_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data)
        response_json = data.get("response_json")
        if isinstance(response_json, dict):
            candidates.append(response_json)
    response_json = payload.get("response_json")
    if isinstance(response_json, dict):
        candidates.append(response_json)
    for candidate in candidates:
        tool_calls = candidate.get("tool_calls") if isinstance(candidate, dict) else None
        if isinstance(tool_calls, list) and tool_calls:
            return [dict(item) for item in tool_calls if isinstance(item, dict)]
        choices = candidate.get("choices") if isinstance(candidate, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict) and isinstance(message.get("tool_calls"), list):
                return [dict(item) for item in message["tool_calls"] if isinstance(item, dict)]
    return []


def _extract_direct_action_payload(output_text: str) -> dict[str, Any]:
    text = str(output_text or "").strip()
    if not text:
        raise ValueError("DIRECT_AIX_MODEL_EMPTY_RESPONSE")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("DIRECT_AIX_MODEL_RESPONSE_NOT_OBJECT")
    return payload


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _codex_model_name(*, provider: str, model: str) -> str:
    normalized_provider = str(provider or "").strip().lower()
    normalized_model = str(model or "").strip()
    if normalized_provider == "openrouter" and normalized_model.lower().startswith("openrouter/"):
        return normalized_model[len("openrouter/") :]
    return normalized_model


def _codex_provider_config_args(*, provider: str) -> list[str]:
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider != "openrouter":
        return []
    integration_base_url = str(settings.RUNTIME_PLANNER_API_URL or "http://integration:8020").strip().rstrip("/")
    base_url = f"{integration_base_url}/api/integrations/llm/openai/v1"
    provider_id = "hyeve-integration"
    return [
        "-c",
        f"model_provider={json.dumps(provider_id)}",
        "-c",
        f"model_providers.{provider_id}.name={json.dumps('Hyeve Integration')}",
        "-c",
        f"model_providers.{provider_id}.base_url={json.dumps(base_url)}",
        "-c",
        f"model_providers.{provider_id}.env_key={json.dumps('HYEVE_OPERATOR_TOKEN')}",
        "-c",
        f"model_providers.{provider_id}.wire_api={json.dumps('responses')}",
    ]


def _parse_cli_status(
    *,
    stdout: str,
    stderr: str,
    returncode: int | None,
    timed_out: bool,
) -> tuple[str, str | None]:
    if timed_out:
        return "FAILED", "timeout"
    structured_status = _structured_codex_status(stdout)
    if structured_status:
        if structured_status == "FAILED":
            return "FAILED", "cli_reported_failure"
        return structured_status, None
    explicit_status = _explicit_status_line(stdout) or _explicit_status_line(stderr)
    if explicit_status == "NEEDS_INPUT":
        return "NEEDS_INPUT", None
    if explicit_status == "FAILED":
        return "FAILED", "cli_reported_failure"
    if returncode not in (0, None):
        return "FAILED", "nonzero_exit"
    if returncode is None:
        return "FAILED", "missing_binary"
    return "COMPLETE", None


def _structured_codex_status(stdout: str) -> str | None:
    status: str | None = None
    for line in str(stdout or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            status = _status_from_text(str(item.get("text") or "")) or status
        elif event.get("type") == "agent_message":
            status = _status_from_text(str(event.get("text") or "")) or status
    return status


def _executor_claimed_validation(*, stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    claim_markers = (
        "running the declared validation",
        "run the declared validation",
        "ran the declared validation",
        "running validation command",
        "ran validation command",
        "validation command now",
        "validation passed",
    )
    return any(marker in text for marker in claim_markers)


def _structured_codex_summary(stdout: str) -> str | None:
    summary: str | None = None
    for line in str(stdout or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = str(item.get("text") or "").strip()
            if text:
                summary = text
        elif event.get("type") == "agent_message":
            text = str(event.get("text") or "").strip()
            if text:
                summary = text
    return summary


def _codex_summary(stdout: str, stderr: str) -> str | None:
    return _structured_codex_summary(stdout) or _first_nonempty_line(stdout, stderr)


def _explicit_status_line(output: str) -> str | None:
    for line in str(output or "").splitlines():
        status = _status_from_text(line)
        if status:
            return status
    return None


def _status_from_text(text: str) -> str | None:
    cleaned = str(text or "").strip().strip("`").strip()
    upper = cleaned.upper()
    if upper == "COMPLETE" or upper.startswith("COMPLETE:"):
        return "COMPLETE"
    if upper == "NEEDS_INPUT" or upper.startswith("NEEDS_INPUT:") or upper.startswith("CLARIFICATION_REQUIRED"):
        return "NEEDS_INPUT"
    if upper == "FAILED" or upper.startswith("FAILED:") or upper.startswith("EXECUTION_FAILED"):
        return "FAILED"
    return None


def _first_nonempty_line(*values: str) -> str | None:
    for value in values:
        for line in str(value or "").splitlines():
            cleaned = line.strip()
            if cleaned:
                return cleaned
    return None


def _status_path(line: str) -> str:
    raw = str(line or "").rstrip()
    if not raw.strip():
        return ""
    path = raw[3:] if len(raw) > 3 else raw.strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.strip().replace("\\", "/")


def _with_git_review(
    result: CliExecutionResult,
    *,
    starting_commit: str,
    review: Mapping[str, Any],
) -> CliExecutionResult:
    return _replace_result(
        result,
        starting_commit=starting_commit,
        changed_files=[str(item) for item in review.get("changed_files") or []],
        diff=str(review.get("diff") or ""),
    )


def _with_execution_evidence(
    result: CliExecutionResult,
    *,
    validation_results: list[dict[str, Any]],
    execution_timestamps: dict[str, str],
) -> CliExecutionResult:
    runtime_context = dict(result.runtime_context or {})
    runtime_context["validation_results"] = [dict(item) for item in validation_results]
    runtime_context["execution_timestamps"] = dict(execution_timestamps)
    return _replace_result(
        result,
        validation_results=[dict(item) for item in validation_results],
        execution_timestamps=dict(execution_timestamps),
        runtime_context=runtime_context,
    )


def _outside_target_edits(*, changed_files: Sequence[str], allowed_target_files: Any) -> list[str]:
    allowed = {
        str(item or "").strip().replace("\\", "/").strip("/")
        for item in list(allowed_target_files or [])
        if str(item or "").strip()
    }
    if not allowed:
        return []
    outside: list[str] = []
    for changed in changed_files:
        normalized = str(changed or "").strip().replace("\\", "/").strip("/")
        if normalized and normalized not in allowed:
            outside.append(normalized)
    return outside


def _append_line(existing: str, line: str) -> str:
    current = str(existing or "").rstrip()
    return f"{current}\n{line}".strip() if current else line


def _replace_result(result: CliExecutionResult, **changes: Any) -> CliExecutionResult:
    payload: dict[str, Any] = {
        "status": result.status,
        "backend": result.backend,
        "command": result.command,
        "working_dir": result.working_dir,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "duration_ms": result.duration_ms,
        "attempts": result.attempts,
        "dry_run": result.dry_run,
        "failure_class": result.failure_class,
        "summary": result.summary,
        "runtime_context": result.runtime_context,
        "starting_commit": result.starting_commit,
        "changed_files": result.changed_files,
        "diff": result.diff,
        "validation_results": result.validation_results,
        "linter_results": result.linter_results,
        "test_results": result.test_results,
        "execution_timestamps": result.execution_timestamps,
    }
    payload.update(changes)
    return CliExecutionResult(**payload)


__all__ = [
    "CliBackendAdapter",
    "CliBackendNotFoundError",
    "CliExecutionRequest",
    "CliExecutionResult",
    "CliExecutorConfig",
    "CliExecutorError",
    "CliExecutorService",
    "CodexCliBackend",
    "create_cli_backend_adapter",
]
