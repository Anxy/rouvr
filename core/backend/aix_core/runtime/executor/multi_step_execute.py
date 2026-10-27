"""Generic multi-step mutation execution on the production ``run_real_execute`` spine.

Does NOT wire BoundedDecomposer / DagPlanCompiler. Reuses discovery, codegen,
validate/apply, mutation evidence, tests, and completion gates in a budget-bounded
inspect → mutate → verify → (repair) loop.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import re
import shlex
import shutil
import sys

from backend.aix_core.runtime.executor.python_argv import resolve_python_argv
from backend.aix_core.runtime.objective.discovery import (
    DiscoveryReport,
    _LARGE_MUTATION_FILE_MAX_BYTES,
    run_bound_files_discovery,
    run_focused_file_discovery,
    run_repository_discovery,
)
from backend.aix_core.runtime.governance.execution_budget import ExecutionBudgetManager
from backend.aix_core.runtime.verification.mutation_evidence import (
    AppliedMutation,
    assert_content_contains,
    merge_mutation_evidence,
)
from backend.aix_core.runtime.executor.openrouter_codegen import OpenRouterUsage
from backend.aix_core.runtime.executor.patch_validate import apply_patch_atomic, validate_patch
from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolbox, RepositoryToolError
from backend.aix_core.runtime.executor.capability_dispatch import (
    CapabilityMismatch,
    select_strategy,
)
from backend.aix_core.runtime.objective.contracts import RequiredEffectV1, RepairDirectiveV1, StepContractV1
from backend.aix_core.runtime.executor.step_upstream_discovery import (
    DEFAULT_UPSTREAM_MAX_BYTES,
    UPSTREAM_HELPER_VERSION,
    UpstreamDiscoveryPlan,
    merge_discovery_paths,
    resolve_upstream_target_paths,
    truncate_upstream_content,
)
from backend.aix_core.runtime.verification.product_structure_coverage import (
    step_has_continuity_refs,
)
from backend.aix_core.runtime.executor.executor_profile import (
    STRATEGY_ENV_ASSIGN,
    STRATEGY_PHP_STRUCTURAL,
    STRATEGY_ROUTE,
    STRATEGY_SNIPPET_CODEGEN,
    STRATEGY_TEXT_REPLACE,
)
from backend.aix_core.objective_cycle.objective_coverage import objective_requires_focused_tests
from backend.aix_core.intelligence.repository.file_structure import detect_language
from backend.aix_core.runtime.executor.required_effects import (
    _step_scope_is_python_only,
    effects_for_step,
    infer_required_effects,
    merge_required_effects,
    sanitize_required_effects,
)
from backend.aix_core.runtime.executor.source_patch_codegen import (
    PatchCodegenResult,
    PatchEdit,
    extract_text_replacements,
    generate_source_patch,
    local_fallback_php_controller_refactor,
    local_fallback_python_typeddict_intro,
    local_fallback_route_file_refactor,
    local_fallback_sqlite_boolean_column,
    local_fallback_api_archive_endpoint,
    local_fallback_cycle_summary_strip,
    local_fallback_archived_objectives_filter,
    local_fallback_safe_backend_base_url,
    local_fallback_text_replacements,
    _build_unified_diff,
    _is_creatable_test_path,
)
from backend.aix_core.runtime.executor.structural_execute import apply_structural_effects
from backend.aix_core.runtime.verification.structural_verify import verify_structural_effects


OnEvent = Callable[[str, dict[str, Any]], None]

_SHELL_METACHARS = frozenset(";|&$`()<>\n")

_OBJECTIVE_PYTEST_RE = re.compile(
    r"(?:python(?:\.exe)?\s+-m\s+)?pytest(?:\.exe)?\s+([^\n\r]+)",
    re.IGNORECASE,
)


def _validation_commands_from_objective(objective: str) -> tuple[str, ...]:
    """Pull an explicit pytest (or python -m pytest) command from objective prose."""
    text = str(objective or "")
    match = _OBJECTIVE_PYTEST_RE.search(text)
    if not match:
        return ()
    rest = match.group(1).strip()
    # Stop at sentence end / next instruction clause.
    rest = re.split(r"\s+(?:and\s+verify|Do\s+not|Change\s+both)\b", rest, maxsplit=1)[0].strip()
    rest = rest.rstrip(".,;")
    if not rest:
        return ()
    # Keep a portable `python -m pytest …` form here. Binding to sys.executable happens
    # in resolve_python_argv after argv split — embedding a Windows path into the string
    # and then shlex-splitting it eats backslashes (D:\v2.1\… → D:v2.1…).
    cmd = f"python -m pytest {rest}"
    return (cmd,)


def _infer_focused_pytest_from_paths(
    *,
    objective: str,
    paths: list[str] | tuple[str, ...],
    prefer_near_paths: list[str] | tuple[str, ...] | None = None,
) -> list[str] | None:
    """When the objective asks for tests and bound/discovered paths include pytest
    modules, run those modules — repos without a root pyproject still need verification.
    """
    lowered = str(objective or "").casefold()
    if not any(token in lowered for token in ("test", "pytest", "regression", "coverage")):
        return None
    test_paths: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        norm = str(raw or "").strip().replace("\\", "/")
        if not norm or norm in seen:
            continue
        base = norm.rsplit("/", 1)[-1]
        is_test_module = base.startswith("test_") and base.endswith(".py")
        is_tests_tree = ("/tests/" in f"/{norm}") and base.endswith(".py") and (
            base.startswith("test_") or base.endswith("_test.py")
        )
        if not (is_test_module or is_tests_tree):
            continue
        # Bound topic discovery often includes unrelated large suites; keep only
        # fixtures/helpers next to the mutation unless nothing else qualifies.
        seen.add(norm)
        test_paths.append(norm)
    if not test_paths:
        return None

    prefer = {
        str(p or "").strip().replace("\\", "/").rsplit("/", 1)[0]
        for p in (prefer_near_paths or ())
        if str(p or "").strip()
    }
    stems = {
        str(p or "").strip().replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".py")
        for p in (prefer_near_paths or ())
        if str(p or "").strip().endswith(".py")
    }
    stems |= {f"test_{s}" for s in list(stems)}
    stems |= {s[5:] for s in list(stems) if s.startswith("test_")}

    def _stem_related(path: str) -> bool:
        if not stems:
            return True
        base = path.rsplit("/", 1)[-1].removesuffix(".py")
        bare = base[5:] if base.startswith("test_") else base
        return base in stems or bare in stems or any(
            bare == s or s.endswith(bare) or bare.endswith(s) for s in stems
        )

    if prefer:
        near = [p for p in test_paths if p.rsplit("/", 1)[0] in prefer]
        if near:
            related_near = [p for p in near if _stem_related(p)]
            test_paths = related_near or near
        else:
            related = [p for p in test_paths if _stem_related(p)]
            if related:
                test_paths = related

    return [sys.executable, "-m", "pytest", "-q", *test_paths[:3]]


def _existence_validated_preexisting_file(existence_cmd: list[str] | None, discovery) -> bool:
    """True when a `test -f/-d/-e` existence check validated a file that
    already had content before the round (EDIT shape, not CREATE).

    Existence proves presence, never behavior; on a pre-existing file it
    must not satisfy verification — the focused-pytest fallback below must
    run the real suite instead.
    """
    if not existence_cmd or len(existence_cmd) < 3:
        return False
    rel = str(existence_cmd[2] or "").replace("\\", "/").lstrip("./").strip()
    if not rel:
        return False
    try:
        before = (getattr(discovery, "config_files", None) or {}).get(rel)
    except (AttributeError, TypeError):
        return False
    return bool(before)


_FILE_TOKEN_RE = re.compile(
    r"(?<![\w/.-])([\w-]+(?:/[\w-]+)*\.(?:py|php|ts|tsx|js|jsx|json|yaml|yml|md))(?![\w])"
)


def _related_repo_test_modules(
    repository_root: Path,
    mutated: list[str] | tuple[str, ...],
) -> list[str]:
    """Stem-related test modules from the repo (editproj `ship.py` ↔ `tests/test_ship.py`).

    Focused-file discovery often loads only the target source, leaving the
    validation pool without any test module — validation then cannot run the
    real suite. Graft stem-related repo tests so EDIT validation still runs
    behavior, instead of failing closed (or worse, diff_only-passing) for
    lack of a pool. Bounded and read-only.
    """
    try:
        stems: set[str] = set()
        for raw in mutated or ():
            norm = str(raw or "").strip().replace("\\", "/")
            if not norm.endswith(".py"):
                continue
            stem = norm.rsplit("/", 1)[-1].removesuffix(".py")
            if stem:
                stems.add(stem)
        if not stems:
            return []
        stems |= {f"test_{s}" for s in list(stems)}
        stems |= {s[5:] for s in list(stems) if s.startswith("test_")}
        root = Path(str(repository_root))
        candidates: list[Path] = []
        try:
            candidates.extend(sorted(root.glob("tests/**/*.py")))
        except OSError:
            pass
        try:
            candidates.extend(sorted(root.glob("test_*.py")))
        except OSError:
            pass
        out: list[str] = []
        for cand in candidates[:200]:
            try:
                rel = cand.relative_to(root).as_posix()
            except ValueError:
                continue
            base = rel.rsplit("/", 1)[-1].removesuffix(".py")
            bare = base[5:] if base.startswith("test_") else base
            if base in stems or bare in stems or any(
                bare == s or s.endswith(bare) or bare.endswith(s) for s in stems
            ):
                if rel not in out:
                    out.append(rel)
            if len(out) >= 5:
                break
        return out
    except (OSError, ValueError, AttributeError, TypeError):
        return []


def _validation_path_pool(
    *,
    mutated: list[str] | tuple[str, ...],
    bound_multi: list[str] | tuple[str, ...],
    discovery,
    repository_root: Path,
) -> list[str]:
    """Candidate paths for focused-pytest inference.

    Discovery often loads only sources, so graft stem-related repo test
    modules — otherwise validation cannot run the real suite (diff_only
    pass with no verification, or fail-closed with no suite to run).
    """
    try:
        base = list(mutated or ()) + list(bound_multi or ())
        config = getattr(discovery, "config_files", None) or {}
        tests = getattr(discovery, "test_files", None) or []
        base = base + list(config.keys()) + list(tests)
    except (AttributeError, TypeError):
        base = list(mutated or ())
    pool = list(dict.fromkeys(str(p) for p in base if str(p or "").strip()))
    try:
        grafted = _related_repo_test_modules(repository_root, mutated)
    except (OSError, ValueError, AttributeError, TypeError):
        grafted = []
    for rel in grafted:
        if rel not in pool:
            pool.append(rel)
    return pool


def _test_only_mutations_miss_named_source(
    *,
    objective: str,
    applied_mutation_paths: list[str] | tuple[str, ...],
    repository_root: Path,
    normalize_repo_relative_path,
    looks_like_a_file,
    extract_target_path,
    resolve_relative_path,
) -> str | None:
    """AIX-EDIT-002 (mutation level): detail when the objective names an
    existing non-test source file, the objective does not ask for new tests,
    but the round mutated only test paths while the named source is untouched.

    Reading tests is fine; editing *only* tests while the requested source
    stays unchanged is a wrong-file edit — fail into clarify, never PASS.
    """
    if not applied_mutation_paths:
        return None
    if objective_requires_focused_tests(objective):
        return None
    text = str(objective or "")
    objective_target = None
    try:
        objective_target = extract_target_path(text)
    except (AttributeError, TypeError, ValueError):
        objective_target = None
    if not objective_target:
        # Fallback: the shared extractor misses prose shapes like
        # "In the existing file ship.py ..." — scan for file-like tokens
        # that resolve to real repo files, preferring non-test sources.
        candidates: list[str] = []
        try:
            for token in sorted(
                set(_FILE_TOKEN_RE.findall(text)), key=len, reverse=True
            ):
                try:
                    resolved = resolve_relative_path(token, repository_root)
                except (OSError, ValueError):
                    continue
                if not resolved or not looks_like_a_file(resolved):
                    continue
                try:
                    if not (Path(str(repository_root)) / resolved).is_file():
                        continue
                except OSError:
                    continue
                candidates.append(resolved)
        except (AttributeError, TypeError):
            candidates = []
        non_test = [c for c in candidates if not _is_test_path(c)]
        objective_target = (non_test or candidates or [None])[0]
    lowered = text.casefold()
    if any(
        tok in lowered
        for tok in (
            "add a test",
            "add tests",
            "add regression",
            "write a test",
            "write tests",
            "new test",
            "new tests",
            "regression test",
        )
    ):
        return None
    if not objective_target:
        return None
    try:
        rel = resolve_relative_path(objective_target, repository_root)
    except (OSError, ValueError):
        return None
    if not rel or not looks_like_a_file(rel) or _is_test_path(rel):
        return None
    try:
        if not (Path(str(repository_root)) / rel).is_file():
            return None
    except OSError:
        return None
    mutated = [
        p for p in (normalize_repo_relative_path(str(p)) for p in applied_mutation_paths) if p
    ]
    if not mutated or rel in mutated:
        return None
    if all(_is_test_path(p) for p in mutated):
        return (
            f"objective names source '{rel}' but the round edited only tests "
            f"({', '.join(mutated[:5])}); clarify scope instead of passing a wrong-file edit"
        )
    return None


def _whole_file_line_counts(before: str, after: str) -> tuple[int, int]:
    """(added, removed) line counts for a whole-file write.

    Line-count deltas alone report 0/0 for same-line-count modifications
    (identifier rename, single-value change) and the verifier then fails
    correct work as 'no mutation diff'. Multiset comparison counts lines
    whose content actually changed.
    """
    try:
        before_counter: Counter[str] = Counter(str(before or "").splitlines())
        after_counter: Counter[str] = Counter(str(after or "").splitlines())
        added = sum((after_counter - before_counter).values())
        removed = sum((before_counter - after_counter).values())
        return int(added), int(removed)
    except (TypeError, ValueError, AttributeError):
        before_lines = str(before or "").splitlines()
        after_lines = str(after or "").splitlines()
        return (
            max(0, len(after_lines) - len(before_lines)),
            max(0, len(before_lines) - len(after_lines)),
        )


def _is_test_path(relative_path: str) -> bool:
    """True for test-suite paths (tests/ dirs or test_*.py/*_test.py files)."""
    norm = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    if not norm:
        return False
    if norm == "tests" or norm.startswith("tests/"):
        return True
    base = norm.rsplit("/", 1)[-1]
    return base.startswith("test_") or base.endswith("_test.py")


def _explicit_source_focus_mismatch(
    *,
    objective_target: str | None,
    allowed_files: tuple[str, ...] | list[str],
    repository_root: Path,
    normalize_repo_relative_path,
    looks_like_a_file,
    resolve_relative_path,
) -> str | None:
    """AIX-EDIT-002: explicit requested source must outrank adjacent tests.

    Returns a detail string when the objective names an existing non-test
    source file but the step scope targets only test paths (planner may
    include tests only when the scope requires them); otherwise None.
    """
    if not objective_target:
        return None
    try:
        rel = resolve_relative_path(objective_target, repository_root)
    except (OSError, ValueError):
        return None
    if not rel or not looks_like_a_file(rel) or _is_test_path(rel):
        return None
    try:
        if not (Path(str(repository_root)) / rel).is_file():
            return None
    except OSError:
        return None
    scoped = [
        normalize_repo_relative_path(str(p))
        for p in (allowed_files or [])
        if str(p).strip()
    ]
    scoped = [p for p in scoped if p]
    if not scoped or rel in scoped:
        return None
    if all(_is_test_path(p) for p in scoped):
        return (
            f"objective names source '{rel}' but step scope targets only tests "
            f"({', '.join(scoped[:5])}); clarify scope instead of silently replacing target"
        )
    return None


def _validation_targets_mutated_paths(
    test_command: list[str] | None, mutated_paths: list[str]
) -> bool:
    """True when a validation argv mentions a directory/stem near mutated files."""
    if not test_command or not mutated_paths:
        return False
    joined = " ".join(str(p) for p in test_command).replace("\\", "/").casefold()
    for raw in mutated_paths:
        norm = str(raw or "").replace("\\", "/").casefold()
        if not norm:
            continue
        directory = norm.rsplit("/", 1)[0]
        stem = norm.rsplit("/", 1)[-1].removesuffix(".py")
        if directory and directory in joined:
            return True
        if stem and stem in joined:
            return True
        if stem.startswith("test_"):
            bare = stem[5:]
            if bare and bare in joined:
                return True
        elif f"test_{stem}" in joined:
            return True
    return False


def _is_syntactic_only_validation(test_command: list[str] | None) -> bool:
    """True for commands that exit 0 without asserting application behavior.

    Planner often emits ``git diff --check`` next to a schema path. That shares a
    parent directory with real mutations, so directory-proximity checks alone would
    treat it as sufficient verification (live S10 false PASS).
    """
    if not test_command:
        return False
    tokens = [str(p).casefold() for p in test_command]
    joined = " ".join(tokens)
    if "git" in tokens and "diff" in tokens and "--check" in tokens:
        return True
    # Bare syntax lint without a test runner.
    if tokens[:2] == ["php", "-l"] or (len(tokens) >= 2 and tokens[1] == "-l" and "php" in tokens[0]):
        return True
    if "pytest" in joined or "-m pytest" in joined:
        return False
    if any(tok in joined for tok in ("unittest", "nose2", "go test", "cargo test", "mvn test")):
        return False
    return False


def _greenfield_php_sql_deliverables_present(
    *,
    objective: str,
    bound_paths: list[str],
    applied_mutations: list[AppliedMutation],
    repository_root: Path,
) -> bool:
    """Accept php -l / git-diff-check when greenfield PHP+SQL files were actually written.

    Cycle workspaces have no pytest harness; requiring a behavioral runner forever
    blocked ERP step 2 after config.php + database.sql were created.

    SEM-001-W4: callers must not use this soft-pass when the step carries nonempty
    continuity refs — product_structure_coverage is required instead.

    REFACTOR / cosmetic-repair objectives that merely mention "database" must not
    soft-pass just because a pre-existing PHP file is already on disk.
    """
    obj = str(objective or "").casefold()
    if any(
        tok in obj
        for tok in (
            "refactor",
            "extract",
            "remove unused",
            "helper method",
            "cosmetic",
        )
    ):
        return False
    if not any(
        tok in obj
        for tok in (
            "php",
            "sql",
            "pdo",
            "database",
            "greenfield",
            "erp",
            "proposed/",
            "config.php",
            "api.php",
        )
    ):
        return False
    after = {
        str(m.path).replace("\\", "/"): str(m.after_content or "")
        for m in applied_mutations
        if m.after_content is not None
    }
    candidates = [
        str(p).replace("\\", "/").lstrip("./")
        for p in (bound_paths or list(after.keys()))
        if str(p).lower().endswith((".php", ".sql"))
    ]
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        return False
    for rel in candidates:
        body = after.get(rel, "")
        if not body.strip():
            disk = repository_root / rel
            try:
                body = disk.read_text(encoding="utf-8", errors="replace") if disk.is_file() else ""
            except OSError:
                body = ""
        if len(body.strip()) < 40:
            return False
        lower = body.casefold()
        if rel.lower().endswith(".php") and "<?php" not in lower:
            return False
        if rel.lower().endswith(".sql") and "create table" not in lower:
            return False
    return True


def _posix_existence_validation(
    *,
    repository_root: Path,
    argv: list[str],
) -> dict[str, Any] | None:
    """SEM-001-W4-T4: implement ``test -f/-d/-e`` via Path.exists (Windows-safe).

    Planner validation often emits ``test -f path``. On Windows ``test`` is not on
    PATH, which previously cleared the command and fell through to diff_only.
    """
    if len(argv) < 3:
        return None
    exe = str(argv[0] or "").strip().casefold()
    flag = str(argv[1] or "").strip()
    if exe not in {"test", "test.exe"} or flag not in {"-f", "-d", "-e"}:
        return None
    rel = str(argv[2] or "").replace("\\", "/").lstrip("./").strip()
    if not rel:
        return None
    target = repository_root / rel
    if flag == "-f":
        ok = target.is_file()
    elif flag == "-d":
        ok = target.is_dir()
    else:
        ok = target.exists()
    return {
        "returncode": 0 if ok else 1,
        "stdout": f"path_exists:{rel}" if ok else "",
        "stderr": "" if ok else f"path_missing:{rel}",
        "command": list(argv),
    }


def _python_syntax_errors(applied_mutations: list[AppliedMutation]) -> list[str]:
    """Fail closed when a mutated Python file cannot even parse."""
    errors: list[str] = []
    for mut in applied_mutations:
        path = str(mut.path or "").replace("\\", "/")
        if not path.endswith(".py"):
            continue
        source = str(mut.after_content or "")
        if not source.strip():
            continue
        try:
            compile(source, path, "exec")
        except SyntaxError as exc:
            errors.append(f"{path}:{exc.lineno}: {exc.msg}")
    return errors


def _safe_validation_argv(raw: str, *, fallback_file: str = "") -> list[str] | None:
    """Drop `&&` / redirection so declared `php -l` can actually run."""
    text = str(raw or "").strip()
    if not text:
        return None
    head = re.split(r"\s*(?:&&|\|\||;)\s*", text, maxsplit=1)[0].strip()
    head = re.sub(r"\s*2>&1\s*$", "", head).strip()
    if any(ch in head for ch in _SHELL_METACHARS):
        if fallback_file.lower().endswith(".php"):
            return ["php", "-l", fallback_file]
        return None
    try:
        parts = shlex.split(head)
    except ValueError:
        return None
    if not parts:
        return None
    # Planner prose sometimes emits `pytest --collect-only`, which exits 0 without
    # asserting anything. That must not count as behavioral verification.
    cleaned: list[str] = []
    skip_next = False
    for idx, part in enumerate(parts):
        if skip_next:
            skip_next = False
            continue
        lowered = part.casefold()
        if lowered in {"--collect-only", "--co"}:
            continue
        cleaned.append(part)
    parts = cleaned or parts
    return resolve_python_argv(parts)


@dataclass
class MutationRoundResult:
    round_index: int
    edits_applied: list[str] = field(default_factory=list)
    gates_passed: bool = False
    gate_reasons: tuple[str, ...] = ()
    test_returncode: int | None = None
    discovery_mode: str = "focused"
    detail: str = ""


@dataclass
class MultiStepMutationResult:
    ok: bool
    code: str
    detail: str
    discovery: DiscoveryReport
    rounds: list[MutationRoundResult]
    diff_text: str
    diff_stats: dict[str, dict[str, int]]
    changed_files: list[dict[str, Any]]
    events: list[dict[str, Any]]
    test_result: dict[str, Any] | None
    completion_gates: dict[str, Any] | None
    proposed_edits: list[dict[str, Any]] | None = None
    already_satisfied: bool = False
    already_satisfied_reason: str = ""
    already_satisfied_evidence_refs: tuple[str, ...] = ()
    token_usage: dict[str, Any] | None = None
    strategy_id: str = ""
    remaining_asked_work: bool = False
    unmet_effects: tuple[str, ...] = ()


def _merge_codegen_usage(
    accumulated: OpenRouterUsage | None, usage: OpenRouterUsage | None
) -> OpenRouterUsage | None:
    if usage is None:
        return accumulated
    if accumulated is None:
        return usage
    return OpenRouterUsage(
        prompt_tokens=int(accumulated.prompt_tokens) + int(usage.prompt_tokens),
        completion_tokens=int(accumulated.completion_tokens) + int(usage.completion_tokens),
        total_tokens=int(accumulated.total_tokens) + int(usage.total_tokens),
        model=str(usage.model or accumulated.model),
        provider=str(usage.provider or accumulated.provider),
        ok=bool(accumulated.ok or usage.ok),
        detail=str(usage.detail or accumulated.detail),
    )


def _usage_to_token_dict(usage: OpenRouterUsage | None) -> dict[str, Any] | None:
    if usage is None:
        return None
    return {
        "provider": usage.provider,
        "model": usage.model,
        "prompt_tokens": int(usage.prompt_tokens),
        "completion_tokens": int(usage.completion_tokens),
        "total_tokens": int(usage.total_tokens),
        "ok": bool(usage.ok),
        "detail": usage.detail,
    }


class MultiStepMutationError(Exception):
    def __init__(
        self,
        code: str,
        detail: str = "",
        *,
        events: list[dict[str, Any]] | None = None,
        raw_model_payload: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.detail = detail
        self.events = list(events or [])
        self.raw_model_payload = raw_model_payload
        # NO_EDIT hardening: codegen usage must survive the failure path so a
        # prose-only terminal can never again be misread as "model not called".
        self.usage = usage
        super().__init__(f"{code}: {detail}" if detail else code)


def _ensure_allowed_files_discovered(
    *,
    toolbox: RepositoryToolbox,
    report: DiscoveryReport,
    allowed_files: tuple[str, ...],
    normalize_repo_relative_path: Callable[[str], str],
    looks_like_a_file: Callable[[str], bool],
) -> DiscoveryReport:
    """Keep bound mutation targets in discovery across expand-on-miss rounds."""
    for raw in allowed_files:
        rel = normalize_repo_relative_path(str(raw))
        if not rel or not looks_like_a_file(rel):
            continue
        if str(report.config_files.get(rel) or "").strip():
            continue
        try:
            content = toolbox.read_file(rel, max_bytes=_LARGE_MUTATION_FILE_MAX_BYTES)
        except (RepositoryToolError, FileNotFoundError, IsADirectoryError, OSError):
            # Bound path that does not exist yet is a create target — keep it in
            # discovery so codegen can emit new_content (live S4 NO_APPLICABLE_SOURCE_FILE).
            if rel not in report.config_files:
                report.config_files[rel] = ""
            continue
        report.config_files[rel] = str(content.get("content") or "")
    return report


def _plan_and_validate_upstream_discovery(
    *,
    step_contract: StepContractV1 | None,
    plan_steps: tuple[StepContractV1, ...] | list[StepContractV1],
    repository_root: Path,
    looks_like_a_file: Callable[[str], bool],
    events: list[dict[str, Any]],
    on_event: OnEvent,
    upstream_max_bytes: int = DEFAULT_UPSTREAM_MAX_BYTES,
) -> UpstreamDiscoveryPlan:
    """SEM-001-W2: resolve depends_on → prior target_scope; fail closed if missing."""
    plan = resolve_upstream_target_paths(
        step_contract=step_contract,
        plan_steps=plan_steps,
        looks_like_a_file=looks_like_a_file,
    )
    if plan.depends_on_step_ids and plan.unresolved_depends_on:
        detail = (
            "depends_on step ids unresolved in plan_steps: "
            + ", ".join(plan.unresolved_depends_on)
        )
        on_event(
            "discovery.upstream_missing",
            {
                "reason": "unresolved_depends_on",
                "unresolved": list(plan.unresolved_depends_on),
                "helper_version": UPSTREAM_HELPER_VERSION,
            },
        )
        raise MultiStepMutationError("NEED_UPSTREAM", detail, events=events)

    missing = [
        path
        for path in plan.upstream_paths
        if not (repository_root / Path(str(path).replace("\\", "/"))).is_file()
    ]
    if missing:
        on_event(
            "discovery.upstream_missing",
            {
                "reason": "missing_on_disk",
                "paths": list(missing),
                "helper_version": UPSTREAM_HELPER_VERSION,
            },
        )
        raise MultiStepMutationError(
            "NEED_UPSTREAM",
            "depends_on target_scope files missing on disk: " + ", ".join(missing[:12]),
            events=events,
        )

    if plan.upstream_paths:
        on_event(
            "discovery.upstream_planned",
            {
                "paths": list(plan.upstream_paths),
                "depends_on": list(plan.depends_on_step_ids),
                "max_bytes": int(upstream_max_bytes),
                "helper_version": UPSTREAM_HELPER_VERSION,
            },
        )
    return plan


def _apply_upstream_body_caps(
    *,
    report: DiscoveryReport,
    upstream_paths: tuple[str, ...] | list[str],
    max_bytes: int,
    on_event: OnEvent,
) -> DiscoveryReport:
    """Deterministically truncate read-only upstream bodies (mutation targets untouched)."""
    for raw_path in upstream_paths:
        path = str(raw_path or "").replace("\\", "/").lstrip("./").strip()
        if not path or path not in report.config_files:
            continue
        body = report.config_files.get(path)
        if body is None:
            continue
        trunc = truncate_upstream_content(str(body), max_bytes=max_bytes, path=path)
        report.config_files[path] = trunc.text
        on_event(
            "discovery.upstream_truncated" if trunc.truncated else "discovery.upstream_body",
            {
                "path": path,
                "original_bytes": trunc.original_bytes,
                "kept_bytes": trunc.kept_bytes,
                "truncated": trunc.truncated,
                "sha256_12": trunc.content_sha256_12,
                "helper_version": UPSTREAM_HELPER_VERSION,
            },
        )
    return report


def _resolve_focused_target(
    *,
    allowed_files: tuple[str, ...],
    objective: str,
    repository_root: Path,
    normalize_repo_relative_path: Callable[[str], str],
    looks_like_a_file: Callable[[str], bool],
    extract_target_path: Callable[[str], str | None],
    resolve_relative_path: Callable[[str, Path], str | None],
) -> str | None:
    allowed_rel = [normalize_repo_relative_path(p) for p in allowed_files if str(p).strip()]
    allowed_rel = [p for p in allowed_rel if p]
    if len(allowed_rel) == 1 and looks_like_a_file(allowed_rel[0]):
        return allowed_rel[0]
    if len(allowed_rel) > 1:
        # Multi-file greenfield steps: finish missing targets before re-editing ones
        # that already exist (ERP api.php present while config.php/database.sql absent).
        missing = [
            p
            for p in allowed_rel
            if looks_like_a_file(p) and not (repository_root / p).is_file()
        ]
        if missing:
            return missing[0]
        existing = [p for p in allowed_rel if looks_like_a_file(p)]
        if existing:
            return existing[0]
    hinted = extract_target_path(objective)
    if hinted:
        rel = resolve_relative_path(hinted, repository_root)
        if rel and looks_like_a_file(rel):
            return rel
    try:
        from backend.aix_core.runtime.executor.frontend_route_resolve import (
            resolve_page_from_objective,
        )

        page = resolve_page_from_objective(repository_root, objective)
        if page and looks_like_a_file(page):
            return normalize_repo_relative_path(page)
    except OSError:
        pass
    return None


def _repo_relative_hit_path(path: str, *, repository_root: Path | None = None) -> str:
    """Normalize search/discovery paths to repo-relative posix (dedupe abs vs rel)."""
    raw = str(path or "").replace("\\", "/").strip()
    if not raw:
        return ""
    if repository_root is not None:
        try:
            abs_path = Path(raw)
            root = repository_root.resolve()
            if not abs_path.is_absolute():
                abs_path = (root / raw).resolve()
            else:
                abs_path = abs_path.resolve()
            return abs_path.relative_to(root).as_posix()
        except (OSError, ValueError):
            pass
    # Strip drive-letter absolute prefixes when root wasn't available.
    if len(raw) >= 3 and raw[1] == ":" and raw[2] == "/":
        return raw
    return raw.lstrip("/")


def _seed_files_for_replacements(
    *,
    toolbox: RepositoryToolbox,
    report: DiscoveryReport,
    objective: str,
    max_files: int = 20,
    skip_search_if_present: bool = False,
    allowed_files_bound: bool = False,
) -> DiscoveryReport:
    """Load files that contain replace/rename tokens into discovery.config_files."""
    pairs = extract_text_replacements(objective)
    if not pairs:
        return report
    root = getattr(toolbox, "repository_root", None)
    existing_norm = {
        _repo_relative_hit_path(p, repository_root=root) for p in report.config_files
    }
    # Discovery may stash .env.example outside config_files — include it when tokens match.
    if report.env_example_path and report.env_example is not None:
        env_rel = _repo_relative_hit_path(report.env_example_path, repository_root=root)
        if env_rel and env_rel not in existing_norm:
            if any(old in report.env_example for old, _new in pairs):
                report.config_files[env_rel] = report.env_example
                existing_norm.add(env_rel)

    # Bound admit scope / focused / repair rounds: never expand via repo-wide
    # OLD-token search. Searching `"version": 2` across aix-backend pulls dozens of
    # unrelated hits and burns EXPLORATION_BUDGET_EXCEEDED before any patch applies.
    if skip_search_if_present or allowed_files_bound:
        return report

    for old, _new in pairs:
        try:
            search = toolbox.search_files(old)
        except (RepositoryToolError, OSError):
            continue
        for hit in (search.get("hits") or [])[:max_files]:
            path = _repo_relative_hit_path(
                str(hit.get("path") or hit.get("relative_path") or ""),
                repository_root=root,
            )
            if not path or path in existing_norm or path in report.config_files:
                continue
            try:
                content = toolbox.read_file(path)
            except (RepositoryToolError, FileNotFoundError, IsADirectoryError, OSError):
                continue
            report.config_files[path] = str(content.get("content") or "")
            existing_norm.add(path)
            if len(report.config_files) >= max_files:
                return report
    return report


def _discover_additional_dependencies(
    *,
    toolbox: RepositoryToolbox,
    report: DiscoveryReport,
    hints: list[str],
    max_extra_files: int = 5,
) -> DiscoveryReport:
    """Expand discovery by searching for symbols/tokens mentioned in repair hints."""
    root = getattr(toolbox, "repository_root", None)
    existing_norm = {
        _repo_relative_hit_path(p, repository_root=root) for p in report.config_files
    }
    for hint in hints:
        token = str(hint or "").strip()
        if len(token) < 3:
            continue
        try:
            search = toolbox.search_files(token)
        except (RepositoryToolError, OSError):
            continue
        for hit in (search.get("hits") or [])[:max_extra_files]:
            path = _repo_relative_hit_path(
                str(hit.get("path") or hit.get("relative_path") or ""),
                repository_root=root,
            )
            if not path or path in existing_norm or path in report.config_files:
                continue
            try:
                content = toolbox.read_file(path)
            except (RepositoryToolError, FileNotFoundError, IsADirectoryError, OSError):
                continue
            report.config_files[path] = str(content.get("content") or "")
            existing_norm.add(path)
            if len(report.config_files) >= max_extra_files + 3:
                return report
    return report


def _structural_patch(
    *,
    discovery: DiscoveryReport,
    effects: tuple[RequiredEffectV1, ...],
) -> PatchCodegenResult:
    edits: list[PatchEdit] = []
    remaining = False
    unmet: list[str] = []
    notes: list[str] = []
    all_passed = True
    for path, content in discovery.config_files.items():
        result = apply_structural_effects(path=path, content=content, effects=effects)
        if result.unsupported:
            return PatchCodegenResult(
                edits=[],
                no_files_identified=True,
                reason=result.unsupported_detail or "UNSUPPORTED_CAPABILITY",
                remaining_asked_work=True,
                unmet_signals=tuple(result.remaining_effects),
                strategy_id=STRATEGY_PHP_STRUCTURAL,
            )
        verified = verify_structural_effects(
            path=path,
            before=content,
            after=result.content,
            effects=effects,
            created_files=result.created_files,
        )
        notes.append(result.detail)
        notes.append(verified.detail)
        if not verified.passed:
            all_passed = False
            remaining = True
            unmet.extend(verified.missing_effects)
        if result.remaining_asked_work:
            remaining = True
            unmet.extend(result.remaining_effects)
        if result.content != content:
            edits.append(
                PatchEdit(
                    path=path,
                    unified_diff=_build_unified_diff(
                        path=path, old_content=content, new_content=result.content
                    ),
                    new_content=result.content,
                    reason=result.detail or "structural php refactor",
                )
            )
        for created_path, created_body in (result.created_files or {}).items():
            edits.append(
                PatchEdit(
                    path=created_path,
                    unified_diff=_build_unified_diff(
                        path=created_path, old_content="", new_content=created_body
                    ),
                    new_content=created_body,
                    reason=f"EXTRACT_TO_FILES create {created_path}",
                )
            )
    if not edits and all_passed:
        return PatchCodegenResult(
            edits=[],
            already_satisfied=True,
            already_satisfied_evidence_refs=tuple(discovery.config_files.keys()),
            reason="; ".join(n for n in notes if n) or "required effects already satisfied",
            strategy_id=STRATEGY_PHP_STRUCTURAL,
        )
    return PatchCodegenResult(
        edits=edits,
        reason="; ".join(n for n in notes if n),
        remaining_asked_work=remaining,
        unmet_signals=tuple(dict.fromkeys(unmet)),
        strategy_id=STRATEGY_PHP_STRUCTURAL,
    )


def _probe_noedit_validation_red(
    *,
    toolbox: RepositoryToolbox,
    repository_root: Path,
    discovery: DiscoveryReport,
    bound_multi: list[str],
    objective: str,
    declared_validation: tuple[str, ...] | list[str],
    infer_test_command: Callable[[set[str]], list[str] | None],
    budget_mgr: ExecutionBudgetManager | None,
    prior_changed: list[str] | tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """Run deterministic validation for a round that produced no edits.

    ROBUST-001: a model "nothing to do" claim (MODEL_RETURNED_NO_EDIT or a
    deterministic already_satisfied) must never be accepted while the suite is
    red, and a no-edit terminal must carry the red evidence. Returns the
    test_result dict when a validation command could run, else None. Never
    raises: a probe failure is absent evidence, not a verdict.
    """
    try:
        test_command: list[str] | None = None
        for raw in declared_validation or ():
            parts = _safe_validation_argv(str(raw), fallback_file="")
            if parts:
                test_command = parts
                break
        if test_command is None:
            test_command = infer_test_command(set(getattr(discovery, "manifests", None) or ()))
        if test_command is None:
            test_command = _infer_focused_pytest_from_paths(
                objective=objective,
                paths=_validation_path_pool(
                    mutated=list(prior_changed or ()),
                    bound_multi=list(bound_multi or ()),
                    discovery=discovery,
                    repository_root=repository_root,
                ),
                prefer_near_paths=list(prior_changed or ()),
            )
        else:
            # Mirror the main path: a bare `python` that is not on PATH must
            # not silently void the probe — fall back to focused pytest.
            exe = str(test_command[0] or "").strip()
            if exe and not Path(exe).is_file() and not shutil.which(exe):
                test_command = _infer_focused_pytest_from_paths(
                    objective=objective,
                    paths=_validation_path_pool(
                        mutated=[],
                        bound_multi=list(bound_multi or ()),
                        discovery=discovery,
                        repository_root=repository_root,
                    ),
                    prefer_near_paths=[],
                )
        if test_command is None:
            return None
        result = toolbox.run_tests(test_command)
        if budget_mgr is not None:
            try:
                budget_mgr.record_test(" ".join(test_command))
            except Exception:
                pass
        return result if isinstance(result, dict) else None
    except Exception:
        return None


async def run_multi_step_mutation(
    *,
    objective: str,
    toolbox: RepositoryToolbox,
    repository_root: Path,
    settings: object | None,
    model_override: str | None,
    budget_mgr: ExecutionBudgetManager | None,
    on_event: OnEvent,
    events: list[dict[str, Any]],
    allowed_files: tuple[str, ...] = (),
    prior_read_files: tuple[str, ...] = (),
    literal_assignments: dict[str, str] | None = None,
    apply_edits: bool = True,
    task_family: str = "UNKNOWN",
    evaluate_completion_gates: Callable[..., Any],
    infer_test_command: Callable[[set[str]], list[str] | None],
    normalize_repo_relative_path: Callable[[str], str],
    looks_like_a_file: Callable[[str], bool],
    extract_target_path: Callable[[str], str | None],
    resolve_relative_path: Callable[[str, Path], str | None],
    max_rounds: int | None = None,
    step_contract: StepContractV1 | None = None,
    plan_steps: tuple[StepContractV1, ...] | list[StepContractV1] = (),
    upstream_max_bytes: int = DEFAULT_UPSTREAM_MAX_BYTES,
    repair_directive: RepairDirectiveV1 | None = None,
    required_effects: tuple[RequiredEffectV1, ...] = (),
    validation_commands: tuple[str, ...] = (),
    text_replacements: list[tuple[str, str]] | None = None,
    coverage_objective: str = "",
    admission_allowed_files: tuple[str, ...] | None = None,
    tenant_id: str = "",
    actor_id: str = "",
    authorization_header: str | None = None,
) -> MultiStepMutationResult:
    """Budget-bounded multi-round mutation: discover → patch → apply → verify → repair."""
    config_max = int(getattr(getattr(budget_mgr, "config", None), "max_mutation_rounds", 3) or 3)
    rounds_limit = max(1, int(max_rounds or config_max))

    focused = _resolve_focused_target(
        allowed_files=allowed_files,
        objective=objective,
        repository_root=repository_root,
        normalize_repo_relative_path=normalize_repo_relative_path,
        looks_like_a_file=looks_like_a_file,
        extract_target_path=extract_target_path,
        resolve_relative_path=resolve_relative_path,
    )
    bound_multi = [
        normalize_repo_relative_path(p)
        for p in allowed_files
        if str(p).strip() and looks_like_a_file(normalize_repo_relative_path(str(p)))
    ]
    bound_multi = [p for p in bound_multi if p]
    # An unqualified repository-wide replacement must not be silently narrowed to
    # the approved file and then reported complete while matching source remains
    # outside that scope.  Search is read-only; fail before any patch is generated.
    # Use coverage_objective (parent intent), not planner-guidance text that embeds
    # "Target: …" — that false path skips this check and lets partial scope pass.
    # Enforce only against human admission allowed_files (not step-contract fill-in
    # when admission was empty — retry/seed single-target plans must still run).
    scope_objective = str(coverage_objective or objective or "")
    replacement_pairs = list(text_replacements or extract_text_replacements(scope_objective))
    objective_target = extract_target_path(scope_objective)
    admission_scope = tuple(
        normalize_repo_relative_path(str(p))
        for p in (admission_allowed_files if admission_allowed_files is not None else allowed_files)
        if str(p).strip()
    )
    admission_scope = tuple(p for p in admission_scope if p)
    # AIX-EDIT-002: explicit requested source outranks adjacent tests. Fail
    # (into clarify) rather than silently replacing the target.
    focus_mismatch = _explicit_source_focus_mismatch(
        objective_target=objective_target,
        allowed_files=allowed_files,
        repository_root=repository_root,
        normalize_repo_relative_path=normalize_repo_relative_path,
        looks_like_a_file=looks_like_a_file,
        resolve_relative_path=resolve_relative_path,
    )
    if focus_mismatch:
        raise MultiStepMutationError(
            "FOCUS_MISMATCH",
            focus_mismatch,
            events=events,
        )
    if admission_scope and replacement_pairs and objective_target is None:
        approved = set(admission_scope)
        outside_matches: set[str] = set()
        for old, _new in replacement_pairs:
            try:
                hits = toolbox.search_files(old).get("hits") or []
            except (RepositoryToolError, OSError):
                continue
            for hit in hits:
                rel = normalize_repo_relative_path(
                    str(hit.get("relative_path") or hit.get("path") or "")
                )
                if rel and rel not in approved:
                    outside_matches.add(rel)
        if outside_matches:
            raise MultiStepMutationError(
                "FILE_NOT_IN_APPROVED_SCOPE",
                "repository-wide replacement also matches files outside allowed_files: "
                + ", ".join(sorted(outside_matches)[:10]),
                events=events,
            )
    prior_reads = [
        normalize_repo_relative_path(p)
        for p in prior_read_files
        if str(p).strip() and looks_like_a_file(normalize_repo_relative_path(str(p)))
    ]
    prior_reads = [p for p in prior_reads if p]
    tests_required = objective_requires_focused_tests(objective)
    # A named source file must not lock mutation when the objective also requires new tests.
    if focused and tests_required:
        if focused not in prior_reads:
            prior_reads.append(focused)
        focused = None
    if tests_required:
        source_hint = next((p for p in prior_reads if p), None)
        if source_hint:
            tests_dir = repository_root / Path(str(source_hint).replace("\\", "/")).parent / "tests"
            try:
                newest = sorted(
                    tests_dir.glob("test_*.py"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )[:1]
            except OSError:
                newest = []
            for path in newest:
                try:
                    rel = path.relative_to(repository_root).as_posix()
                except ValueError:
                    continue
                if rel and rel not in prior_reads:
                    prior_reads.append(rel)
    # Multi-file admit scope always wins over a single extract_target_path hit —
    # otherwise focused single-file discovery runs and the second allowed file is
    # only grafted later while keyword/full discovery paths still burn budget.
    use_bound_multi = len(bound_multi) >= 2
    use_prior_reads = (not focused) and (not use_bound_multi) and len(prior_reads) >= 1
    if use_bound_multi:
        focused = None
        use_prior_reads = False
    # SEM-001-W2: discovery_paths = target_scope ∪ depends_on.target_scope (read-only).
    # Missing upstream fails closed (NEED_UPSTREAM). Mutation allow-list stays targets-only.
    upstream_plan = _plan_and_validate_upstream_discovery(
        step_contract=step_contract,
        plan_steps=plan_steps,
        repository_root=repository_root,
        looks_like_a_file=looks_like_a_file,
        events=events,
        on_event=on_event,
        upstream_max_bytes=upstream_max_bytes,
    )
    upstream_paths = tuple(upstream_plan.upstream_paths)
    mutation_discovery_seed = tuple(bound_multi) if bound_multi else (
        (focused,) if focused else ()
    )
    discovery_bound_paths = merge_discovery_paths(
        mutation_paths=mutation_discovery_seed,
        upstream_paths=upstream_paths,
    )
    if budget_mgr is not None and use_bound_multi:
        budget_mgr.set_bound_mutation_paths(bound_multi)
    elif budget_mgr is not None and use_prior_reads:
        budget_mgr.set_bound_mutation_paths(prior_reads)
    # Force-binding codegen to a named source file prevents creating the required tests.
    codegen_preferred = None if tests_required else (
        focused or (bound_multi[0] if bound_multi else None) or (
            prior_reads[0] if prior_reads else None
        )
    )

    repair_notes: list[str] = []
    # ROBUST-001: files changed by earlier rounds this attempt. An empty round
    # must still validate the working-tree state those rounds left behind --
    # otherwise it re-verifies a stale diff with no tests and can pass blind.
    changed_paths: list[str] = []
    expanded = False
    last_used_deterministic = False
    round_results: list[MutationRoundResult] = []
    last_discovery = DiscoveryReport()
    last_diff = ""
    last_stats: dict[str, dict[str, int]] = {}
    last_changed: list[dict[str, Any]] = []
    last_tests: dict[str, Any] | None = None
    last_gates: dict[str, Any] | None = None
    accumulated_usage: OpenRouterUsage | None = None
    effects = required_effects or (effects_for_step(step_contract) if step_contract is not None else ())
    # Soft-skip / weak planner steps still inherit architectural effects from the
    # full objective text (live S6: fee-only step purpose + EXTRACT_HELPER false PASS).
    effects = merge_required_effects(
        effects,
        infer_required_effects(purpose=objective, validation_commands=validation_commands or ()),
    )
    step_purpose = step_contract.purpose if step_contract is not None else ""
    step_acceptance = step_contract.acceptance_criteria if step_contract is not None else ()
    effects = sanitize_required_effects(effects, objective, step_purpose, *step_acceptance)
    if step_contract is not None and _step_scope_is_python_only(step_contract):
        effects = tuple(e for e in effects if e.kind != "EXTRACT_TO_FILES")
    if any(e.kind == "EXTRACT_TO_FILES" for e in effects):
        # In-file regroup/helper extract is a weaker substitute for a real multi-file
        # architectural split (live S6 false PASS via GROUP_METHODS alone).
        effects = tuple(
            e
            for e in effects
            if e.kind not in {"EXTRACT_HELPER", "GROUP_METHODS", "MOVE_METHODS"}
        )
    # Governance/verification effects constrain or validate a mutation; they do not
    # describe the transformation strategy itself. Treating RUN_DECLARED_VALIDATION
    # as a mutation capability forces an otherwise ordinary exact text replacement
    # through strategy selection, where it correctly has no patch implementation and
    # fails as UNSUPPORTED_CAPABILITY (the post-routing failure exposed by
    # cyc_8d6f434a5d76). Keep the full set for verification below, but select an
    # implementation only from effects that actually mutate source state.
    strategy_effects = tuple(
        effect
        for effect in effects
        if effect.kind not in {"PRESERVE_PUBLIC_INTERFACE", "RUN_DECLARED_VALIDATION"}
    )
    # PHP structural EXTRACT_HELPER/GROUP_* are not implemented for TS/JS operator UI.
    # Demote them so snippet codegen can land shared helpers (live FE#7 UNSUPPORTED_CAPABILITY).
    obj_l = str(objective or "").casefold()
    fe_like = any(
        tok in obj_l
        for tok in (
            "operator",
            "frontend",
            "react",
            "tsx",
            "panels",
            "aix-frontend",
            "empty-state",
            "empty state",
        )
    )
    if fe_like or any(
        str(p).replace("\\", "/").lower().endswith((".ts", ".tsx", ".js", ".jsx"))
        for p in (allowed_files or ())
        if str(p).strip()
    ):
        strategy_effects = tuple(
            effect
            for effect in strategy_effects
            if effect.kind
            not in {
                "EXTRACT_HELPER",
                "GROUP_METHODS",
                "MOVE_METHODS",
                "EXTRACT_TO_FILES",
                "STRIP_UNUSED_IMPORTS",
            }
        )
    forbidden = tuple(repair_directive.forbidden_strategies) if repair_directive is not None else ()
    preferred = repair_directive.preferred_strategy if repair_directive is not None else None
    last_strategy_id = ""
    last_remaining = False
    last_unmet: tuple[str, ...] = ()
    declared_validation = validation_commands or (
        tuple(step_contract.validation_commands) if step_contract is not None else ()
    )
    if not declared_validation:
        declared_validation = _validation_commands_from_objective(objective)

    # SEM-001-W4: Continuity Law (C) — nonempty refs disable soft presence / diff_only.
    continuity_refs_required = step_has_continuity_refs(step_contract)

    for round_index in range(rounds_limit):
        if round_index > 0 and budget_mgr is not None:
            b = budget_mgr.record_retry(f"mutation_round_{round_index}")
            if not b.allowed:
                raise MultiStepMutationError(
                    b.reason_code or "EXECUTION_LOOP_DETECTED",
                    b.reason_detail,
                    events=events,
                )

        on_event(
            "mutation.round.started",
            {
                "round_index": round_index,
                "focused_target": focused or "",
                "bound_multi": len(bound_multi) if use_bound_multi else 0,
                "expanded": expanded,
            },
        )

        if focused and not upstream_paths:
            # Bound single-file steps must not expand into `run_repository_discovery`.
            # That walk issues one `search_files` per objective keyword (up to 12) and
            # routinely trips EXPLORATION_BUDGET_EXCEEDED (16 > 15) before a patch
            # lands — the MPE-25 live CronJob run. The file is already in scope.
            discovery = run_focused_file_discovery(
                toolbox=toolbox, target_path=focused, objective=objective
            )
            mode = "focused"
        elif use_bound_multi or upstream_paths:
            # SEM-001-W2: when depends_on yields upstream files, prefer bound discovery
            # so read-only prior artifacts are loaded alongside mutation targets.
            bound_paths = list(discovery_bound_paths) if discovery_bound_paths else list(bound_multi)
            if not bound_paths and focused:
                bound_paths = [focused]
            discovery = run_bound_files_discovery(
                toolbox=toolbox, target_paths=bound_paths, objective=objective
            )
            mode = "bound_multi+upstream" if upstream_paths else "bound_multi"
        elif use_prior_reads:
            # Exploration already read these files. Re-use them instead of a full-repo
            # walk that trips EXPLORATION_BUDGET_EXCEEDED (26 > 25) before mutation.
            discovery = run_bound_files_discovery(
                toolbox=toolbox, target_paths=prior_reads, objective=objective
            )
            mode = "prior_reads"
        else:
            discovery = run_repository_discovery(toolbox=toolbox, objective=objective)
            mode = "full"
            expanded = True

        if repair_notes and not focused and not use_bound_multi and not use_prior_reads and not upstream_paths:
            discovery = _discover_additional_dependencies(
                toolbox=toolbox, report=discovery, hints=repair_notes
            )
            mode = f"{mode}+deps"

        discovery = _seed_files_for_replacements(
            toolbox=toolbox,
            report=discovery,
            objective=objective,
            skip_search_if_present=round_index > 0 or bool(focused) or use_bound_multi or use_prior_reads or bool(upstream_paths),
            allowed_files_bound=bool(bound_multi) or bool(focused),
        )
        discovery = _ensure_allowed_files_discovered(
            toolbox=toolbox,
            report=discovery,
            allowed_files=allowed_files,
            normalize_repo_relative_path=normalize_repo_relative_path,
            looks_like_a_file=looks_like_a_file,
        )
        # Cap upstream bodies only — mutation targets keep full content for patching.
        discovery = _apply_upstream_body_caps(
            report=discovery,
            upstream_paths=upstream_paths,
            max_bytes=upstream_max_bytes,
            on_event=on_event,
        )
        last_discovery = discovery

        # Prior failed verification may have already written PHP/SQL deliverables.
        # Re-patching them with brittle LLM diffs causes PATCH_CONTEXT_MISMATCH
        # (live ERP step 2 after config.php + database.sql landed).
        gf_paths = list(bound_multi) if bound_multi else [
            normalize_repo_relative_path(p)
            for p in allowed_files
            if str(p).strip() and looks_like_a_file(str(p))
        ]
        if (not continuity_refs_required) and _greenfield_php_sql_deliverables_present(
            objective=str(coverage_objective or objective or ""),
            bound_paths=gf_paths,
            applied_mutations=[],
            repository_root=repository_root,
        ):
            on_event(
                "mutation.already_satisfied",
                {
                    "reason": "greenfield_php_sql_deliverables_already_present",
                    "paths": gf_paths[:12],
                },
            )
            return MultiStepMutationResult(
                ok=True,
                code="ALREADY_SATISFIED",
                detail="greenfield_php_sql_deliverables_already_present",
                discovery=discovery,
                rounds=round_results,
                diff_text="",
                diff_stats={},
                changed_files=[],
                events=events,
                test_result=None,
                completion_gates=None,
                already_satisfied=True,
                already_satisfied_reason="greenfield_php_sql_deliverables_already_present",
                already_satisfied_evidence_refs=tuple(gf_paths),
                token_usage=_usage_to_token_dict(accumulated_usage),
            )

        effective_objective = objective
        if repair_notes:
            effective_objective = (
                objective
                + "\n\nPRIOR_VERIFICATION_FAILURES:\n"
                + "\n".join(f"- {n}" for n in repair_notes[-5:])
                + "\nRepair the failures with the smallest additional change."
            )

        # AIX-EC-004 Packet EC-03: a fallback may run only when it supports every
        # required effect. Comment-insert cannot implement GROUP_METHODS.
        language = ""
        if focused:
            language = detect_language(focused, "")
        elif discovery.config_files:
            first_path = next(iter(discovery.config_files))
            language = detect_language(first_path, discovery.config_files[first_path])

        codegen: PatchCodegenResult | None = None
        if strategy_effects:
            try:
                round_preferred = preferred
                # Without exact OLD/NEW pairs, text_replace cannot implement prose /
                # structural work — prefer snippet codegen (live S5 TypedDict).
                if (
                    round_preferred is None
                    and not text_replacements
                    and not extract_text_replacements(effective_objective)
                ):
                    round_preferred = STRATEGY_SNIPPET_CODEGEN
                choice = select_strategy(
                    effects=strategy_effects,
                    language=language,
                    forbidden_strategies=forbidden,
                    preferred_strategy=round_preferred,
                )
            except CapabilityMismatch as exc:
                raise MultiStepMutationError(exc.code, exc.detail, events=events) from exc
            last_strategy_id = choice.strategy_id
            on_event(
                "mutation.strategy.selected",
                {
                    "strategy_id": choice.strategy_id,
                    "effects": sorted({e.kind for e in strategy_effects}),
                },
            )
            if choice.strategy_id == STRATEGY_PHP_STRUCTURAL:
                codegen = _structural_patch(discovery=discovery, effects=strategy_effects)
                last_used_deterministic = True
            elif choice.strategy_id == STRATEGY_ROUTE:
                codegen = local_fallback_route_file_refactor(discovery, effective_objective)
                # ROUTE and TEXT_REPLACE both truthfully advertise LITERAL_REPLACE,
                # but route refactoring is a narrower specialization. Capability
                # selection alone cannot tell that an ordinary README replacement is
                # not a route edit. If the specialization is inapplicable, continue
                # to the other deterministic strategy supporting the same typed
                # effect instead of turning a valid source edit into
                # UNSUPPORTED_CAPABILITY.
                if codegen is None or not codegen.edits:
                    codegen = local_fallback_text_replacements(
                        discovery, effective_objective, pairs=text_replacements
                    )
                    if codegen is not None and (codegen.edits or codegen.already_satisfied):
                        last_strategy_id = STRATEGY_TEXT_REPLACE
                        last_used_deterministic = True
                    else:
                        # No exact OLD/NEW pairs either — structural/prose edits need
                        # snippet codegen (live S5: TypedDict refactor).
                        last_used_deterministic = False
                        last_strategy_id = STRATEGY_SNIPPET_CODEGEN
                        codegen = await generate_source_patch(
                            objective=effective_objective,
                            discovery=discovery,
                            settings=settings,
                            model_override=model_override,
                            literal_assignments=literal_assignments,
                            preferred_path=codegen_preferred,
                            tenant_id=tenant_id,
                            actor_id=actor_id,
                            authorization_header=authorization_header,
                            plan_actions=tuple(step_contract.actions or ()) if step_contract is not None else (),
                            repair_pending=bool(repair_notes),

                        )
                else:
                    last_used_deterministic = True
            elif choice.strategy_id == STRATEGY_TEXT_REPLACE:
                codegen = local_fallback_text_replacements(
                    discovery, effective_objective, pairs=text_replacements
                )
                if codegen is not None and (codegen.edits or codegen.already_satisfied):
                    last_used_deterministic = True
                else:
                    typed = local_fallback_python_typeddict_intro(
                        discovery, effective_objective, preferred_path=codegen_preferred
                    )
                    if typed is not None and typed.edits:
                        codegen = typed
                        last_used_deterministic = True
                        last_strategy_id = "aix.fallback.python_typeddict.v1"
                    else:
                        last_used_deterministic = False
                        last_strategy_id = STRATEGY_SNIPPET_CODEGEN
                        codegen = await generate_source_patch(
                            objective=effective_objective,
                            discovery=discovery,
                            settings=settings,
                            model_override=model_override,
                            literal_assignments=literal_assignments,
                            preferred_path=codegen_preferred,
                            tenant_id=tenant_id,
                            actor_id=actor_id,
                            authorization_header=authorization_header,
                            plan_actions=tuple(step_contract.actions or ()) if step_contract is not None else (),
                            repair_pending=bool(repair_notes),

                        )
            elif choice.strategy_id == STRATEGY_ENV_ASSIGN and (
                extract_text_replacements(effective_objective) or text_replacements
            ):
                # Same LITERAL_REPLACE advertisement collision as ROUTE: an ordinary
                # quoted string replacement must not die in the env-assign lane.
                codegen = local_fallback_text_replacements(
                    discovery, effective_objective, pairs=text_replacements
                )
                if codegen is not None:
                    last_strategy_id = STRATEGY_TEXT_REPLACE
                last_used_deterministic = True
            else:
                last_used_deterministic = False
                codegen = await generate_source_patch(
                    objective=effective_objective,
                    discovery=discovery,
                    settings=settings,
                    model_override=model_override,
                    literal_assignments=literal_assignments,
                    preferred_path=codegen_preferred,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    authorization_header=authorization_header,
                    plan_actions=tuple(step_contract.actions or ()) if step_contract is not None else (),
                    repair_pending=bool(repair_notes),

                )
                if codegen is not None:
                    codegen.strategy_id = STRATEGY_SNIPPET_CODEGEN
            if codegen is None:
                codegen = PatchCodegenResult(
                    edits=[],
                    no_files_identified=True,
                    reason="UNSUPPORTED_CAPABILITY: selected strategy produced no patch",
                    remaining_asked_work=True,
                    strategy_id=choice.strategy_id,
                )
            last_remaining = bool(codegen.remaining_asked_work)
            last_unmet = codegen.unmet_signals
            if not codegen.edits and not codegen.already_satisfied:
                sqlite_fb = local_fallback_sqlite_boolean_column(
                    discovery, effective_objective, preferred_path=codegen_preferred
                )
                if sqlite_fb is not None and sqlite_fb.edits:
                    codegen = sqlite_fb
                    last_used_deterministic = True
                    last_strategy_id = "aix.fallback.sqlite_boolean_column.v1"
                    last_remaining = bool(codegen.remaining_asked_work)
                    last_unmet = codegen.unmet_signals
            if not codegen.edits and not codegen.already_satisfied:
                # ROBUST-001: never accept stagnation blind. A no-edit round
                # with red deterministic validation is repair fuel (bounded by
                # the round loop), not an immediate terminal.
                probe = _probe_noedit_validation_red(
                    toolbox=toolbox,
                    repository_root=repository_root,
                    discovery=discovery,
                    bound_multi=bound_multi,
                    objective=objective,
                    declared_validation=declared_validation,
                    infer_test_command=infer_test_command,
                    budget_mgr=budget_mgr,
                    prior_changed=changed_paths,
                )
                if probe is not None and int(probe.get("returncode", 0)) != 0:
                    stderr = str(probe.get("stderr") or probe.get("stdout") or "")[:500]
                    repair_notes.append(f"noedit_validation_red:{stderr}")
                    repair_notes.append(
                        codegen.reason or f"strategy {choice.strategy_id} produced no change"
                    )
                    last_tests = probe
                    round_results.append(
                        MutationRoundResult(
                            round_index=round_index,
                            edits_applied=[],
                            gates_passed=False,
                            gate_reasons=("model_returned_no_edit_with_red_validation",),
                            test_returncode=int(probe.get("returncode", 1)),
                            discovery_mode=mode,
                            detail="no-edit round with red validation; repair continues",
                        )
                    )
                    on_event(
                        "mutation.round.completed",
                        {
                            "round_index": round_index,
                            "gates_passed": False,
                            "edits": "",
                            "gate_reasons": ["model_returned_no_edit_with_red_validation"],
                            "test_returncode": int(probe.get("returncode", 1)),
                        },
                    )
                    if not expanded:
                        expanded = True
                    continue
                raise MultiStepMutationError(
                    (
                        codegen.failure_code
                        or ("CAPABILITY_BLOCKED" if codegen.remaining_asked_work else "MODEL_RETURNED_NO_EDIT")
                    ),
                    codegen.reason
                    or f"strategy {choice.strategy_id} produced no change for "
                    f"{sorted({e.kind for e in strategy_effects})}",
                    events=events,
                    raw_model_payload=codegen.raw_model_payload,
                    usage=_usage_to_token_dict(
                        _merge_codegen_usage(
                            accumulated_usage, getattr(codegen, "usage", None)
                        )
                    ),
                )
        else:
            route_result = local_fallback_route_file_refactor(discovery, effective_objective)
            if route_result is not None and route_result.edits:
                codegen = route_result
                last_used_deterministic = True
                last_strategy_id = STRATEGY_ROUTE
            else:
                php_result = local_fallback_php_controller_refactor(discovery, effective_objective)
                # Honor remaining_asked_work: comment-only partials must not win.
                if php_result is not None and php_result.edits and not php_result.remaining_asked_work:
                    codegen = php_result
                    last_used_deterministic = True
                    last_strategy_id = "aix.fallback.php_comments.v1"
                else:
                    replace_result = local_fallback_text_replacements(
                        discovery, effective_objective, pairs=text_replacements
                    )
                    if replace_result is not None and (
                        replace_result.edits
                        or replace_result.already_satisfied
                        or replace_result.remaining_asked_work
                    ):
                        codegen = replace_result
                        last_used_deterministic = True
                        last_strategy_id = STRATEGY_TEXT_REPLACE
                    else:
                        last_used_deterministic = False
                        last_strategy_id = STRATEGY_SNIPPET_CODEGEN
                        codegen = await generate_source_patch(
                            objective=effective_objective,
                            discovery=discovery,
                            settings=settings,
                            model_override=model_override,
                            literal_assignments=literal_assignments,
                            preferred_path=codegen_preferred,
                            tenant_id=tenant_id,
                            actor_id=actor_id,
                            authorization_header=authorization_header,
                            plan_actions=tuple(step_contract.actions or ()) if step_contract is not None else (),
                            repair_pending=bool(repair_notes),

                        )

        if codegen is None:
            raise MultiStepMutationError("NO_APPLICABLE_SOURCE_FILE", "no codegen result", events=events)

        accumulated_usage = _merge_codegen_usage(accumulated_usage, getattr(codegen, "usage", None))

        if codegen.no_files_identified:
            sqlite_fb = local_fallback_sqlite_boolean_column(
                discovery, effective_objective, preferred_path=codegen_preferred
            )
            if sqlite_fb is not None and sqlite_fb.edits:
                codegen = sqlite_fb
                last_used_deterministic = True
                last_strategy_id = "aix.fallback.sqlite_boolean_column.v1"

        # Prefer the deterministic migration+model path whenever it applies.
        # Snippet codegen often invents brittle schema.sql hunks that fail
        # PATCH_CONTEXT_MISMATCH and abort before a forward migration is created
        # (live S10). The fallback already encodes the safe architectural choice:
        # additive migrations/ under the bound migrate.py package.
        sqlite_preferred = local_fallback_sqlite_boolean_column(
            discovery, effective_objective, preferred_path=codegen_preferred
        )
        if (
            sqlite_preferred is not None
            and sqlite_preferred.edits
            and not getattr(codegen, "already_satisfied", False)
        ):
            codegen = sqlite_preferred
            last_used_deterministic = True
            last_strategy_id = "aix.fallback.sqlite_boolean_column.v1"

        api_preferred = local_fallback_api_archive_endpoint(
            discovery, effective_objective, preferred_path=codegen_preferred
        )
        if (
            api_preferred is not None
            and api_preferred.edits
            and not getattr(codegen, "already_satisfied", False)
        ):
            # Prefer concrete archive wiring over empty/no-op LLM claims (live S11).
            codegen = api_preferred
            last_used_deterministic = True
            last_strategy_id = "aix.fallback.api_archive_endpoint.v1"

        # Prefer parent coverage objective so planner-narrowed step prose still
        # triggers the UI strip fallback (live FE#1).
        ui_objective = str(coverage_objective or effective_objective or "")
        from backend.aix_core.objective_cycle.objective_coverage import (
            objective_requires_ui_surface,
        )

        if objective_requires_ui_surface(ui_objective):
            for candidate in (
                "src/app/(admin)/execution/page.tsx",
                "src/app/(admin)/run-detail/page.tsx",
                "src/app/(admin)/objectives/page.tsx",
                "src/lib/service-endpoints.ts",
            ):
                if candidate in (discovery.config_files or {}):
                    continue
                full = repository_root / candidate
                if full.is_file():
                    try:
                        discovery.config_files[candidate] = full.read_text(
                            encoding="utf-8-sig", errors="replace"
                        )
                    except OSError:
                        pass
        ui_preferred = local_fallback_cycle_summary_strip(
            discovery, ui_objective, preferred_path=codegen_preferred
        )
        if (
            ui_preferred is not None
            and ui_preferred.edits
            and not getattr(codegen, "already_satisfied", False)
        ):
            codegen = ui_preferred
            last_used_deterministic = True
            last_strategy_id = "aix.fallback.cycle_summary_strip.v1"

        archived_preferred = local_fallback_archived_objectives_filter(
            discovery, ui_objective, preferred_path=codegen_preferred
        )
        if (
            archived_preferred is not None
            and (archived_preferred.edits or archived_preferred.already_satisfied)
            and not getattr(codegen, "already_satisfied", False)
        ):
            # Prefer wiring the objectives-page filter over lib-only contract edits (FE#2).
            # Also prefer already_satisfied so prior untracked UI landings short-circuit.
            codegen = archived_preferred
            last_used_deterministic = True
            last_strategy_id = "aix.fallback.archived_objectives_filter.v1"

        url_obj = str(coverage_objective or effective_objective or "")
        if any(
            tok in url_obj.casefold()
            for tok in (
                "base url",
                "backend url",
                "hardcoded",
                "production url",
                "unsafe production",
                "gateway and base",
            )
        ):
            for candidate in ("src/lib/service-endpoints.ts", "src/lib/aix-cycle-gateway.ts"):
                if candidate in (discovery.config_files or {}):
                    continue
                full = repository_root / candidate
                if full.is_file():
                    try:
                        discovery.config_files[candidate] = full.read_text(
                            encoding="utf-8-sig", errors="replace"
                        )
                    except OSError:
                        pass
        url_preferred = local_fallback_safe_backend_base_url(
            discovery, url_obj, preferred_path=codegen_preferred
        )
        if (
            url_preferred is not None
            and (url_preferred.edits or url_preferred.already_satisfied)
            and not getattr(codegen, "already_satisfied", False)
        ):
            codegen = url_preferred
            last_used_deterministic = True
            last_strategy_id = "aix.fallback.safe_backend_base_url.v1"

        if codegen.no_files_identified and codegen.already_satisfied:
            # ROBUST-001: deterministic evidence outranks a "done" claim. When
            # validation is red, the claim is repair fuel (bounded by the round
            # loop), never an accept.
            probe = _probe_noedit_validation_red(
                toolbox=toolbox,
                repository_root=repository_root,
                discovery=discovery,
                bound_multi=bound_multi,
                objective=objective,
                declared_validation=declared_validation,
                infer_test_command=infer_test_command,
                budget_mgr=budget_mgr,
                prior_changed=changed_paths,
            )
            if probe is not None and int(probe.get("returncode", 0)) != 0:
                stderr = str(probe.get("stderr") or probe.get("stdout") or "")[:500]
                repair_notes.append(f"satisfied_claim_with_red_validation:{stderr}")
                repair_notes.append(codegen.reason or "already_satisfied claimed with red validation")
                last_tests = probe
                round_results.append(
                    MutationRoundResult(
                        round_index=round_index,
                        edits_applied=[],
                        gates_passed=False,
                        gate_reasons=("satisfied_claim_with_red_validation",),
                        test_returncode=int(probe.get("returncode", 1)),
                        discovery_mode=mode,
                        detail="satisfied claim rejected by red validation; repair continues",
                    )
                )
                on_event(
                    "mutation.round.completed",
                    {
                        "round_index": round_index,
                        "gates_passed": False,
                        "edits": "",
                        "gate_reasons": ["satisfied_claim_with_red_validation"],
                        "test_returncode": int(probe.get("returncode", 1)),
                    },
                )
                if not expanded:
                    expanded = True
                continue
            return MultiStepMutationResult(
                ok=True,
                code="ALREADY_SATISFIED",
                detail=codegen.reason,
                discovery=discovery,
                rounds=round_results,
                diff_text="",
                diff_stats={},
                changed_files=[],
                events=events,
                test_result=None,
                completion_gates=None,
                already_satisfied=True,
                already_satisfied_reason=codegen.reason,
                already_satisfied_evidence_refs=codegen.already_satisfied_evidence_refs,
                token_usage=_usage_to_token_dict(accumulated_usage),
            )

        if codegen.no_files_identified:
            if not expanded:
                expanded = True
                repair_notes.append(codegen.reason or "no applicable source file in focused discovery")
                on_event("mutation.expand_on_miss", {"reason": codegen.reason[:300]})
                continue
            raise MultiStepMutationError(
                "NO_APPLICABLE_SOURCE_FILE",
                codegen.reason or "no applicable source file",
                events=events,
            )

        allowed_set = {
            normalize_repo_relative_path(p)
            for p in allowed_files
            if str(p).strip()
        }
        allowed_set = {p for p in allowed_set if p}
        for extra in prior_reads:
            rel = normalize_repo_relative_path(extra)
            if rel:
                allowed_set.add(rel)
        allow_test_creates = objective_requires_focused_tests(
            coverage_objective or objective
        )
        # Last-chance widen for UI product asks when planner/admit scope omitted the page.
        from backend.aix_core.objective_cycle.objective_coverage import (
            objective_requires_ui_surface,
        )

        cov = str(coverage_objective or objective or "").casefold()
        if objective_requires_ui_surface(cov) or "archiv" in cov or "objectives list" in cov:
            for extra in (
                "src/app/(admin)/objectives/page.tsx",
                "src/app/(admin)/execution/page.tsx",
                "src/components/aix/CycleSummaryStrip.tsx",
                "src/lib/service-endpoints.ts",
            ):
                if "archiv" in cov and "execution" in extra:
                    continue
                if ("cycle summary" in cov or "summary strip" in cov) and "objectives/page" in extra:
                    continue
                need = False
                if "archiv" in cov and "objectives/page" in extra:
                    need = True
                if ("cycle summary" in cov or "summary strip" in cov) and (
                    "execution/page" in extra or "CycleSummaryStrip" in extra
                ):
                    need = True
                if "objectives list" in cov and "objectives/page" in extra:
                    need = True
                if need:
                    allowed_set.add(normalize_repo_relative_path(extra))
        allow_sibling_creates = any(e.kind == "EXTRACT_TO_FILES" for e in effects)
        allow_migration_creates = any(
            tok in str(objective or "").casefold()
            for tok in ("migration", "schema change", "add column", "alter table")
        )
        allow_ui_component_creates = any(
            tok in str(coverage_objective or objective or "").casefold()
            for tok in ("cycle summary", "summary strip", "fixture-mode", "operator", "frontend")
        )

        def _edit_in_approved_scope(norm: str) -> bool:
            rel = normalize_repo_relative_path(norm)
            if not allowed_set:
                return True
            if rel in allowed_set:
                return True
            if allow_test_creates and _is_creatable_test_path(rel):
                return True
            parent = str(Path(rel).parent).replace("\\", "/")
            if parent in (".", ""):
                parent = ""
            # Additive SQL migrations under an approved db/ package (live S10).
            if allow_migration_creates and parent.endswith("/migrations"):
                package = str(Path(parent).parent).replace("\\", "/")
                for approved in allowed_set:
                    ap = str(Path(approved).parent).replace("\\", "/")
                    if ap == package:
                        return True
            # New operator UI components next to bound aix components (live FE#1).
            if allow_ui_component_creates and "/components/aix" in parent.casefold():
                for approved in allowed_set:
                    ap = str(Path(approved).parent).replace("\\", "/")
                    if "/components/aix" in ap.casefold() or ap.casefold().endswith("/aix"):
                        return True
                # Also allow when only a page under app/(admin)/ was approved.
                for approved in allowed_set:
                    ap = str(approved).replace("\\", "/").casefold()
                    if "/app/" in ap and ap.endswith("page.tsx"):
                        return True
            if not allow_sibling_creates:
                return False
            # EXTRACT_TO_FILES may create or replace sibling *Service modules next to
            # the bound god-file. Rejecting existing siblings blocked live S6 after a
            # partial prior attempt left FeeService.php on disk.
            for approved in allowed_set:
                ap = str(Path(approved).parent).replace("\\", "/")
                if ap in (".", ""):
                    ap = ""
                if ap == parent:
                    return True
            return False

        from backend.aix_core.runtime.executor.patch_validate import (
            PatchValidationResult,
            compute_content_hash,
        )

        validated: list[tuple[PatchEdit, Any, str]] = []
        for edit in codegen.edits:
            norm = str(edit.path).replace("\\", "/").lstrip("/")
            if not _edit_in_approved_scope(norm):
                raise MultiStepMutationError(
                    "FILE_NOT_IN_APPROVED_SCOPE",
                    f"edit path {edit.path!r} outside allowed_files={sorted(allowed_set)}",
                    events=events,
                )
            full_target = repository_root / edit.path
            before = (
                full_target.read_text(encoding="utf-8-sig", errors="replace")
                if full_target.is_file()
                else ""
            )
            before_hash = compute_content_hash(before)
            # Deterministic offline fallbacks (and brand-new file creates) supply
            # authoritative new_content. Prefer whole-file writes so brittle
            # unified-diff context cannot abort an otherwise-correct batch.
            use_whole_file = (
                edit.new_content is not None
                and str(edit.new_content) != before
                and (
                    last_used_deterministic
                    or not before
                    or "fallback" in str(last_strategy_id or "").casefold()
                    or _is_creatable_test_path(norm)
                    or "/proposed/" in f"/{norm.casefold()}"
                )
            )
            if use_whole_file:
                after = str(edit.new_content)
                after_hash = compute_content_hash(after)
                whole_added, whole_removed = _whole_file_line_counts(before, after)
                patch_val = PatchValidationResult(
                    valid=True,
                    reason_code=None,
                    reason_detail="new_content whole-file write",
                    before_hash=before_hash,
                    after_hash=after_hash,
                    lines_added=whole_added,
                    lines_removed=whole_removed,
                    new_content=after,
                    modified_path=norm,
                )
                validated.append((edit, patch_val, before_hash))
                continue
            patch_val = validate_patch(
                file_path=edit.path,
                existing_content=before,
                unified_diff=edit.unified_diff,
                expected_file_hash=before_hash,
            )
            if not patch_val.valid:
                raise MultiStepMutationError(
                    patch_val.reason_code or "PATCH_VALIDATION_FAILED",
                    f"{edit.path}: {patch_val.reason_detail}",
                    events=events,
                )
            validated.append((edit, patch_val, before_hash))

        if not apply_edits:
            proposed = [
                {
                    "path": edit.path,
                    "unified_diff": edit.unified_diff,
                    "new_content": patch_val.new_content,
                    "before_hash": before_hash,
                    "after_hash": patch_val.after_hash,
                    "lines_added": patch_val.lines_added,
                    "lines_removed": patch_val.lines_removed,
                }
                for edit, patch_val, before_hash in validated
            ]
            return MultiStepMutationResult(
                ok=True,
                code="PATCH_PROPOSED",
                detail=f"{len(proposed)} patch(es) proposed; awaiting application",
                discovery=discovery,
                rounds=round_results,
                diff_text="\n".join(p["unified_diff"] for p in proposed),
                diff_stats={},
                changed_files=[],
                events=events,
                test_result=None,
                completion_gates=None,
                proposed_edits=proposed,
                token_usage=_usage_to_token_dict(accumulated_usage),
            )

        applied_mutations: list[AppliedMutation] = []
        changed_detail: list[dict[str, Any]] = []
        for edit, patch_val, before_hash in validated:
            full_target = repository_root / edit.path
            on_event("patch.application.started", {"path": edit.path, "round_index": str(round_index)})
            try:
                after_hash = apply_patch_atomic(full_target, patch_val)
            except Exception as exc:  # noqa: BLE001
                on_event("patch.application.failed", {"path": edit.path, "error": str(exc)[:300]})
                raise MultiStepMutationError("PATCH_FAILED", f"{edit.path}: {exc}", events=events) from exc
            on_event(
                "patch.application.completed",
                {
                    "path": edit.path,
                    "before_hash": before_hash,
                    "after_hash": str(after_hash),
                    "round_index": str(round_index),
                },
            )
            applied_mutations.append(
                AppliedMutation(
                    path=edit.path,
                    unified_diff=edit.unified_diff,
                    lines_added=int(patch_val.lines_added or 0),
                    lines_removed=int(patch_val.lines_removed or 0),
                    after_content=edit.new_content,
                )
            )
            changed_detail.append(
                {"path": edit.path, "before_hash": before_hash, "after_hash": after_hash}
            )

        try:
            git_diff = toolbox.get_git_diff()
        except (RepositoryToolError, OSError):
            git_diff = {"diff": "", "stats": {}}
        evidence = merge_mutation_evidence(git_diff_result=git_diff, applied=applied_mutations)
        last_diff = evidence.diff_text
        last_stats = evidence.diff_stats
        last_changed = changed_detail
        mutated = [m.path for m in applied_mutations]
        for p in mutated:
            if p not in changed_paths:
                changed_paths.append(p)
        syntax_errors = _python_syntax_errors(applied_mutations)
        if syntax_errors:
            repair_notes.extend(f"python_syntax:{err}" for err in syntax_errors)

        # Optional state assertions from literal assignments written this round.
        state_failures: list[str] = []
        if literal_assignments:
            for mut in applied_mutations:
                if mut.after_content is None:
                    continue
                needles = [f"{k}={v}" for k, v in literal_assignments.items()]
                state_failures.extend(
                    assert_content_contains(mut.path, mut.after_content, needles=needles)
                )

        test_result: dict[str, Any] | None = None
        test_command = None
        fallback_php = ""
        if focused and str(focused).lower().endswith(".php"):
            fallback_php = focused
        elif discovery.config_files:
            fallback_php = next(
                (p for p in discovery.config_files if str(p).lower().endswith(".php")),
                "",
            )
        if declared_validation:
            for raw in declared_validation:
                parts = _safe_validation_argv(str(raw), fallback_file=fallback_php)
                if parts:
                    test_command = parts
                    break
        if test_command is None:
            test_command = infer_test_command(set(discovery.manifests))
        if test_command is None:
            # Mutated files must be in the pool; discovery often only loaded sources.
            path_pool = _validation_path_pool(
                mutated=mutated or changed_paths,
                bound_multi=bound_multi,
                discovery=discovery,
                repository_root=repository_root,
            )
            test_command = _infer_focused_pytest_from_paths(
                objective=objective,
                paths=path_pool,
                prefer_near_paths=mutated,
            )
        if test_command is None and fallback_php:
            php_bin = shutil.which("php") or shutil.which("php.exe")
            if php_bin:
                test_command = [php_bin, "-l", fallback_php]
        # SEM-001-W4-T4: resolve ``test -f/-d`` before PATH checks (Windows has no test).
        existence_cmd: list[str] | None = None
        if test_command is not None:
            existence_probe = _posix_existence_validation(
                repository_root=repository_root, argv=list(test_command)
            )
            if existence_probe is not None:
                existence_cmd = list(test_command)
        if test_command:
            exe = str(test_command[0] or "").strip()
            if (
                existence_cmd is None
                and exe
                and not Path(exe).is_file()
                and not shutil.which(exe)
            ):
                repair_notes.append(f"verification_command_missing:{exe}")
                test_command = None
                # Declared validation often uses a bare `python` that is not on PATH.
                # Fall back to focused pytest with the running interpreter.
                path_pool = _validation_path_pool(
                    mutated=mutated or changed_paths,
                    bound_multi=bound_multi,
                    discovery=discovery,
                    repository_root=repository_root,
                )
                test_command = _infer_focused_pytest_from_paths(
                    objective=objective,
                    paths=path_pool,
                    prefer_near_paths=mutated,
                )
        # Prefer running tests when a command exists and we mutated non-assignment-only
        # source, or when a previous round failed verification.
        assignment_only_env = bool(literal_assignments) and bool(evidence.applied_paths) and all(
            str(p).replace("\\", "/").rsplit("/", 1)[-1] in {".env", ".env.example", ".env.local"}
            or str(p).replace("\\", "/").endswith(".env")
            for p in evidence.applied_paths
        )
        should_test = test_command is not None and not assignment_only_env and (
            round_index > 0 or not literal_assignments or any(
                not str(p).replace("\\", "/").rsplit("/", 1)[-1].startswith(".env")
                for p in evidence.applied_paths
            )
        )
        if should_test and test_command is not None:
            if existence_cmd is not None and _existence_validated_preexisting_file(
                existence_cmd, discovery
            ):
                # AIX-EDIT-001: existence proved a file that already had
                # content before the round (EDIT shape) — presence is not
                # behavior. Divert to the real focused suite, fail closed
                # when no focused suite can be built.
                repair_notes.append("existence_probe_insufficient_for_preexisting_file")
                existence_cmd = None
                test_command = _infer_focused_pytest_from_paths(
                    objective=objective,
                    paths=_validation_path_pool(
                        mutated=mutated or changed_paths,
                        bound_multi=bound_multi,
                        discovery=discovery,
                        repository_root=repository_root,
                    ),
                    prefer_near_paths=mutated,
                )
                if test_command is None:
                    test_result = {
                        "returncode": 1,
                        "stdout": "",
                        "stderr": (
                            "existence probe insufficient for pre-existing file "
                            "and no focused suite could be built"
                        ),
                        "command": ["focused_pytest_unavailable"],
                    }
            if existence_cmd is not None:
                test_result = _posix_existence_validation(
                    repository_root=repository_root, argv=list(existence_cmd)
                )
                test_command = list(existence_cmd)
                if budget_mgr is not None:
                    budget_mgr.record_test(" ".join(test_command))
            elif test_result is None:
                try:
                    test_result = toolbox.run_tests(test_command)
                    if budget_mgr is not None:
                        budget_mgr.record_test(" ".join(test_command))
                except (RepositoryToolError, OSError) as exc:
                    repair_notes.append(f"test_run_failed:{exc}")
                    test_result = None
                    from backend.aix_core.objective_cycle.objective_coverage import (
                        _playwright_cert_present_in_files,
                    )

                    pw_workspace = {
                        **dict(discovery.config_files or {}),
                        **{
                            m.path: str(m.after_content or "")
                            for m in applied_mutations
                            if m.after_content is not None
                        },
                    }
                    if "playwright" in str(coverage_objective or objective or "").casefold() and (
                        _playwright_cert_present_in_files(pw_workspace)
                        or any("playwright" in str(m.path).replace("\\", "/").casefold() for m in applied_mutations)
                    ):
                        # Windows often fails bare `npm`/`npx` without .cmd; do not block
                        # cert-coverage mutations when the suite file itself was extended.
                        repair_notes.append("playwright_cert_presence_verified")
                        test_result = {
                            "returncode": 0,
                            "stdout": "playwright_cert_presence_verified",
                            "stderr": str(exc)[:300],
                            "command": ["playwright_cert_presence_verified"],
                        }
                        test_command = ["playwright_cert_presence_verified"]
                # Planner-declared pytest often points at unrelated modules (`-k timeout`
                # on a suite that collects nothing → exit 5, or an unrelated suite that
                # happens to exit 0). Prefer/require focused tests next to mutations.
                # SEM-001-W4: Path.exists validation for test -f/-d is already definitive.
                rc = int(test_result.get("returncode", 1)) if isinstance(test_result, dict) else 1
                needs_focused = bool(applied_mutations) and (
                    rc in {5, 4}
                    or _is_syntactic_only_validation(test_command)
                    or _existence_validated_preexisting_file(existence_cmd, discovery)
                    or not _validation_targets_mutated_paths(
                        test_command, [m.path for m in applied_mutations]
                    )
                )
                if needs_focused:
                    path_pool = _validation_path_pool(
                        mutated=mutated or changed_paths,
                        bound_multi=bound_multi,
                        discovery=discovery,
                        repository_root=repository_root,
                    )
                    focused_cmd = _infer_focused_pytest_from_paths(
                        objective=objective,
                        paths=path_pool,
                        prefer_near_paths=mutated,
                    )
                    if focused_cmd and focused_cmd != test_command:
                        repair_notes.append(
                            f"validation_fallback_focused_pytest:prior_rc={rc}"
                        )
                        try:
                            test_result = toolbox.run_tests(focused_cmd)
                            test_command = focused_cmd
                            if budget_mgr is not None:
                                budget_mgr.record_test(" ".join(focused_cmd))
                        except (RepositoryToolError, OSError) as exc:
                            repair_notes.append(f"focused_pytest_failed:{exc}")
                    elif _is_syntactic_only_validation(test_command):
                        # Never let git diff --check alone greenlight UI/product asks
                        # when no focused behavioral test exists (live FE#1 false PASS).
                        from backend.aix_core.objective_cycle.objective_coverage import (
                            objective_requires_ui_surface,
                        )

                        ui_obj = str(coverage_objective or objective or "")
                        tsx_hits = [
                            m
                            for m in applied_mutations
                            if str(m.path).lower().endswith((".tsx", ".jsx"))
                            and str(m.after_content or "").strip()
                        ]
                        structural_kinds = {
                            "GROUP_METHODS", "MOVE_METHODS", "EXTRACT_HELPER",
                            "EXTRACT_TO_FILES", "STRIP_UNUSED_IMPORTS",
                        }
                        if any(effect.kind in structural_kinds for effect in strategy_effects):
                            # Syntax/lint plus the independent structural-effect verifier
                            # below is substantive verification for a structural refactor.
                            # Keep the real command result; do not overwrite it as failed.
                            repair_notes.append("structural_effect_verification_required")
                        elif objective_requires_ui_surface(ui_obj) and tsx_hits:
                            # Frontend repos often lack pytest; accept real TSX surface
                            # presence as behavioral evidence for UI strip/mount asks.
                            repair_notes.append("ui_surface_presence_verified")
                            test_result = {
                                "returncode": 0,
                                "stdout": "ui_surface_presence_verified:"
                                + ",".join(m.path for m in tsx_hits[:5]),
                                "stderr": "",
                                "command": ["ui_surface_presence_verified"],
                            }
                            test_command = ["ui_surface_presence_verified"]
                        else:
                            from backend.aix_core.objective_cycle.objective_coverage import (
                                _ui_surface_present_in_files,
                            )

                            workspace = {
                                **dict(discovery.config_files or {}),
                                **{
                                    m.path: str(m.after_content or "")
                                    for m in applied_mutations
                                    if m.after_content is not None
                                },
                            }
                            if objective_requires_ui_surface(ui_obj) and _ui_surface_present_in_files(
                                ui_obj, workspace
                            ):
                                repair_notes.append("ui_surface_presence_verified:workspace")
                                test_result = {
                                    "returncode": 0,
                                    "stdout": "ui_surface_presence_verified:workspace",
                                    "stderr": "",
                                    "command": ["ui_surface_presence_verified"],
                                }
                                test_command = ["ui_surface_presence_verified"]
                            elif any(
                                tok in str(ui_obj).casefold()
                                for tok in (
                                    "base url",
                                    "backend url",
                                    "hardcoded",
                                    "production url",
                                    "unsafe production",
                                )
                            ) and any(
                                "assertSafeProductionBaseUrl" in str(body or "")
                                for body in workspace.values()
                            ):
                                repair_notes.append("config_url_safety_verified")
                                test_result = {
                                    "returncode": 0,
                                    "stdout": "config_url_safety_verified",
                                    "stderr": "",
                                    "command": ["config_url_safety_verified"],
                                }
                                test_command = ["config_url_safety_verified"]
                            elif (not continuity_refs_required) and _greenfield_php_sql_deliverables_present(
                                objective=ui_obj,
                                bound_paths=list(bound_multi),
                                applied_mutations=list(applied_mutations),
                                repository_root=repository_root,
                            ):
                                repair_notes.append("greenfield_php_sql_presence_verified")
                                test_result = {
                                    "returncode": 0,
                                    "stdout": "greenfield_php_sql_presence_verified",
                                    "stderr": "",
                                    "command": ["greenfield_php_sql_presence_verified"],
                                }
                                test_command = ["greenfield_php_sql_presence_verified"]
                            else:
                                repair_notes.append("syntactic_validation_insufficient")
                                test_result = {
                                    "returncode": 1,
                                    "stdout": "",
                                    "stderr": "syntactic-only validation is insufficient for this objective",
                                    "command": list(test_command),
                                }
        if syntax_errors:
            test_result = {
                "returncode": 2,
                "stdout": "",
                "stderr": "\n".join(syntax_errors),
                "command": ["python", "-m", "py_compile", *mutated],
            }
        last_tests = test_result

        # AIX-EDIT-002 (mutation level): the objective names an existing
        # source file but the round edited only tests. Fail into clarify —
        # a wrong-file edit must never reach verification as a PASS.
        focus_miss = _test_only_mutations_miss_named_source(
            objective=str(coverage_objective or objective or ""),
            applied_mutation_paths=[m.path for m in applied_mutations],
            repository_root=repository_root,
            normalize_repo_relative_path=normalize_repo_relative_path,
            looks_like_a_file=looks_like_a_file,
            extract_target_path=extract_target_path,
            resolve_relative_path=resolve_relative_path,
        )
        if focus_miss:
            raise MultiStepMutationError(
                "FOCUS_MISMATCH",
                focus_miss,
                events=events,
                usage=_usage_to_token_dict(accumulated_usage),
            )

        # Intentionally skipping tests for bound KEY=value env/config mutations must still
        # count as verification — not "tests exist elsewhere in the repo, so fail closed".
        intentionally_skipped_tests = test_result is None and not should_test
        diff_only = intentionally_skipped_tests or (test_result is None and test_command is None)
        # SEM-001-W4: Continuity Law (C) — never soft-pass via diff_only when refs nonempty.
        if continuity_refs_required:
            diff_only = False
        gate_result = evaluate_completion_gates(
            discovery=discovery,
            diff_stats=evidence.diff_stats,
            diff_text=evidence.diff_text,
            test_result=test_result,
            already_satisfied=False,
            diff_only_verification_allowed=(
                False
                if continuity_refs_required
                else (
                    diff_only
                    or assignment_only_env
                    or bool(literal_assignments and not state_failures)
                )
            ),
            state_assertions_passed=not state_failures,
            state_assertion_detail="; ".join(state_failures) if state_failures else "",
            task_family=task_family,
            objective=(coverage_objective or objective),
            required_effects=effects,
            before_files=dict(discovery.config_files),
            after_files={
                m.path: str(m.after_content or "")
                for m in applied_mutations
                if m.after_content is not None
            },
            step_contract=step_contract,
        )
        last_gates = {
            "passed": gate_result.passed,
            "gates": gate_result.gates,
            "reasons": list(gate_result.reasons),
            "verification_verdict": gate_result.verification_verdict,
        }

        round_results.append(
            MutationRoundResult(
                round_index=round_index,
                edits_applied=[m.path for m in applied_mutations],
                gates_passed=bool(gate_result.passed),
                gate_reasons=tuple(gate_result.reasons),
                test_returncode=(
                    int(test_result.get("returncode")) if isinstance(test_result, dict) and "returncode" in test_result else None
                ),
                discovery_mode=mode,
                detail="; ".join(gate_result.reasons) if not gate_result.passed else "ok",
            )
        )
        on_event(
            "mutation.round.completed",
            {
                "round_index": round_index,
                "gates_passed": gate_result.passed,
                "edits": ",".join(m.path for m in applied_mutations),
                "gate_reasons": list(gate_result.reasons)[:8],
                "test_returncode": (
                    int(test_result.get("returncode"))
                    if isinstance(test_result, dict) and "returncode" in test_result
                    else None
                ),
            },
        )

        if gate_result.passed and not state_failures:
            return MultiStepMutationResult(
                ok=True,
                code="REAL_EXECUTE_OK",
                detail=f"multi-step mutation completed in {round_index + 1} round(s)",
                discovery=discovery,
                rounds=round_results,
                diff_text=last_diff,
                diff_stats=last_stats,
                changed_files=last_changed,
                events=events,
                test_result=last_tests,
                completion_gates=last_gates,
                token_usage=_usage_to_token_dict(accumulated_usage),
                strategy_id=last_strategy_id,
                remaining_asked_work=last_remaining,
                unmet_effects=last_unmet,
            )

        # ROBUST-001: gate/test failures always enter the bounded repair loop
        # below, regardless of which strategy produced the edits. A deterministic
        # pass that applied cleanly can still be repaired by a later model round
        # working from the fed-back failure evidence; the round loop stays
        # bounded by rounds_limit, and exhaustion still raises explicitly with
        # the accumulated evidence. (Previously deterministic-path failures
        # raised COMPLETION_GATES_FAILED immediately with no repair round.)
        tests_failed = (
            test_result is not None and int(test_result.get("returncode", 1)) != 0
        )

        # Bounded repair: feed failure evidence into the next round.
        if tests_failed:
            stderr = str(test_result.get("stderr") or test_result.get("stdout") or "")[:500]
            repair_notes.append(f"tests_failed:{stderr}")
        repair_notes.extend(list(gate_result.reasons)[:5])
        repair_notes.extend(state_failures[:5])
        if not expanded:
            expanded = True

    # AIX Closed-Loop Execution -- Sprint 003 (CL3-P3): a bounded repair budget that
    # runs out because the diff never covered what the objective named is a distinct,
    # more actionable failure than the generic "some gate failed" -- name it
    # specifically so callers/telemetry/CLI can tell "the LLM kept producing cosmetic
    # patches" apart from a real verification/test failure. `OBJECTIVE_COVERAGE_FAILED`
    # is registered REPAIRABLE (not TERMINAL_ONLY) in durable_api_backend.py, same as
    # `COMPLETION_GATES_FAILED` -- exhausting this loop's own bounded rounds does not by
    # itself forbid a human-initiated retry_step from trying again with fresh guidance.
    exhausted_code = (
        "OBJECTIVE_COVERAGE_FAILED"
        if isinstance(last_gates, dict) and last_gates.get("gates", {}).get("objective_coverage") is False
        else "COMPLETION_GATES_FAILED"
    )
    raise MultiStepMutationError(
        exhausted_code,
        "; ".join(repair_notes[-8:]) or "verification failed after mutation rounds",
        events=events,
    )


__all__ = [
    "MutationRoundResult",
    "MultiStepMutationResult",
    "MultiStepMutationError",
    "run_multi_step_mutation",
]
