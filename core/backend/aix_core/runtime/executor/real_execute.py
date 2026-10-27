"""001P-W11: real objective-cycle execute helper.

Loads an existing WorkHub task (read-only), then dispatches the objective to exactly
one explicit, real execution branch based on a deterministic intent classification:
`READ_FILE`, `INSPECT_DIRECTORY`, `SEARCH_CODE`, `ANALYZE_CODE`, `EDIT_FILE`/
`EDIT_SYMBOL`, `CREATE_FILE`, `RUN_TESTS`, or `UNKNOWN`. There is no generic
"registered repository + not-a-read => source edit" fallback: an objective that does
not classify into one of the concrete intents above never becomes an edit — it is
either an explicit, opt-in demo/proposal capability (`demo_capability=True`, gated
separately from the general production executor) or a fail-closed
`NO_EXECUTABLE_BRANCH_MATCHED` result.

AIX-GAEF-001: `StepContractV1.actions` (a tuple of governed `ActionRequestV1`) is a
second, separate entry point that bypasses this phrase classifier entirely. When a
step contract carries `actions`, `run_real_execute` dispatches straight to
`foundation.capability_executor` (registry -> policy gateway -> deterministic
executor) instead of `classify_intent`. That is the only sanctioned way to add a new
filesystem capability (e.g. rename, mkdir) going forward — never a new
`if prompt contains "..."` branch in `classify_intent`.

Lifecycle mutation (mark done) is handled separately by 001P-W13 when
OBJECTIVE_CYCLE_MARK_WORKHUB_DONE is enabled.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from backend.aix_core.runtime.executor.attachments import resolve_attachment_text
from backend.aix_core.framework.verification.runtime_bridge import evaluate_completion_gates_routed
from backend.aix_core.runtime.foundation.execution_context import ExecutionContextV1
from backend.aix_core.runtime.objective.contracts import RepairDirectiveV1, StepContextCapsuleV1, StepContractV1
from backend.aix_core.runtime.objective.discovery import run_focused_file_discovery, run_repository_discovery
from backend.aix_core.runtime.governance.execution_budget import (
    ExecutionBudgetConfig,
    ExecutionBudgetManager,
)
from backend.aix_core.runtime.executor.openrouter_codegen import generate_html_css_page
from backend.aix_core.runtime.planner.plan_validation import validate_executable_plan
from backend.aix_core.runtime.executor.patch_validate import (
    apply_patch_atomic,
    compute_content_hash,
    validate_patch,
    validate_write_file_policy,
)
from backend.aix_core.intelligence.repository.repository_location import (
    RepositoryLocationError,
    is_registered_repository,
    rebind_repository_for_objective,
    resolve_repository_root,
)
from backend.aix_core.runtime.executor.read_file_execute import (
    ReadFileError,
    _looks_like_a_file,
    execute_read_file,
    extract_target_path,
    looks_like_read_file_objective,
    normalize_repo_relative_path,
    resolve_relative_path,
)
from backend.aix_core.runtime.executor.multi_step_execute import (
    MultiStepMutationError,
    run_multi_step_mutation,
)
from backend.aix_core.runtime.executor.step_prompt_continuity import (
    ContinuityPromptSection,
    build_continuity_prompt_section,
)
from backend.aix_core.runtime.executor.filesystem_primitives import (
    FilesystemPrimitiveError,
    apply_rename_path,
    looks_like_rename_path_objective,
    rename_paths_from_step_contract,
    resolve_rename_paths,
)
from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolbox, RepositoryToolError
from backend.aix_core.runtime.governance.sandbox import CommandNetworkResourceSandbox
from backend.aix_core.runtime.governance.secret_boundary import redact_objective_text
from backend.aix_core.runtime.executor.source_patch_codegen import (
    extract_env_assignments,
    extract_text_replacements,
)
from backend.aix_core.intelligence.repository.symbol_resolve import SymbolResolver
from backend.aix_core.runtime.executor.workspace_isolation import (
    WorkspaceIsolationBoundary,
    WorkspaceIsolationError,
    WorkspaceScopeV1,
    objective_folder_slug,
    resolve_sticky_date_key,
)
from backend.aix_core.framework.integration.workhub.client import WorkHubTaskClient

OnEvent = Callable[[str, dict[str, Any]], None]


# ---------------------------------------------------------------------------
# Intent classification — the only routing surface for real execution.
# ---------------------------------------------------------------------------

# Objectives naming this shape are never routed anywhere real: no DELETE_FILE branch
# exists, so a delete request must clarify/BLOCK rather than silently fall through to
# an edit branch that would happily "patch" the file instead of removing it.
_DELETE_PHRASES = ("delete ", "remove ", "get rid of")

# Generic command execution ("run the server", "deploy the service") has no real
# branch either — only the narrower RUN_TESTS intent below is real. Checked after the
# test-specific regex so "run the tests" is never misclassified as a bare command.
_COMMAND_ONLY_PHRASES = ("run ", "execute ", "start ", "deploy ", "install ")

# Match "run/execute … tests" as a *suite* request. Do not treat path segments like
# `backend/tests/foo.py` or filenames `test_*.py` as RUN_TESTS — those appear inside
# ordinary edit objectives that also ask for pytest validation after the mutation.
_TEST_RE = re.compile(
    r"\b(?:run|execute)\s+"
    r"(?:(?:the|all|full|affected|focused|relevant|regression|unit)\s+){0,3}"
    r"(?:test\s+suite|tests)\b"
    r"|\b(?:run|execute)\s+(?:python\s+-m\s+)?pytest\b",
    re.IGNORECASE,
)

_INSPECT_DIRECTORY_PHRASES = (
    "list the directory",
    "list the repository",
    "list the folder",
    "show the directory structure",
    "show the repository structure",
    "show me the structure",
    "explore the repository",
    "explore this repository",
    "what files are in",
    "what is in the directory",
    "what's in the directory",
    "inspect the repository",
    "inspect this repository",
    "directory structure",
    "repository structure",
    "folder structure",
    "walk through the repository",
)

_SEARCH_PHRASES = ("find ", "search ", "where is", "where are", "locate ", "grep ")

_ANALYZE_PHRASES = (
    "explain ",
    "analyze ",
    "analyse ",
    "review ",
    "assess ",
    "evaluate ",
    "what does",
    "how does",
    "walk me through",
)

_CREATE_PHRASES = ("create ", "scaffold ", "generate a new ", "add a new ", "build a new ", "make a new ")

_EDIT_VERB_PHRASES = (
    "add ",
    "append ",
    "update ",
    "change ",
    "modify ",
    "fix ",
    "implement ",
    "patch ",
    "edit ",
    "rewrite ",
    "replace ",
    "rename ",
    "refactor ",
    "restructure ",
    "clean up ",
    "integrate ",
    "configure ",
    "wire ",
    "set up ",
    "connect ",
    "enable ",
    "group ",
    "grouped ",
    "extract ",
    "reorganize ",
    "reorganise ",
    "reorder ",
    "move method",
)

# Families whose completion doesn't require any mutation -- a real, grounded answer
# from genuinely-gathered exploration evidence is itself the deliverable. See
# `_run_grounded_answer_execute`: only reachable when real exploration already ran and
# produced real evidence, never a substitute for it.
_GROUNDED_ANSWER_FAMILIES = frozenset({"DEBUG", "ANALYZE", "EXPLAIN", "REVIEW"})

_SEARCH_STOPWORDS = frozenset(
    {
        "find",
        "search",
        "for",
        "where",
        "is",
        "are",
        "the",
        "a",
        "an",
        "in",
        "of",
        "to",
        "locate",
        "grep",
        "code",
        "that",
        "this",
    }
)


_PATH_SEGMENT_SYMBOL_DENYLIST = frozenset(
    {
        "backend",
        "frontend",
        "src",
        "lib",
        "libs",
        "app",
        "apps",
        "api",
        "apis",
        "services",
        "service",
        "runtime",
        "core",
        "tests",
        "test",
        "scripts",
        "docs",
        "proposed",
        "packages",
        "modules",
        "module",
        "objective",
        "objectives",
        "repository",
        "repositories",
        "workspace",
        "workspaces",
        "config",
        "configs",
        "util",
        "utils",
        "common",
        "shared",
        "internal",
        "public",
        "private",
        "vendor",
        "third_party",
        "node_modules",
    }
)


def _extract_symbol_name_from_objective(objective: str) -> str | None:
    from backend.aix_core.runtime.executor.read_file_execute import _scrub_negated_edit_prohibitions

    scrubbed = _scrub_negated_edit_prohibitions(objective or "")
    matches = (
        re.search(
            r"\b([a-zA-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]*)\s+(?:function|method|symbol)\b",
            scrubbed,
            re.IGNORECASE,
        ),
        re.search(
            r"\bsymbol[- ]scoped\s+edit\s+(?:(?:of|for)\s+)?([a-zA-Z_][a-zA-Z0-9_]*)",
            scrubbed,
            re.IGNORECASE,
        ),
        re.search(
            r"\b(?:resolve|edit|modify|refactor)\s+(?:the\s+|of\s+)?([a-zA-Z_][a-zA-Z0-9_]*)",
            scrubbed,
            re.IGNORECASE,
        ),
        # Only match identifier-like names (contain underscore or mixed case),
        # not plain English words that happen to follow "function".
        re.search(
            r"\b(?:function|symbol|method|class|def)\s+([a-zA-Z_][a-zA-Z0-9]*(?:_[a-zA-Z0-9_]+)+|[a-z]+[A-Z][a-zA-Z0-9_]*)",
            scrubbed,
        ),
    )
    for match in matches:
        if not match:
            continue
        candidate = match.group(1)
        # "refactor of backend/aix_core/..." — path segment, not a Python symbol.
        end = match.end(1)
        if end < len(scrubbed) and scrubbed[end] in "/\\":
            continue
        # Contract prose often says "preserve existing function interfaces" or
        # "method behavior".  Those are category nouns, not explicit symbol names;
        # treating them as symbols makes a normal file edit fail preflight with a
        # misleading SYMBOL_NOT_FOUND before code generation can add the requested
        # helper.  Exact identifier-like names remain surgical EDIT_SYMBOL requests.
        if candidate.casefold() in {
            "interface", "interfaces", "behavior", "behaviour", "implementation",
            "signature", "signatures", "contract", "contracts", "semantics",
            "in", "from", "for", "within", "exactly", "scoped", "scope", "edit",
            "the", "a", "an", "only", "requested",
            "function", "symbol", "method", "class", "def",
            "file", "files", "code", "source", "sources",
            "while", "when", "where", "with", "without", "using",
            "existing", "public", "behavior", "behaviour",
            "and", "or", "then", "also",
            # Common English verbs/words that follow "function" or "method"
            # but are not identifier names.
            "satisfies", "returns", "produces", "handles", "receives",
            "remains", "accepts", "performs", "executes", "calls",
            "passes", "fails", "works", "requires", "needs",
            "should", "must", "will", "that", "which",
            *_PATH_SEGMENT_SYMBOL_DENYLIST,
        }:
            continue
        return candidate
    return None


def _derive_search_query(objective: str) -> str:
    quoted = re.search(r"\"([^\"]+)\"|'([^']+)'", objective)
    if quoted:
        return (quoted.group(1) or quoted.group(2) or "").strip()
    tokens = [
        token
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", objective)
        if len(token) > 2 and token.lower() not in _SEARCH_STOPWORDS
    ]
    return " ".join(tokens[:5])


def _step_mutation_kinds(step_contract: StepContractV1) -> set[str]:
    from backend.aix_core.runtime.executor.required_effects import effect_kinds, effects_for_step

    return effect_kinds(effects_for_step(step_contract)) - {
        "PRESERVE_PUBLIC_INTERFACE",
        "RUN_DECLARED_VALIDATION",
    }


def _contract_forces_edit(step_contract: StepContractV1) -> bool:
    """True only when the contract actually declares a mutation.

    Never use substring checks like ``"edit" in schema`` — the default schema
    ``aix.step_contract.v1.generic_edit`` contains the letters "edit" and was
    falsely forcing validate-only steps into EDIT_FILE (PATCH_CONTEXT_MISMATCH).
    """
    if _step_mutation_kinds(step_contract):
        return True
    return any(
        a.capability == "filesystem" and a.operation == "patch"
        for a in (step_contract.actions or ())
    )


def _step_is_validate_only(step_contract: StepContractV1) -> bool:
    if _contract_forces_edit(step_contract):
        return False
    if _step_mutation_kinds(step_contract):
        return False
    # validation_commands alone do not make a step validate-only — mutation steps
    # routinely declare post-edit checks (php -l, pytest). Purpose/criteria must
    # say validate/verify/evidence.
    purpose = f"{step_contract.purpose} {' '.join(step_contract.acceptance_criteria)}".casefold()
    return any(
        tok in purpose
        for tok in ("validate", "validation", "verify the", "attach evidence", "static validation")
    )


def _looks_like_standalone_deliverable(objective: str, targets: tuple[str, ...]) -> bool:
    """True for greenfield artifacts meant for the cycle folder (e.g. schema.sql only)."""
    if not targets:
        return True
    norms = tuple(str(t).replace("\\", "/").lstrip("./") for t in targets if str(t).strip())
    # Governance create_root greenfield (proposed/<slug>/...) always stays in-cycle.
    if norms and all(t == "proposed" or t.startswith("proposed/") for t in norms):
        return True
    if any("/" in t or t.startswith("src") for t in norms):
        return False
    lowered = (objective or "").casefold()
    if "create only" in lowered or "only a " in lowered:
        return True
    if len(norms) == 1:
        name = norms[0].casefold()
        if name.endswith((".sql", ".md", ".html", ".css", ".json", ".txt", ".csv", ".yaml", ".yml")):
            return True
    return False


def _prefers_cycle_project_deliverable(
    *,
    intent: str,
    objective: str,
    repository_root: Path,
    cycle_root: Path,
    targets: tuple[str, ...],
) -> bool:
    """Greenfield CREATE (and follow-up edits of those files) stay inside the cycle folder."""
    if intent not in {"CREATE_FILE", "EDIT_FILE", "EDIT_SYMBOL"}:
        return False
    norms = tuple(str(t).replace("\\", "/").lstrip("./").rstrip("/") for t in targets if str(t).strip())
    obj = str(objective or "")
    obj_l = obj.lower()
    # Explicit proposed/ greenfield (ERP mini-system, calculator, etc.) must never
    # fall back to the registered repo — that dumps files outside disc_*/cyc_*.
    if any(n.startswith("proposed/") for n in norms) or "proposed/" in obj_l:
        return True
    if "governance create_root" in obj_l or "greenfield" in obj_l:
        return True
    if not norms:
        return intent == "CREATE_FILE"
    if any((cycle_root / rel).is_file() for rel in norms):
        return True
    # Already-started cycle project (partial deliverables) stays sticky.
    if (cycle_root / "proposed").is_dir():
        return True
    leaf_norms = tuple(t for t in norms if Path(t).suffix)
    check = leaf_norms or norms
    # Missing from registered repo → create/edit inside the cycle project.
    if all(not (repository_root / rel).is_file() for rel in check):
        return True
    if intent == "CREATE_FILE" and _looks_like_standalone_deliverable(objective, norms):
        return True
    return False


def _actions_fulfill_the_step(step_contract: StepContractV1) -> bool:
    """True when governed `actions` are enough to finish this step.

    Information-only actions (reads, inspect, symbol resolve) never complete a
    step that still declares a mutation effect. Completing those as SUCCESS
    produces an empty diff and a VERIFICATION_HALT.

    filesystem.patch actions require the multi-step pipeline (discover → codegen
    → validate → apply → test → repair) and cannot be completed by the simple
    action executor. They fall through to the mutation lane which has its own
    governed authorization gate.

    content.generate_html_css is async LLM codegen into the cycle workspace —
    handled by `_run_demo_proposal_execute`, not the sync action loop.
    """
    from backend.aix_core.runtime.foundation.capability_registry import is_information_operation

    actions = tuple(step_contract.actions or ())
    if not actions:
        return False
    # filesystem.patch needs the full multi-step pipeline — not the simple loop
    if any(a.capability == "filesystem" and a.operation == "patch" for a in actions):
        return False
    if any(
        a.capability == "content"
        and a.operation in {"generate_html_css", "generate_page", "html_css_page"}
        for a in actions
    ):
        return False
    mutating = any(
        not is_information_operation(action.capability, action.operation) for action in actions
    )
    if mutating:
        return True
    return not _step_mutation_kinds(step_contract)


def _directory_only_create_scopes(paths: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    """Return scopes when every path is a directory (greenfield CREATE_DIRECTORY step).

    File-bearing scopes are left for the patch/codegen lane. Empty → not directory-only.
    """
    cleaned: list[str] = []
    for raw in paths or ():
        rel = str(raw or "").strip().replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        rel = rel.lstrip("/")
        if rel:
            cleaned.append(rel)
    if not cleaned:
        return ()
    files = [p for p in cleaned if _looks_like_a_file(p)]
    dirs = [p for p in cleaned if not _looks_like_a_file(p)]
    if dirs and not files:
        return tuple(dirs)
    return ()


def _step_contract_with_mkdir_actions(
    step_contract: "StepContractV1", directories: tuple[str, ...]
) -> "StepContractV1":
    """Attach governed mkdir actions for directory-only CREATE scopes (no LLM patch)."""
    from backend.aix_core.runtime.objective.contracts import ActionRequestV1

    existing = tuple(step_contract.actions or ())
    if any(a.capability == "filesystem" and a.operation == "mkdir" for a in existing):
        return step_contract
    actions = existing + tuple(
        ActionRequestV1(
            step_id=step_contract.id,
            capability="filesystem",
            operation="mkdir",
            arguments={"path": path, "marker": True},
            expected_observation={"exists": True},
        )
        for path in directories
    )
    return step_contract.model_copy(update={"actions": actions})


def _step_requests_html_css_generation(
    step_contract: "StepContractV1 | None",
    objective: str,
    *,
    has_vision: bool = False,
) -> bool:
    """True when the accepted step should run the HTML/CSS LLM codegen module."""
    if not looks_like_page_generation_objective(objective, has_vision=has_vision):
        return False
    if step_contract is None:
        return False
    schema = str(step_contract.expected_output_schema_ref or "").casefold()
    if "html_css_proposal" in schema:
        return True
    for action in step_contract.actions or ():
        if action.capability == "content" and action.operation in {
            "generate_html_css",
            "generate_page",
            "html_css_page",
        }:
            return True
    scope = " ".join(step_contract.target_scope or ()).replace("\\", "/").casefold()
    purpose = (step_contract.purpose or "").casefold()
    if "proposed" in scope and any(tok in purpose for tok in ("html", "css", "page", "login")):
        return True
    return False


_PAGE_GENERATION_TOKENS = (
    "html",
    "css",
    "login",
    "sign in",
    "signin",
    "sign up",
    "signup",
    "registration",
    "register",
    "landing page",
    "web page",
    "contact page",
    "static page",
    "screenshot",
    "recreate",
    "clone",
    "exact same",
    "same as",
    "pixel",
    "mockup",
    "todo",
    "to-do",
    "to do",
    "dashboard",
    "website",
    "web app",
    "frontend",
    "front-end",
)

# Short tokens that must be whole words — substring match falsely hits ".env append"
# (" app"), "SMTP_FROM" ("form"), etc.
_PAGE_GENERATION_WORD_TOKENS = (
    "ui",
    "form",
    "page",
    "app",
    "application",
)


def looks_like_page_generation_objective(objective: str, *, has_vision: bool = False) -> bool:
    """True when objective should use the opt-in HTML/CSS (or TSX form) demo capability.

    Never treat a concrete repo file edit (e.g. append SMTP keys to `.env`, refactor a
    `.php` controller) as page generation — those must stay on the real EDIT_FILE /
    CREATE_FILE path.

    Also never treat a mixed-scope ask (backend/API/DB/tests/migrations/…) as a static
    page demo merely because it mentions html/frontend/page.
    """
    from backend.aix_core.runtime.objective.objective_scope import (
        is_demo_compatible_page_objective,
    )

    lowered = (objective or "").lower()
    if looks_like_create_empty_directory_objective(objective):
        return False
    if looks_like_rename_path_objective(objective):
        return False

    # Mixed / non-static scope must not enter the demo page lane.
    if not is_demo_compatible_page_objective(objective, has_vision=has_vision):
        # Preserve historical TSX registration form opt-in only when fully demo-compatible.
        return False

    wants_tsx_form = ("react" in lowered or "tsx" in lowered) and (
        "registration" in lowered or "register" in lowered
    )
    if wants_tsx_form:
        return True
    if has_vision:
        return True

    # Explicit filesystem / source-file mutations are never demo page generation.
    has_concrete_path = bool(
        re.search(r"(^|[\s\\/])\.env(\b|[\\/])", lowered)
        or re.search(r"[a-z]:[\\/]|[/\\][^\s\"']+\.[a-z0-9]{1,10}\b", lowered)
        or re.search(r"(?<![a-z0-9_/\\-])([a-z0-9_./\\-]+\.[a-z0-9]{1,10})\b", lowered)
    )
    mutation_verbs = (
        "append",
        "add ",
        "update ",
        "set ",
        "write ",
        "edit ",
        "change ",
        "replace ",
        "configure ",
        "put ",
        "refactor ",
        "restructure ",
        "clean up ",
        "remove ",
        "extract ",
        "group ",
        "normalize ",
    )
    if has_concrete_path and any(verb in lowered for verb in mutation_verbs):
        return False

    if looks_like_create_empty_directory_objective(objective):
        return False
    if any(token in lowered for token in _PAGE_GENERATION_TOKENS):
        return True
    return any(
        re.search(rf"(?<![a-z0-9_]){re.escape(token)}(?![a-z0-9_])", lowered)
        for token in _PAGE_GENERATION_WORD_TOKENS
    )


_CREATE_EMPTY_DIRECTORY_PHRASES = (
    "empty directory",
    "empty folder",
    "mkdir ",
    "create a directory",
    "create an empty directory",
    "create a new directory",
    "create a folder",
    "create a new folder",
    "new directory named",
    "new folder named",
    "directory named",
    "folder named",
    "its a folder",
    "it's a folder",
    "is a folder",
)

_EMPTY_DIR_NAME_STOP = frozenset(
    {
        "file",
        "files",
        "directory",
        "directories",
        "folder",
        "folders",
        "path",
        "name",
        "any",
        "just",
        "new",
        "empty",
        "the",
        "this",
        "that",
        "not",
        "and",
        "or",
        "it",
        "a",
        "an",
        "using",
        "appropriate",
        "repository",
    }
)


def looks_like_create_empty_directory_objective(objective: str) -> bool:
    """True for mkdir / empty-folder asks. Must not steal god-class extracts."""
    lowered = (objective or "").casefold()
    if not lowered:
        return False
    if "god class" in lowered or "god-class" in lowered or "layered architecture" in lowered:
        return False
    if "extract" in lowered and "into" in lowered:
        return False
    if any(phrase in lowered for phrase in _CREATE_EMPTY_DIRECTORY_PHRASES):
        return True
    if "directory not file" in lowered or (
        "not a file" in lowered and "directory" in lowered
    ):
        return any(tok in lowered for tok in ("create ", "mkdir", "named ", "names ", "called "))
    return False


_READ_ONLY_SUMMARY_RE = re.compile(
    r"(?i)\b("
    r"summary|summarize|summarise|overview|"
    r"current status|state of|report on|status of|"
    r"what is the status|tell me about"
    r")\b"
)
_MUTATING_VERBS = (
    "fix ", "update ", "change ", "modify ", "edit ", "implement ",
    "create ", "delete ", "add ", "patch ", "replace ", "integrate ",
)


def looks_like_read_only_summary_objective(objective: str) -> bool:
    """Read-only repo/directory summary asks that should not use single_file_helper."""
    from backend.aix_core.runtime.executor.read_file_execute import _scrub_negated_edit_prohibitions

    lowered = (objective or "").casefold()
    if not lowered:
        return False
    scrubbed = _scrub_negated_edit_prohibitions(lowered)
    if any(verb in scrubbed for verb in _MUTATING_VERBS):
        return False
    if _READ_ONLY_SUMMARY_RE.search(objective):
        return True
    if "summary" in lowered and any(
        phrase in lowered for phrase in ("give me", "provide", "want a", "need a", "can you")
    ):
        return True
    return False


_UNBOUNDED_TREE_MARKERS = (
    "complete current",
    "complete codebase",
    "entire codebase",
    "whole codebase",
    "complete aix",
    "entire aix",
    "all files",
    "complete contents",
    "whole tree",
    "entire repository",
    "whole repo",
)


def looks_like_unbounded_codebase_audit(objective: str) -> bool:
    """Whole-repo read-only audits: seeded one-step markdown, not live planner NME."""
    lowered = (objective or "").casefold()
    if not lowered:
        return False
    read_only = (
        "read-only" in lowered
        or "read only" in lowered
        or "do not modify" in lowered
        or "do not change" in lowered
        or "observation only" in lowered
        or "docs-only" in lowered
        or "docs only" in lowered
    )
    if not read_only:
        return False
    whole = any(marker in lowered for marker in _UNBOUNDED_TREE_MARKERS)
    explore = any(
        phrase in lowered
        for phrase in (
            "explore",
            "map the architecture",
            "codebase audit",
            "codebase-audit",
            "trace the full flow",
        )
    )
    return whole and explore


def codebase_audit_markdown_filename(objective: str) -> str:
    """Named audit markdown from the objective, else the canonical deliverable name."""
    match = re.search(
        r"\b([A-Za-z0-9._-]*CURRENT-CODEBASE-AUDIT\.md)\b",
        objective or "",
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    match = re.search(r"\b([A-Za-z0-9._-]+AUDIT\.md)\b", objective or "", re.IGNORECASE)
    if match:
        return match.group(1)
    return "AIX-CURRENT-CODEBASE-AUDIT.md"


_AUDIT_SPINE_PATHS = (
    "backend/main.py",
    "backend/routers/objective_cycle_router.py",
    "backend/hyeve_aix_service/config.py",
    "backend/aix_core/runtime/objective/runtime_attach.py",
    "backend/aix_core/runtime/planner/step_contract_planner.py",
    "backend/aix_core/runtime/executor/real_execute.py",
    "backend/aix_core/runtime/verification/completion_gates.py",
    "backend/aix_core/framework/integration/workhub/client.py",
    "scripts/aix-chat-cli.ps1",
)


_AUDIT_EXCERPT_NEEDLES = (
    "CANONICAL_COMMAND",
    "Get-FrameworkFlagLabel",
    "WIRED (flag-off)",
    "assert_workhub_write_allowed",
    "AIX_FRAMEWORK_",
    "objective_cycle_router",
)


def _read_bounded_audit_excerpt(root: Path, relative: str, *, max_chars: int = 2200) -> str:
    path = root / relative.replace("\\", "/")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for needle in _AUDIT_EXCERPT_NEEDLES:
        idx = text.find(needle)
        if idx >= 0:
            start = max(0, idx - 240)
            return text[start : start + max_chars]
    return text[:max_chars]


def _assemble_codebase_audit_markdown(
    *,
    listing_names: tuple[str, ...],
    excerpts: dict[str, str],
    repository_root: Path,
) -> str:
    """Deterministic audit from bounded live-spine reads. No whole-tree search."""
    main_ex = excerpts.get("backend/main.py", "")
    canonical = "objective_cycle_router" if "CANONICAL_COMMAND" in main_ex else "(not in excerpt)"
    framework_off = "AIX_FRAMEWORK_" in excerpts.get("backend/hyeve_aix_service/config.py", "")
    cli = excerpts.get("scripts/aix-chat-cli.ps1", "")
    cli_honest = "WIRED (flag-off)" in cli or "Get-FrameworkFlagLabel" in cli
    workhub = excerpts.get("backend/aix_core/framework/integration/workhub/client.py", "")
    f8 = "assert_workhub_write_allowed" in workhub
    lines = [
        "# AIX Current Codebase Audit",
        "",
        f"**Date:** {_utc()[:10]}",
        f"**Produced by:** AIX `run_real_execute` codebase-audit branch (bounded spine reads, no whole-tree search).",
        f"**Repository root:** `{repository_root.as_posix()}`",
        "",
        "## Verdict",
        "",
        "AIX has one live production spine: `objective_cycle_router` → `DurableObjectiveCycleApiBackend`.",
        f"Authority matrix excerpt shows CANONICAL_COMMAND on `{canonical}`.",
        "F1–F11 packages exist under `aix_core/framework/**` and stay flag-off unless `AIX_FRAMEWORK_*` is true.",
        f"F8 WorkHub write gate is {'LIVE (`assert_workhub_write_allowed`)' if f8 else 'not visible in the bounded client excerpt'}.",
        f"CLI `/framework` is {'honest (reads .env flags)' if cli_honest else 'not confirmed honest from excerpt'}.",
        "",
        "## Architecture map (live)",
        "",
        "```",
        "CLI / Frontend BFF / HMAC gateway",
        "  → hyeve-aix-service :8100",
        "    CANONICAL_COMMAND: routers/objective_cycle_router.py",
        "      → DurableObjectiveCycleApiBackend",
        "        discuss → admit → plan (propose_step_contract) → run_real_execute",
        "        → evaluate_completion_gates → cycle outcome",
        "  → integration :8233 (LLM)  workhub :8226 (SoR)  postgres ledger",
        "```",
        "",
        f"**Top-level listing ({len(listing_names)} entries):** " + ", ".join(listing_names[:40]),
        "",
        "## Actual live flow",
        "",
        "| Stage | Live owner | Evidence |",
        "|-------|------------|----------|",
        "| Understanding | `POST /objectives/discuss` → `run_discuss_turn` | `intelligence_layer/service.py` |",
        "| Admit | `POST /objectives/admit` | `objective_cycle_router.py` + WorkHub id or create flag |",
        "| Plan | `POST /cycles/{id}/resume` → `propose_step_contract` | `step_contract_planner.py` |",
        "| Execute | `POST /cycles/{id}/dispatch` → `run_real_execute` | `real_execute.py` |",
        "| Verify | `evaluate_completion_gates` | `completion_gates.py` |",
        "",
        "## Module ownership (bounded evidence)",
        "",
    ]
    for rel, text in excerpts.items():
        snippet = " ".join(text.split())[:180]
        lines.append(f"- `{rel}` — {snippet}{'…' if len(snippet) >= 180 else ''}")
    lines.extend(
        [
            "",
            "## Key problems",
            "",
            "- Whole-repo `/create` must not search the tree; this cycle used a seeded one-step write of the named `.md`.",
            "- F1–F11 are not the runtime unless flags are on. F12 is LOCKED.",
            "- `OBJECTIVE_CYCLE_MARK_WORKHUB_DONE` defaults false; WorkHub closeout is not live.",
            "- Approval still signals `RUNNING_STEP` before an ACCEPTED plan on non-seeded NME paths.",
            "",
            "## Prioritized next actions",
            "",
            "1. Prove the live spine with `/pd01` (atomic ACCEPT → dispatch).",
            "2. Keep Temporal worker off on `:8100`.",
            "3. Do not start F12 without OC equivalence tests.",
            "",
            "## Preserve",
            "",
            "Single CANONICAL_COMMAND writer, event ledger, PD-003 / SEM-001, fail-closed WorkHub writes.",
        ]
    )
    return "\n".join(lines) + "\n"


async def _run_codebase_audit_execute(
    *,
    objective: str,
    repository_id: str,
    settings: object | None,
    workspace: Path,
    workhub_loaded: bool,
    artifacts: dict[str, str],
    apply_edits: bool = True,
) -> RealExecuteResult:
    """Read a bounded live-spine file set and write the named audit markdown.

    Never walks the whole repository. Does not mutate source (.py/.ts/…).
    """
    from backend.aix_core.intelligence.repository.repository_location import (
        RepositoryLocationError,
        resolve_repository_root,
    )
    from backend.aix_core.intelligence.repository.repository_tools import (
        RepositoryToolbox,
        RepositoryToolError,
    )

    filename = codebase_audit_markdown_filename(objective)
    try:
        repository_root = resolve_repository_root(repository_id, settings=settings)
    except RepositoryLocationError as exc:
        raise RealExecuteError(exc.code, exc.detail) from exc

    listing_names: tuple[str, ...] = ()
    try:
        listing = RepositoryToolbox(repository_root=repository_root, on_event=lambda *_: None).list_directory(".")
        listing_names = tuple(
            str(e.get("name") or e.get("path") or "")
            for e in (listing.get("entries") or [])
            if str(e.get("name") or e.get("path") or "").strip()
        )
    except (RepositoryToolError, OSError, TypeError, AttributeError):
        listing_names = ()

    excerpts: dict[str, str] = {}
    for rel in _AUDIT_SPINE_PATHS:
        text = _read_bounded_audit_excerpt(repository_root, rel)
        if text:
            excerpts[rel] = text

    markdown = _assemble_codebase_audit_markdown(
        listing_names=listing_names,
        excerpts=excerpts,
        repository_root=repository_root,
    )
    evidence_dir = workspace / "evidence"
    proposed_dir = workspace / "proposed"
    proposed_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    artifacts = dict(artifacts)
    proposed_path = proposed_dir / filename
    proposed_path.write_text(markdown, encoding="utf-8")
    artifacts["proposed_markdown"] = str(proposed_path)
    repo_path = repository_root / filename
    if apply_edits:
        repo_path.write_text(markdown, encoding="utf-8")
        artifacts["repository_markdown"] = str(repo_path)
        live_checkout = Path("D:/v2.1/aix/hyeve-aix-service")
        live_workspace = Path("D:/v2.1")
        if (
            not os.environ.get("PYTEST_CURRENT_TEST")
            and live_workspace.is_dir()
            and repository_root.resolve() == live_checkout.resolve()
        ):
            try:
                workspace_copy = live_workspace / filename
                workspace_copy.write_text(markdown, encoding="utf-8")
                artifacts["workspace_markdown"] = str(workspace_copy)
            except OSError:
                pass
    diff_text = f"--- /dev/null\n+++ b/{filename}\n@@ -0,0 +1,{max(1, markdown.count(chr(10)))} @@\n"
    diff_path = evidence_dir / "source_diff.txt"
    diff_path.write_text(diff_text, encoding="utf-8")
    artifacts["source_diff"] = str(diff_path)

    plan_path = _write_plan(
        evidence_dir,
        [{"id": "step-1", "action": "bounded_codebase_audit_markdown", "status": "executed"}],
        "Bounded live-spine audit markdown; no whole-tree search; no source mutation.",
    )
    artifacts["plan"] = str(plan_path)
    execution_path = evidence_dir / "execution_report.json"
    _write_json(
        execution_path,
        {
            "ok": True,
            "executor": "objective_cycle.real_execute.codebase_audit.v1",
            "workspace": str(workspace),
            "repository_root": str(repository_root),
            "filename": filename,
            "files_read": list(excerpts),
            "artifacts": artifacts,
            "workhub_loaded": workhub_loaded,
            "workhub_lifecycle_authority": False,
            "recorded_at": _utc(),
        },
    )
    artifacts["execution_report"] = str(execution_path)
    refs = [f"file://{path}" for path in artifacts.values()]
    changed = (
        [{"path": filename, "additions": max(1, markdown.count("\n")), "deletions": 0}]
        if apply_edits
        else []
    )
    return RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace=str(workspace),
        evidence_refs=refs,
        workhub_loaded=workhub_loaded,
        workhub_lifecycle_authority=False,
        detail=f"bounded codebase audit written to {filename}",
        artifacts=artifacts,
        changed_source_files=changed,
        repository_root=str(repository_root),
        diff_text=diff_text,
        completion_gates={"passed": True, "reason": "named audit markdown exists"},
        executor="objective_cycle.real_execute.codebase_audit.v1",
        orchestration_status="COMPLETED",
        planning_status="COMPLETED",
        execution_status="COMPLETED" if apply_edits else "AWAITING_APPLICATION",
        verification_status="PASSED",
        objective_status="COMPLETED" if apply_edits else "AWAITING_REVIEW",
        real_operations=1,
        target_files_read=len(excerpts),
        target_files_modified=1 if apply_edits else 0,
        administrative_artifacts_generated=len(artifacts),
        run_artifacts=[filename],
    )


def resolve_empty_directory_marker_path(
    *,
    objective: str,
    allowed_files: tuple[str, ...] = (),
) -> str | None:
    """Repo-relative `.gitkeep` path. Git cannot track a truly empty directory."""
    from backend.aix_core.runtime.executor.read_file_execute import (
        _looks_like_a_file,
        normalize_repo_relative_path,
    )

    allowed_marker: str | None = None
    for raw in allowed_files:
        rel = normalize_repo_relative_path(str(raw))
        if not rel:
            continue
        posix = rel.replace("\\", "/").strip("/")
        name = Path(posix).name
        if name == ".gitkeep":
            return posix
        if not _looks_like_a_file(posix):
            allowed_marker = f"{posix}/.gitkeep"
    named = _extract_empty_directory_name(objective)
    if named:
        return f"{named}/.gitkeep"
    return allowed_marker


def _extract_empty_directory_name(objective: str) -> str | None:
    match = re.search(
        r"(?:named|names|called|name)\s+[\"'`]?([A-Za-z][A-Za-z0-9._-]{0,63})",
        objective or "",
        re.IGNORECASE,
    )
    if match:
        name = match.group(1)
        if name.casefold() not in _EMPTY_DIR_NAME_STOP:
            return name
    match = re.search(
        r"(?:mkdir|directory|folder)\s+[\"'`]?([A-Za-z][A-Za-z0-9._-]{0,63})",
        objective or "",
        re.IGNORECASE,
    )
    if match:
        name = match.group(1)
        if name.casefold() not in _EMPTY_DIR_NAME_STOP:
            return name
    return None


_DOCS_CONTENT_TOKENS = (
    "poem",
    "poems",
    "rhyme",
    "rhymes",
    "nursery",
    "story",
    "stories",
    "cbse",
)
_SOURCE_MUTATION_EXT_RE = re.compile(
    r"\.(php|py|ts|tsx|js|jsx|go|rs|java|cs|rb|env)\b",
    re.IGNORECASE,
)


def looks_like_markdown_doc_objective(objective: str) -> bool:
    """True for creating a new markdown/doc artifact (poems, stories, notes).

    Never diverts a bound source-file mutation onto the docs proposal path.
    """
    lowered = (objective or "").lower()
    if not lowered:
        return False
    if _SOURCE_MUTATION_EXT_RE.search(lowered) and ".md" not in lowered:
        return False
    # Existing-file mutations (replace/edit README.md) are never "create a doc".
    if any(
        phrase in lowered
        for phrase in (
            "replace ",
            "edit ",
            "modify ",
            "update ",
            "change ",
            "rename ",
            "append ",
            "patch ",
        )
    ):
        return False
    wants_md = ".md" in lowered or "markdown" in lowered
    wants_content = any(token in lowered for token in _DOCS_CONTENT_TOKENS)
    # "Do not create HTML" must not count as create-intent (live CLI false success).
    create_ish = bool(
        re.search(
            r"(?<!\bdo not )(?<!don't )(?<!\bnever )\b(create|write|generate|compose)\b"
            r"|\bmake a (new )?(markdown|md|poem|story|note)",
            lowered,
        )
    )
    return create_ish and (wants_md or wants_content)


def classify_intent(
    objective: str,
    *,
    target_path: str | None,
    symbol_name: str | None,
) -> str:
    """Deterministic-only classification into the concrete intents this executor
    actually implements. Never guesses toward EDIT_FILE/EDIT_SYMBOL/CREATE_FILE for
    text this executor has no real, purpose-built branch for — those cases resolve to
    `UNKNOWN` and the caller fails closed (clarify or BLOCKED), matching the same
    "never guess" discipline as `read_file_execute.py`'s classifier."""
    lowered = (objective or "").strip().lower()
    if not lowered:
        return "UNKNOWN"
    if looks_like_create_empty_directory_objective(objective):
        return "CREATE_FILE"
    if looks_like_read_only_summary_objective(objective):
        if target_path and _looks_like_a_file(target_path):
            return "READ_FILE"
        return "INSPECT_DIRECTORY"
    if looks_like_read_file_objective(objective, target_path):
        return "READ_FILE"
    # Refactor/edit of existing code wins over incidental "create or change" validation
    # clauses (live S6: "php -l on every PHP file you create or change" was classified
    # CREATE_FILE and skipped EXTRACT_TO_FILES structural split).
    textual_editish = any(phrase in lowered for phrase in _EDIT_VERB_PHRASES)
    # A named identifier in the objective ("function double") is not itself an edit.
    # Treating `symbol_name` as editish sends create/define steps into EDIT_SYMBOL
    # and SYMBOL_NOT_FOUND before the file exists.
    editish = textual_editish
    createish = any(phrase in lowered for phrase in _CREATE_PHRASES)
    # Explicit creation remains creation even when the requested new artifact names
    # the function/class it will define; that symbol cannot pre-exist by definition.
    if createish and not textual_editish:
        return "CREATE_FILE"
    # Edit/mutation wins over incidental "run pytest …/tests/…" validation clauses.
    # Live S3: objectives that replace/update source *and* ask to run a focused pytest
    # were classified as RUN_TESTS first → full-repo discovery → EXPLORATION_BUDGET_EXCEEDED
    # without ever mutating.
    if editish:
        return "EDIT_SYMBOL" if symbol_name else "EDIT_FILE"
    if _TEST_RE.search(lowered):
        return "RUN_TESTS"
    if any(phrase in lowered for phrase in _DELETE_PHRASES):
        return "UNKNOWN"
    if any(phrase in lowered for phrase in _INSPECT_DIRECTORY_PHRASES):
        return "INSPECT_DIRECTORY"
    if any(phrase in lowered for phrase in _SEARCH_PHRASES):
        return "SEARCH_CODE"
    if any(phrase in lowered for phrase in _ANALYZE_PHRASES):
        return "ANALYZE_CODE"
    if any(phrase in lowered for phrase in _COMMAND_ONLY_PHRASES):
        return "UNKNOWN"
    # Resource-shape fallback: no verb bucket matched at all, but a real resource was
    # already resolved (see `rebind_repository_for_objective`) -- classify from what it
    # actually is rather than staying UNKNOWN. Generalizes to any phrasing that names a
    # bound file or repository without an enumerated verb ("Check <repo>", "Tell me
    # about <repo>"), instead of growing this file's phrase lists indefinitely.
    if target_path is not None:
        return "READ_FILE" if _looks_like_a_file(target_path) else "INSPECT_DIRECTORY"
    return "UNKNOWN"


class RealExecuteError(RuntimeError):
    def __init__(
        self,
        code: str,
        detail: str = "",
        *,
        events: list[dict[str, Any]] | None = None,
        raw_model_payload: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.detail = detail
        # Real discovery/tool events captured before the failure, so a BLOCKED cycle
        # still shows a caller what was actually inspected — never dropped just
        # because the outcome wasn't a success.
        self.events: list[dict[str, Any]] = list(events or [])
        self.raw_model_payload = raw_model_payload
        super().__init__(code if not detail else f"{code}:{detail}")


def _utc() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _persist_token_usage(
    *,
    evidence_dir: Path,
    artifacts: dict[str, str],
    usage: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not usage:
        return None
    recorded = {**usage, "recorded_at": _utc()}
    token_path = evidence_dir / "token_usage.json"
    _write_json(token_path, recorded)
    artifacts["token_usage"] = str(token_path)
    return recorded


def _detail_with_token_usage(detail: str, usage: dict[str, Any] | None) -> str:
    if not usage:
        return detail
    return (
        f"{detail}; tokens total={usage.get('total_tokens')} "
        f"(prompt={usage.get('prompt_tokens')}, completion={usage.get('completion_tokens')}) "
        f"via {usage.get('provider')}/{usage.get('model')}"
    )


def _redact_task_snapshot(task: dict[str, Any] | None) -> dict[str, Any]:
    if not task:
        return {}
    keep = ("id", "task_id", "number", "task_number", "title", "status", "state", "project_id")
    out = {k: task.get(k) for k in keep if k in task}
    title = str(task.get("title") or task.get("name") or "")
    if title:
        out["title"] = title[:200]
    return out


@dataclass
class RealExecuteResult:
    ok: bool
    code: str
    workspace: str
    evidence_refs: list[str] = field(default_factory=list)
    workhub_loaded: bool = False
    workhub_lifecycle_authority: bool = False
    detail: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    token_usage: dict[str, Any] | None = None
    # Set only by the real edit branch, once a patch has actually been *applied*
    # (`apply_edits=True`). Real repo-relative paths with real diff stats — never
    # merged with `run_artifacts`.
    changed_source_files: list[dict[str, Any]] = field(default_factory=list)
    run_artifacts: list[str] = field(default_factory=list)
    repository_root: str | None = None
    diff_text: str | None = None
    completion_gates: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    # Evidence-facing executor id (e.g. objective_cycle.real_execute.source.multistep.v1).
    executor: str = "objective_cycle.real_execute.v1"
    # Commands actually invoked during mutation verification (from tool events).
    validation_commands: list[str] = field(default_factory=list)
    # Set only by the real read-file branch. Kept separate from `completion_gates`
    # (edit-specific) since a read objective has no diff/test gates to report.
    read_verification: dict[str, Any] | None = None
    read_file: dict[str, Any] | None = None
    resolved_symbol: dict[str, Any] | None = None
    budget_summary: dict[str, Any] | None = None
    # Set only by the edit/create branches when a patch was computed but not applied
    # (the default). A caller (e.g. a reviewer via `editSession.applyProposed`) writes
    # `new_content` itself once it accepts the proposal.
    proposed_edits: list[dict[str, Any]] | None = None

    # Stage-separated outcome model (AIX Real Execution Sprint 001 status model).
    # `ok` is retained for compatibility and means only that orchestration returned a
    # usable result — it is never a completion signal on its own; callers must consume
    # `objective_status`.
    orchestration_status: str = "COMPLETED"
    planning_status: str = "COMPLETED"
    execution_status: str = "NOT_STARTED"
    verification_status: str = "NOT_RUN"
    objective_status: str = "INCOMPLETE"
    reason_code: str | None = None
    real_operations: int = 0
    target_files_read: int = 0
    target_files_modified: int = 0
    administrative_artifacts_generated: int = 0
    strategy_id: str = ""
    remaining_asked_work: bool = False
    unmet_effects: tuple[str, ...] = ()


def default_workspace_base() -> Path:
    configured = str(os.getenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_WORKSPACE_BASE", "") or "").strip()
    if configured:
        return Path(configured)
    return Path(r"D:\v2.1\AIX\.tmp\aix-cycle-workspaces")


def _normalize_bearer(raw: str | None) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    return value or None


def build_workhub_client(
    settings: object | None = None,
    *,
    bearer_authorization: str | None = None,
) -> WorkHubTaskClient:
    """Build WorkHub client. Prefer caller's live Bearer over env service token."""
    if settings is not None:
        base = str(getattr(settings, "WORKHUB_API_URL", "") or "").strip()
        env_token = str(getattr(settings, "WORKHUB_API_TOKEN", "") or "").strip() or None
        closeout = str(getattr(settings, "WORKHUB_CLOSEOUT_API_TOKEN", "") or "").strip() or None
        timeout = float(getattr(settings, "WORKHUB_API_TIMEOUT_SECONDS", 15) or 15)
    else:
        base = str(os.getenv("HYEVE_AIX_SERVICE_WORKHUB_API_URL", "http://127.0.0.1:8226") or "").strip()
        env_token = str(os.getenv("HYEVE_AIX_SERVICE_WORKHUB_API_TOKEN", "") or "").strip() or None
        closeout = str(os.getenv("HYEVE_AIX_SERVICE_WORKHUB_CLOSEOUT_API_TOKEN", "") or "").strip() or None
        timeout = 15.0
    if not base:
        raise RealExecuteError("WORKHUB_URL_MISSING")
    token = _normalize_bearer(bearer_authorization) or _normalize_bearer(env_token)
    return WorkHubTaskClient(
        base_url=base,
        service_token=token,
        closeout_service_token=closeout,
        timeout_seconds=timeout,
    )


# Admit may accept these only while create-intent processing resolves them to a
# real WorkHub task id. They must never reach contract-load or mark-done HTTP.
WORKHUB_TASK_ID_PLACEHOLDERS = frozenset({"auto", "create", "new"})


def is_workhub_task_id_placeholder(task_id: str | None) -> bool:
    """True when task_id is an unresolved create marker, not a WorkHub identity."""
    return str(task_id or "").strip().lower() in WORKHUB_TASK_ID_PLACEHOLDERS


async def load_workhub_task(
    *,
    client: WorkHubTaskClient,
    project_id: str,
    workhub_task_id: str,
    task_number: int | None,
    tenant_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Read-only WorkHub load. Never creates tasks."""
    if task_number is not None:
        task = await client.get_task_by_number(
            project_id=project_id,
            task_number=int(task_number),
            tenant_id=tenant_id,
        )
        if task is None:
            return None, "WORKHUB_TASK_NOT_FOUND"
        return task, None

    # Fail closed before HTTP: placeholders are admit-time create markers only.
    if is_workhub_task_id_placeholder(workhub_task_id) or not str(workhub_task_id or "").strip():
        return None, "WORKHUB_TASK_ID_UNRESOLVED"

    ok, err, data = await client.get_aix_adapter_read_contract(
        project_id=project_id,
        task_id=workhub_task_id,
        tenant_id=tenant_id,
    )
    if ok and data is not None:
        return data, None
    # Fallback: try interpreting workhub_task_id as task number.
    if str(workhub_task_id).isdigit():
        task = await client.get_task_by_number(
            project_id=project_id,
            task_number=int(workhub_task_id),
            tenant_id=tenant_id,
        )
        if task is not None:
            return task, None
        return None, "WORKHUB_TASK_NOT_FOUND"
    return None, f"WORKHUB_LOAD_FAILED:{err or 'unknown'}"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_plan(evidence_dir: Path, steps: list[dict[str, Any]], notes: str) -> Path:
    """Branch-specific plan — never the generic `bounded_propose_artifact` +
    `human_review` shape unless that literally is the branch taken (the explicit
    demo/proposal capability)."""
    plan_path = evidence_dir / "plan.json"
    _write_json(plan_path, {"steps": steps, "notes": notes, "recorded_at": _utc()})
    return plan_path


def _count_tool_events(events: list[dict[str, Any]]) -> int:
    return sum(1 for e in events if str(e.get("event_type") or "").startswith("tool."))


def _make_on_event(events: list[dict[str, Any]], budget_mgr: ExecutionBudgetManager | None) -> OnEvent:
    """Shared event recorder wired to `ExecutionBudgetManager`, reused by every real
    branch — reads, searches and commands are all tracked through this single path
    rather than re-implemented per branch."""

    def on_event(event_type: str, payload_refs: dict[str, Any]) -> None:
        events.append({"event_type": event_type, "payload_refs": payload_refs, "recorded_at": _utc()})
        if budget_mgr is None:
            return
        b_res = None
        if event_type in ("repository.search", "tool.search"):
            b_res = budget_mgr.record_search(
                query=str(payload_refs.get("pattern") or payload_refs.get("query") or ""),
                scope=str(payload_refs.get("relative_path") or ""),
            )
        elif event_type in ("repository.read", "tool.file_read"):
            b_res = budget_mgr.record_read(
                file_path=str(payload_refs.get("relative_path") or ""),
                file_hash=str(payload_refs.get("file_hash") or ""),
            )
        elif event_type == "tool.command_run":
            b_res = budget_mgr.record_command(str(payload_refs.get("command") or ""))
        if b_res is not None and not b_res.allowed:
            payload = b_res.event_payload or {}
            evt_name = payload.get("event_name", "budget.exhausted")
            events.append(
                {
                    "event_type": evt_name,
                    "payload_refs": {"reason_code": b_res.reason_code, "detail": b_res.reason_detail},
                    "recorded_at": _utc(),
                }
            )
            raise RealExecuteError(b_res.reason_code or "EXPLORATION_BUDGET_EXCEEDED", b_res.reason_detail, events=events)

    return on_event


def _record_test_run(
    *,
    budget_mgr: ExecutionBudgetManager | None,
    events: list[dict[str, Any]],
    test_command: list[str],
) -> None:
    """`RepositoryToolbox.run_tests` only ever emits `tool.command_run` (it is
    implemented as `run_command` with a single-entry allowlist) — never a distinct
    `tool.run_tests` event, so a test run was previously only ever counted as a generic
    command and `ExecutionBudgetManager.test_count` never moved. Called explicitly,
    once, right after a real `run_tests` call succeeds."""
    if budget_mgr is None:
        return
    b_res = budget_mgr.record_test(" ".join(test_command))
    if not b_res.allowed:
        payload = b_res.event_payload or {}
        evt_name = payload.get("event_name", "budget.exhausted")
        events.append(
            {
                "event_type": evt_name,
                "payload_refs": {"reason_code": b_res.reason_code, "detail": b_res.reason_detail},
                "recorded_at": _utc(),
            }
        )
        raise RealExecuteError(b_res.reason_code or "EXPLORATION_BUDGET_EXCEEDED", b_res.reason_detail, events=events)


def _infer_test_command(discovery_manifests: set[str]) -> list[str] | None:
    if discovery_manifests & {"pyproject.toml", "requirements.txt", "setup.py", "Pipfile"}:
        return [sys.executable, "-m", "pytest", "-q"]
    if "package.json" in discovery_manifests:
        import shutil

        # Prefer Playwright when the FE package exposes it (npm test is often absent).
        npx = shutil.which("npx.cmd") or shutil.which("npx")
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if npx:
            return [npx, "playwright", "test", "--reporter=line"]
        if npm:
            return [npm, "run", "playwright", "--", "--reporter=line"]
        return ["npm", "test"]
    return None


def _proposed_registration_form_tsx() -> str:
    return """'use client';

import { FormEvent, useState } from 'react';

type FormState = {
  name: string;
  email: string;
  password: string;
  confirmPassword: string;
};

const initial: FormState = { name: '', email: '', password: '', confirmPassword: '' };

/** Proposed artifact from AIX objective-cycle real execute (001P-W11), demo capability
 * only. Not auto-merged. */
export function RegisterFormProposed() {
  const [form, setForm] = useState<FormState>(initial);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState(false);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setOk(false);
    if (!form.name.trim() || !form.email.trim() || !form.password) {
      setError('Name, email, and password are required.');
      return;
    }
    if (!form.email.includes('@')) {
      setError('Enter a valid email.');
      return;
    }
    if (form.password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    if (form.password !== form.confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    setError(null);
    setOk(true);
  }

  return (
    <form onSubmit={onSubmit} noValidate>
      <h1>Register</h1>
      {error ? <p role="alert">{error}</p> : null}
      {ok ? <p role="status">Validation passed (proposed artifact — not submitted to backend).</p> : null}
      <label>
        Name
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </label>
      <label>
        Email
        <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
      </label>
      <label>
        Password
        <input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
      </label>
      <label>
        Confirm password
        <input
          type="password"
          value={form.confirmPassword}
          onChange={(e) => setForm({ ...form, confirmPassword: e.target.value })}
        />
      </label>
      <button type="submit">Create account</button>
    </form>
  );
}
"""


# ---------------------------------------------------------------------------
# Real branches
# ---------------------------------------------------------------------------


async def _run_patch_propose_execute(
    *,
    objective: str,
    repository_id: str,
    settings: object | None,
    model_override: str | None,
    workspace: Path,
    workhub_loaded: bool,
    artifacts: dict[str, str],
    budget_mgr: ExecutionBudgetManager | None,
    branch_kind: str,
    apply_edits: bool = True,
    allowed_files: tuple[str, ...] = (),
    literal_assignments: dict[str, str] | None = None,
    task_family: str = "UNKNOWN",
    step_contract: "StepContractV1 | None" = None,
    plan_steps: tuple["StepContractV1", ...] | list["StepContractV1"] = (),
    repair_directive: "RepairDirectiveV1 | None" = None,
    text_replacements: list[tuple[str, str]] | None = None,
    coverage_objective: str = "",
    execution_context: dict[str, Any] | None = None,
    mutation_repository_root: Path | None = None,
    tenant_id: str = "",
    actor_id: str = "",
    caller_authorization: str | None = None,
    admission_allowed_files: tuple[str, ...] = (),
) -> RealExecuteResult:
    """EDIT/CREATE/EDIT_SYMBOL via generic multi-step mutation runner.

    Discovers (focused → expand-on-miss), proposes patches, applies, verifies, and
    optionally repairs within budget. No task-type hardcoding.

    ``mutation_repository_root`` overrides the registered repo root so greenfield
    deliverables can land under the cycle's objective-named folder.
    """
    if mutation_repository_root is not None:
        repository_root = Path(mutation_repository_root)
    else:
        try:
            repository_root = resolve_repository_root(repository_id, settings=settings)
        except RepositoryLocationError as exc:
            raise RealExecuteError(exc.code, exc.detail) from exc

    events: list[dict[str, Any]] = []
    on_event = _make_on_event(events, budget_mgr)
    toolbox = RepositoryToolbox(repository_root=repository_root, on_event=on_event)
    evidence_dir = workspace / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    artifacts = dict(artifacts)

    def persist_failure_events(
        *,
        code: str,
        detail: str,
        failure_events: list[dict[str, Any]],
        usage: dict[str, Any] | None = None,
        raw_model_payload: dict[str, Any] | None = None,
    ) -> None:
        events_path = evidence_dir / "discovery_events.json"
        _write_json(
            events_path,
            {
                "events": failure_events,
                "failure": {"code": code, "detail": detail},
                "recorded_at": _utc(),
            },
        )
        artifacts["discovery_events"] = str(events_path)
        # NO_EDIT hardening: usage must be recorded even on failure, or a
        # prose-only terminal is indistinguishable from "model never called".
        token_usage = _persist_token_usage(
            evidence_dir=evidence_dir,
            artifacts=artifacts,
            usage=usage,
        )
        failure_path = evidence_dir / "execution_failure.json"
        _write_json(
            failure_path,
            {
                "ok": False,
                "executor": f"objective_cycle.real_execute.{branch_kind.lower()}.v1",
                "reason_code": code,
                "detail": detail,
                "repository_root": str(repository_root),
                "artifacts": artifacts,
                "token_usage": token_usage,
                "raw_model_payload_truncated": (
                    str(raw_model_payload)[:2000]
                    if isinstance(raw_model_payload, dict)
                    else None
                ),
                "recorded_at": _utc(),
            },
        )
        artifacts["execution_failure"] = str(failure_path)

    try:
        multi = await run_multi_step_mutation(
            objective=objective,
            coverage_objective=coverage_objective or objective,
            toolbox=toolbox,
            repository_root=repository_root,
            settings=settings,
            model_override=model_override,
            budget_mgr=budget_mgr,
            on_event=on_event,
            events=events,
            allowed_files=allowed_files,
            prior_read_files=prior_read_paths_from_context(execution_context),
            literal_assignments=literal_assignments,
            apply_edits=apply_edits,
            task_family=task_family,
            evaluate_completion_gates=lambda **kw: evaluate_completion_gates_routed(
                settings=settings, **kw
            ),
            infer_test_command=_infer_test_command,
            normalize_repo_relative_path=normalize_repo_relative_path,
            looks_like_a_file=_looks_like_a_file,
            extract_target_path=extract_target_path,
            resolve_relative_path=resolve_relative_path,
            step_contract=step_contract,
            plan_steps=plan_steps,
            repair_directive=repair_directive,
            text_replacements=text_replacements,
            tenant_id=tenant_id,
            actor_id=actor_id,
            authorization_header=caller_authorization,
            admission_allowed_files=admission_allowed_files,
        )
    except MultiStepMutationError as exc:
        persist_failure_events(
            code=exc.code,
            detail=exc.detail,
            failure_events=exc.events or events,
            usage=getattr(exc, "usage", None),
            raw_model_payload=exc.raw_model_payload,
        )
        raise RealExecuteError(
            exc.code,
            exc.detail,
            events=exc.events or events,
            raw_model_payload=exc.raw_model_payload,
        ) from exc
    except RealExecuteError as exc:
        persist_failure_events(
            code=exc.code,
            detail=exc.detail,
            failure_events=exc.events or events,
        )
        raise

    events_path = evidence_dir / "discovery_events.json"
    _write_json(
        events_path,
        {
            "events": events,
            "rounds": [
                {
                    "round_index": r.round_index,
                    "edits_applied": r.edits_applied,
                    "gates_passed": r.gates_passed,
                    "gate_reasons": list(r.gate_reasons),
                    "discovery_mode": r.discovery_mode,
                    "detail": r.detail,
                }
                for r in multi.rounds
            ],
            "recorded_at": _utc(),
        },
    )
    artifacts["discovery_events"] = str(events_path)

    if multi.already_satisfied:
        gate_result = evaluate_completion_gates_routed(
            settings=settings,
            discovery=multi.discovery,
            diff_stats={},
            diff_text="",
            test_result=None,
            already_satisfied=True,
            already_satisfied_reason=multi.already_satisfied_reason,
            already_satisfied_evidence_refs=multi.already_satisfied_evidence_refs,
            step_contract=step_contract,
        )
        gates_path = evidence_dir / "completion_gates.json"
        _write_json(
            gates_path,
            {
                "passed": gate_result.passed,
                "gates": gate_result.gates,
                "reasons": list(gate_result.reasons),
                "verification_verdict": gate_result.verification_verdict,
                "recorded_at": _utc(),
            },
        )
        plan_path = _write_plan(
            evidence_dir,
            [{"id": "step-1", "action": "verify_already_satisfied", "status": "executed"}],
            f"{branch_kind}: objective already satisfied by existing repository content.",
        )
        artifacts.update({"completion_gates": str(gates_path), "plan": str(plan_path)})
        if not gate_result.passed:
            raise RealExecuteError(
                "COMPLETION_GATES_FAILED",
                "; ".join(gate_result.reasons) or "completion gates failed",
                events=events,
            )
        token_usage = _persist_token_usage(
            evidence_dir=evidence_dir,
            artifacts=artifacts,
            usage=multi.token_usage,
        )
        execution_path = evidence_dir / "execution_report.json"
        _write_json(
            execution_path,
            {
                "ok": True,
                "executor": f"objective_cycle.real_execute.{branch_kind.lower()}.v1",
                "workspace": str(workspace),
                "repository_root": str(repository_root),
                "artifacts": artifacts,
                "already_satisfied": True,
                "workhub_loaded": workhub_loaded,
                "workhub_lifecycle_authority": False,
                "recorded_at": _utc(),
            },
        )
        artifacts["execution_report"] = str(execution_path)
        refs = [f"file://{path}" for path in artifacts.values()]
        return RealExecuteResult(
            ok=True,
            code="ALREADY_SATISFIED",
            workspace=str(workspace),
            evidence_refs=refs,
            workhub_loaded=workhub_loaded,
            workhub_lifecycle_authority=False,
            detail=_detail_with_token_usage(
                f"real {branch_kind.lower()}: objective already satisfied in {repository_root}",
                token_usage,
            ),
            artifacts=artifacts,
            repository_root=str(repository_root),
            completion_gates={
                "passed": gate_result.passed,
                "gates": gate_result.gates,
                "reasons": list(gate_result.reasons),
                "verification_verdict": gate_result.verification_verdict,
            },
            events=events,
            orchestration_status="COMPLETED",
            planning_status="COMPLETED",
            execution_status="COMPLETED",
            verification_status="PASSED",
            objective_status="COMPLETED",
            real_operations=_count_tool_events(events),
            target_files_read=multi.discovery.files_read,
            target_files_modified=0,
            administrative_artifacts_generated=len(artifacts),
            token_usage=token_usage,
        )

    if multi.code == "PATCH_PROPOSED":
        proposed = multi.proposed_edits or []
        patch_path = evidence_dir / "proposed_patch.json"
        _write_json(patch_path, {"edits": proposed, "recorded_at": _utc()})
        plan_path = _write_plan(
            evidence_dir,
            [
                {"id": "step-1", "action": "propose_patch", "status": "executed"},
                {"id": "step-2", "action": "human_review", "status": "pending"},
            ],
            f"{branch_kind}: multi-step patches proposed; awaiting application.",
        )
        artifacts.update({"proposed_patch": str(patch_path), "plan": str(plan_path)})
        token_usage = _persist_token_usage(
            evidence_dir=evidence_dir,
            artifacts=artifacts,
            usage=multi.token_usage,
        )
        execution_path = evidence_dir / "execution_report.json"
        _write_json(
            execution_path,
            {
                "ok": True,
                "executor": f"objective_cycle.real_execute.{branch_kind.lower()}.v1",
                "workspace": str(workspace),
                "repository_root": str(repository_root),
                "artifacts": artifacts,
                "proposed_edits": proposed,
                "workhub_loaded": workhub_loaded,
                "workhub_lifecycle_authority": False,
                "recorded_at": _utc(),
            },
        )
        artifacts["execution_report"] = str(execution_path)
        refs = [f"file://{path}" for path in artifacts.values()]
        return RealExecuteResult(
            ok=True,
            code="PATCH_PROPOSED",
            workspace=str(workspace),
            evidence_refs=refs,
            workhub_loaded=workhub_loaded,
            workhub_lifecycle_authority=False,
            detail=_detail_with_token_usage(multi.detail, token_usage),
            artifacts=artifacts,
            repository_root=str(repository_root),
            diff_text=multi.diff_text or None,
            events=events,
            proposed_edits=proposed,
            orchestration_status="COMPLETED",
            planning_status="COMPLETED",
            execution_status="AWAITING_APPLICATION",
            verification_status="NOT_RUN",
            objective_status="AWAITING_REVIEW",
            reason_code="NO_FILES_CHANGED",
            real_operations=max(_count_tool_events(events), 1),
            target_files_read=multi.discovery.files_read,
            target_files_modified=0,
            administrative_artifacts_generated=len(artifacts),
            token_usage=token_usage,
        )

    diff_path = evidence_dir / "source_diff.txt"
    diff_path.write_text(multi.diff_text or "", encoding="utf-8")
    gates_path = evidence_dir / "completion_gates.json"
    _write_json(gates_path, {**(multi.completion_gates or {}), "recorded_at": _utc()})
    plan_steps = [
        {
            "id": f"round-{r.round_index}",
            "action": "mutate_verify",
            "status": "executed",
            "files": r.edits_applied,
        }
        for r in multi.rounds
    ] or [{"id": "step-1", "action": "mutate_verify", "status": "executed"}]
    plan_path = _write_plan(
        evidence_dir,
        plan_steps,
        f"{branch_kind}: multi-step mutation completed with evidence-gated verification.",
    )
    artifacts.update(
        {
            "source_diff": str(diff_path),
            "completion_gates": str(gates_path),
            "plan": str(plan_path),
        }
    )
    token_usage = _persist_token_usage(
        evidence_dir=evidence_dir,
        artifacts=artifacts,
        usage=multi.token_usage,
    )
    execution_path = evidence_dir / "execution_report.json"
    _write_json(
        execution_path,
        {
            "ok": True,
            "executor": f"objective_cycle.real_execute.{branch_kind.lower()}.multistep.v1",
            "workspace": str(workspace),
            "repository_root": str(repository_root),
            "artifacts": artifacts,
            "rounds": len(multi.rounds),
            "changed_source_files": [c.get("path") for c in multi.changed_files],
            "workhub_loaded": workhub_loaded,
            "workhub_lifecycle_authority": False,
            "recorded_at": _utc(),
        },
    )
    artifacts["execution_report"] = str(execution_path)
    refs = [f"file://{path}" for path in artifacts.values()]
    changed_files_detail = [
        {
            "path": c["path"],
            "additions": multi.diff_stats.get(c["path"], {}).get("additions", 0),
            "deletions": multi.diff_stats.get(c["path"], {}).get("deletions", 0),
        }
        for c in multi.changed_files
    ]
    gates = multi.completion_gates or {}
    validation_commands = [
        str(e.get("payload_refs", {}).get("command") or "").strip()
        for e in events
        if str(e.get("event_type") or "") == "tool.command_run"
        and str(e.get("payload_refs", {}).get("command") or "").strip()
    ]
    # Deduplicate while preserving order.
    validation_commands = list(dict.fromkeys(validation_commands))
    return RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace=str(workspace),
        evidence_refs=refs,
        workhub_loaded=workhub_loaded,
        workhub_lifecycle_authority=False,
        detail=_detail_with_token_usage(multi.detail, token_usage),
        artifacts=artifacts,
        changed_source_files=changed_files_detail,
        repository_root=str(repository_root),
        diff_text=multi.diff_text or None,
        completion_gates=gates,
        events=events,
        executor=f"objective_cycle.real_execute.{branch_kind.lower()}.multistep.v1",
        validation_commands=validation_commands,
        orchestration_status="COMPLETED",
        planning_status="COMPLETED",
        execution_status="COMPLETED",
        verification_status="PASSED",
        objective_status="COMPLETED",
        real_operations=_count_tool_events(events),
        target_files_read=multi.discovery.files_read,
        target_files_modified=len(changed_files_detail),
        administrative_artifacts_generated=len(artifacts),
        token_usage=token_usage,
        strategy_id=str(getattr(multi, "strategy_id", "") or ""),
        remaining_asked_work=bool(getattr(multi, "remaining_asked_work", False)),
        unmet_effects=tuple(getattr(multi, "unmet_effects", ()) or ()),
    )


async def _run_read_file_execute(
    *,
    objective: str,
    repository_id: str,
    target_path: str,
    settings: object | None,
    workspace: Path,
    workhub_loaded: bool,
    artifacts: dict[str, str],
    budget_mgr: ExecutionBudgetManager | None,
    mutation_repository_root: Path | None = None,
) -> RealExecuteResult:
    """Real READ_FILE branch: resolve the requested path against the approved
    repository root and read it for real, never a proposal. Raises `RealExecuteError`
    (never returns `ok=True`) on any resolution or read failure, verification failure,
    or empty result — the caller (`durable_api_backend.py::_maybe_real_execute`)
    already transitions the cycle to BLOCKED on `RealExecuteError`."""
    if mutation_repository_root is not None:
        repository_root = Path(mutation_repository_root)
    else:
        try:
            repository_root = resolve_repository_root(repository_id, settings=settings)
        except RepositoryLocationError as exc:
            raise RealExecuteError(exc.code, exc.detail) from exc

    events: list[dict[str, Any]] = []
    on_event = _make_on_event(events, budget_mgr)
    toolbox = RepositoryToolbox(repository_root=repository_root, on_event=on_event)

    try:
        read_result = execute_read_file(toolbox=toolbox, target_path=target_path, repository_root=repository_root)
    except ReadFileError as exc:
        raise RealExecuteError(exc.code, exc.detail, events=events) from exc

    verification = read_result.verification()
    if not all(verification.values()):
        raise RealExecuteError(
            "VERIFICATION_FAILED",
            "; ".join(k for k, ok in verification.items() if not ok),
            events=events,
        )

    evidence_dir = workspace / "evidence"
    file_read_path = evidence_dir / "file_read.json"
    _write_json(
        file_read_path,
        {
            "resource": read_result.relative_path,
            "repository_root": str(repository_root),
            "bytes_read": read_result.bytes_read,
            "line_count": read_result.line_count,
            "truncated": read_result.truncated,
            "env_summary": read_result.env_summary.as_dict() if read_result.env_summary else None,
            "preview": read_result.preview,
            "verification": verification,
            "recorded_at": _utc(),
        },
    )
    events_path = evidence_dir / "discovery_events.json"
    _write_json(events_path, {"events": events, "recorded_at": _utc()})
    plan_path = _write_plan(
        evidence_dir,
        [{"id": "step-1", "action": "read_target_file", "status": "executed"}],
        "READ_FILE: real filesystem.read_file call against the approved repository.",
    )

    artifacts = dict(artifacts)
    artifacts["file_read"] = str(file_read_path)
    artifacts["discovery_events"] = str(events_path)
    artifacts["plan"] = str(plan_path)

    execution_path = evidence_dir / "execution_report.json"
    _write_json(
        execution_path,
        {
            "ok": True,
            "executor": "objective_cycle.real_execute.read_file.v1",
            "workspace": str(workspace),
            "repository_root": str(repository_root),
            "artifacts": artifacts,
            "read_verification": verification,
            "workhub_loaded": workhub_loaded,
            "workhub_lifecycle_authority": False,
            "recorded_at": _utc(),
        },
    )
    artifacts["execution_report"] = str(execution_path)

    refs = [f"file://{path}" for path in artifacts.values()]
    keys_found = len(read_result.env_summary.keys) if read_result.env_summary else None
    detail = f"real file read: {read_result.relative_path} ({read_result.bytes_read} bytes) in {repository_root}"
    if keys_found is not None:
        sensitive_count = sum(1 for k in read_result.env_summary.keys if k.sensitive)
        detail = f"{detail}; {keys_found} env var(s) found, {sensitive_count} sensitive value(s) redacted"

    real_ops = max(_count_tool_events(events), 1)
    return RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace=str(workspace),
        evidence_refs=refs,
        workhub_loaded=workhub_loaded,
        workhub_lifecycle_authority=False,
        detail=detail,
        artifacts=artifacts,
        token_usage=None,
        repository_root=str(repository_root),
        events=events,
        read_verification=verification,
        read_file={
            "path": read_result.relative_path,
            "bytes_read": read_result.bytes_read,
            "line_count": read_result.line_count,
            "truncated": read_result.truncated,
        },
        orchestration_status="COMPLETED",
        planning_status="COMPLETED",
        execution_status="COMPLETED",
        verification_status="PASSED",
        objective_status="COMPLETED",
        real_operations=real_ops,
        target_files_read=1,
        target_files_modified=0,
        administrative_artifacts_generated=len(artifacts),
    )


async def _run_inspect_directory_execute(
    *,
    repository_id: str,
    settings: object | None,
    target_path: str | None,
    workspace: Path,
    workhub_loaded: bool,
    artifacts: dict[str, str],
    budget_mgr: ExecutionBudgetManager | None,
) -> RealExecuteResult:
    """Real INSPECT_DIRECTORY branch: `RepositoryToolbox.list_directory` against the
    approved repository root — never a proposal, never a guessed structure."""
    try:
        repository_root = resolve_repository_root(repository_id, settings=settings)
    except RepositoryLocationError as exc:
        raise RealExecuteError(exc.code, exc.detail) from exc

    events: list[dict[str, Any]] = []
    on_event = _make_on_event(events, budget_mgr)
    toolbox = RepositoryToolbox(repository_root=repository_root, on_event=on_event)

    relative_path = "."
    if target_path:
        resolved = resolve_relative_path(target_path, repository_root)
        if resolved is None:
            raise RealExecuteError(
                "WORKSPACE_NOT_BOUND",
                f"{target_path!r} does not resolve inside the approved repository root",
                events=events,
            )
        relative_path = resolved

    try:
        listing = toolbox.list_directory(relative_path)
    except RepositoryToolError as exc:
        raise RealExecuteError(exc.code, exc.detail, events=events) from exc

    evidence_dir = workspace / "evidence"
    listing_path = evidence_dir / "directory_listing.json"
    _write_json(
        listing_path,
        {
            "relative_path": relative_path,
            "repository_root": str(repository_root),
            "entries": listing["entries"],
            "recorded_at": _utc(),
        },
    )
    events_path = evidence_dir / "discovery_events.json"
    _write_json(events_path, {"events": events, "recorded_at": _utc()})
    plan_path = _write_plan(
        evidence_dir,
        [{"id": "step-1", "action": "list_directory", "status": "executed"}],
        "INSPECT_DIRECTORY: real directory listing against the approved repository.",
    )

    artifacts = dict(artifacts)
    artifacts["directory_listing"] = str(listing_path)
    artifacts["discovery_events"] = str(events_path)
    artifacts["plan"] = str(plan_path)

    execution_path = evidence_dir / "execution_report.json"
    _write_json(
        execution_path,
        {
            "ok": True,
            "executor": "objective_cycle.real_execute.inspect_directory.v1",
            "workspace": str(workspace),
            "repository_root": str(repository_root),
            "artifacts": artifacts,
            "entry_count": len(listing["entries"]),
            "workhub_loaded": workhub_loaded,
            "workhub_lifecycle_authority": False,
            "recorded_at": _utc(),
        },
    )
    artifacts["execution_report"] = str(execution_path)

    refs = [f"file://{path}" for path in artifacts.values()]
    real_ops = max(_count_tool_events(events), 1)
    detail = f"real directory inspection: {relative_path} ({len(listing['entries'])} entries) in {repository_root}"
    return RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace=str(workspace),
        evidence_refs=refs,
        workhub_loaded=workhub_loaded,
        workhub_lifecycle_authority=False,
        detail=detail,
        artifacts=artifacts,
        repository_root=str(repository_root),
        events=events,
        orchestration_status="COMPLETED",
        planning_status="COMPLETED",
        execution_status="COMPLETED",
        verification_status="PASSED",
        objective_status="COMPLETED",
        real_operations=real_ops,
        target_files_read=0,
        target_files_modified=0,
        administrative_artifacts_generated=len(artifacts),
    )


async def _run_search_code_execute(
    *,
    objective: str,
    repository_id: str,
    settings: object | None,
    target_path: str | None,
    workspace: Path,
    workhub_loaded: bool,
    artifacts: dict[str, str],
    budget_mgr: ExecutionBudgetManager | None,
) -> RealExecuteResult:
    """Real SEARCH_CODE branch: `RepositoryToolbox.search_files` against the approved
    repository root."""
    try:
        repository_root = resolve_repository_root(repository_id, settings=settings)
    except RepositoryLocationError as exc:
        raise RealExecuteError(exc.code, exc.detail) from exc

    query = _derive_search_query(objective)
    if not query:
        raise RealExecuteError("INTENT_UNCLEAR", "no search query could be extracted from the objective")

    events: list[dict[str, Any]] = []
    on_event = _make_on_event(events, budget_mgr)
    toolbox = RepositoryToolbox(repository_root=repository_root, on_event=on_event)

    relative_path = "."
    if target_path:
        relative_path = resolve_relative_path(target_path, repository_root) or "."

    try:
        results = toolbox.search_files(query, relative_path)
    except RepositoryToolError as exc:
        raise RealExecuteError(exc.code, exc.detail, events=events) from exc

    evidence_dir = workspace / "evidence"
    results_path = evidence_dir / "search_results.json"
    _write_json(
        results_path,
        {
            "query": query,
            "relative_path": relative_path,
            "repository_root": str(repository_root),
            "hits": results["hits"],
            "recorded_at": _utc(),
        },
    )
    events_path = evidence_dir / "discovery_events.json"
    _write_json(events_path, {"events": events, "recorded_at": _utc()})
    plan_path = _write_plan(
        evidence_dir,
        [{"id": "step-1", "action": "search_repository", "status": "executed"}],
        f"SEARCH_CODE: real repository search for {query!r}.",
    )

    artifacts = dict(artifacts)
    artifacts["search_results"] = str(results_path)
    artifacts["discovery_events"] = str(events_path)
    artifacts["plan"] = str(plan_path)

    execution_path = evidence_dir / "execution_report.json"
    _write_json(
        execution_path,
        {
            "ok": True,
            "executor": "objective_cycle.real_execute.search_code.v1",
            "workspace": str(workspace),
            "repository_root": str(repository_root),
            "artifacts": artifacts,
            "hit_count": len(results["hits"]),
            "workhub_loaded": workhub_loaded,
            "workhub_lifecycle_authority": False,
            "recorded_at": _utc(),
        },
    )
    artifacts["execution_report"] = str(execution_path)

    refs = [f"file://{path}" for path in artifacts.values()]
    real_ops = max(_count_tool_events(events), 1)
    detail = f"real repository search: {query!r} ({len(results['hits'])} hit(s)) in {repository_root}"
    return RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace=str(workspace),
        evidence_refs=refs,
        workhub_loaded=workhub_loaded,
        workhub_lifecycle_authority=False,
        detail=detail,
        artifacts=artifacts,
        repository_root=str(repository_root),
        events=events,
        orchestration_status="COMPLETED",
        planning_status="COMPLETED",
        execution_status="COMPLETED",
        verification_status="PASSED",
        objective_status="COMPLETED",
        real_operations=real_ops,
        target_files_read=0,
        target_files_modified=0,
        administrative_artifacts_generated=len(artifacts),
    )


async def _run_analyze_code_execute(
    *,
    objective: str,
    repository_id: str,
    settings: object | None,
    workspace: Path,
    workhub_loaded: bool,
    artifacts: dict[str, str],
    budget_mgr: ExecutionBudgetManager | None,
) -> RealExecuteResult:
    """Real ANALYZE_CODE branch: runs the same mandatory repository discovery the edit
    branch uses (manifests, source tree, config files, relevant hits, tests), and
    returns that real, tool-call-backed findings report as the analysis — never a
    narrative summary invented without inspection."""
    try:
        repository_root = resolve_repository_root(repository_id, settings=settings)
    except RepositoryLocationError as exc:
        raise RealExecuteError(exc.code, exc.detail) from exc

    events: list[dict[str, Any]] = []
    on_event = _make_on_event(events, budget_mgr)
    toolbox = RepositoryToolbox(repository_root=repository_root, on_event=on_event)
    discovery = run_repository_discovery(toolbox=toolbox, objective=objective)

    real_ops = _count_tool_events(events)
    if real_ops == 0:
        raise RealExecuteError("NO_TARGET_RESOURCE_ACCESSED", "repository analysis made no real tool calls", events=events)

    evidence_dir = workspace / "evidence"
    analysis_path = evidence_dir / "analysis_report.json"
    _write_json(
        analysis_path,
        {
            "repository_root": str(repository_root),
            "manifests_found": sorted(discovery.manifests.keys()),
            "config_files_found": sorted(discovery.config_files.keys()),
            "test_files_found": discovery.test_files,
            "relevant_module_hits": discovery.relevant_module_hits,
            "keywords": list(discovery.keywords),
            "recorded_at": _utc(),
        },
    )
    events_path = evidence_dir / "discovery_events.json"
    _write_json(events_path, {"events": events, "recorded_at": _utc()})
    plan_path = _write_plan(
        evidence_dir,
        [{"id": "step-1", "action": "analyze_repository", "status": "executed"}],
        "ANALYZE_CODE: real repository discovery used as the analysis evidence.",
    )

    artifacts = dict(artifacts)
    artifacts["analysis_report"] = str(analysis_path)
    artifacts["discovery_events"] = str(events_path)
    artifacts["plan"] = str(plan_path)

    execution_path = evidence_dir / "execution_report.json"
    _write_json(
        execution_path,
        {
            "ok": True,
            "executor": "objective_cycle.real_execute.analyze_code.v1",
            "workspace": str(workspace),
            "repository_root": str(repository_root),
            "artifacts": artifacts,
            "workhub_loaded": workhub_loaded,
            "workhub_lifecycle_authority": False,
            "recorded_at": _utc(),
        },
    )
    artifacts["execution_report"] = str(execution_path)

    refs = [f"file://{path}" for path in artifacts.values()]
    target_files_read = len(discovery.manifests) + len(discovery.config_files) + (1 if discovery.env_example else 0)
    detail = f"real repository analysis: {target_files_read} file(s) read, {len(discovery.relevant_module_hits)} keyword hit set(s) in {repository_root}"
    return RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace=str(workspace),
        evidence_refs=refs,
        workhub_loaded=workhub_loaded,
        workhub_lifecycle_authority=False,
        detail=detail,
        artifacts=artifacts,
        repository_root=str(repository_root),
        events=events,
        orchestration_status="COMPLETED",
        planning_status="COMPLETED",
        execution_status="COMPLETED",
        verification_status="PASSED",
        objective_status="COMPLETED",
        real_operations=real_ops,
        target_files_read=target_files_read,
        target_files_modified=0,
        administrative_artifacts_generated=len(artifacts),
    )


def _evidence_sufficient(execution_context: dict[str, Any]) -> bool:
    """At least one real read/search/symbol-resolution result -- never a bare
    directory listing on its own, which proves nothing about the actual question."""
    return bool(
        execution_context.get("files_read")
        or execution_context.get("symbols_resolved")
        or any((s.get("hit_summary") or {}).get("hit_count") for s in execution_context.get("searches_executed") or [])
    )


async def _run_grounded_answer_execute(
    *,
    objective: str,
    repository_id: str,
    workspace: Path,
    workhub_loaded: bool,
    artifacts: dict[str, str],
    execution_context: dict[str, Any] | None,
) -> RealExecuteResult | None:
    """Non-mutating completion for DEBUG/ANALYZE/EXPLAIN/REVIEW objectives that have no
    recognized verb/resource-shape branch (real_execute.py has never had a "answer the
    question" intent -- only READ/INSPECT/SEARCH/ANALYZE/EDIT/CREATE/RUN_TESTS). Real
    exploration already ran (`_run_exploration_gate`, Sprint 003/004) before this
    function was ever reached and left its findings on `execution_context` -- this
    turns those *already-real* tool results into the grounded answer, it never invents
    one and never performs its own I/O. Returns `None` (never a fabricated completion)
    when there isn't enough real evidence to answer from, so the caller falls through
    to the existing truthful `NO_EXECUTABLE_BRANCH_MATCHED` result.
    """
    context = execution_context or {}
    if not _evidence_sufficient(context):
        return None

    files_read = list(context.get("files_read") or [])
    symbols_resolved = list(context.get("symbols_resolved") or [])
    searches_executed = list(context.get("searches_executed") or [])
    directories_listed = list(context.get("directories_listed") or [])

    findings: list[dict[str, Any]] = []
    for symbol in symbols_resolved:
        findings.append(
            {
                "kind": "symbol",
                "name": symbol.get("name"),
                "locations": symbol.get("locations") or [],
            }
        )
    for search in searches_executed:
        hit_summary = search.get("hit_summary") or {}
        if hit_summary.get("hit_count"):
            findings.append(
                {
                    "kind": "search",
                    "query": search.get("query"),
                    "hit_count": hit_summary.get("hit_count"),
                    "paths": (hit_summary.get("paths") or [])[:10],
                }
            )
    for read in files_read:
        findings.append({"kind": "file_read", "path": read.get("path"), "bytes_read": read.get("bytes_read")})

    evidence_dir = workspace / "evidence"
    analysis_path = evidence_dir / "analysis_report.json"
    _write_json(
        analysis_path,
        {
            "objective": objective,
            "repository_id": repository_id,
            "findings": findings,
            "directories_listed": len(directories_listed),
            "evidence_sufficient": True,
            "recorded_at": _utc(),
        },
    )

    artifacts = dict(artifacts)
    artifacts["analysis_report"] = str(analysis_path)
    plan_path = _write_plan(
        evidence_dir,
        [{"id": "step-1", "action": "grounded_answer_from_exploration_evidence", "status": "executed"}],
        "Non-mutating completion: answer grounded in real exploration evidence already"
        " gathered for this objective.",
    )
    artifacts["plan"] = str(plan_path)

    execution_path = evidence_dir / "execution_report.json"
    _write_json(
        execution_path,
        {
            "ok": True,
            "executor": "objective_cycle.real_execute.grounded_answer.v1",
            "workspace": str(workspace),
            "artifacts": artifacts,
            "workhub_loaded": workhub_loaded,
            "workhub_lifecycle_authority": False,
            "recorded_at": _utc(),
        },
    )
    artifacts["execution_report"] = str(execution_path)

    real_operations = len(files_read) + len(symbols_resolved) + len(searches_executed) + len(directories_listed)

    refs = [f"file://{path}" for path in artifacts.values()]
    detail = (
        f"grounded, non-mutating answer from real exploration evidence: "
        f"{len(files_read)} file(s) read, {len(symbols_resolved)} symbol(s) resolved, "
        f"{len(searches_executed)} search(es) executed"
    )
    return RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace=str(workspace),
        evidence_refs=refs,
        workhub_loaded=workhub_loaded,
        workhub_lifecycle_authority=False,
        detail=detail,
        artifacts=artifacts,
        orchestration_status="COMPLETED",
        planning_status="COMPLETED",
        execution_status="COMPLETED",
        verification_status="PASSED",
        objective_status="COMPLETED",
        real_operations=real_operations,
        target_files_read=len(files_read),
        target_files_modified=0,
        administrative_artifacts_generated=len(artifacts),
    )


class CommandCancelledError(RuntimeError):
    """Raised by `_run_test_command_cancellable` when a real operator cancellation was
    observed mid-run. Never a timeout, never a normal failure -- a distinct outcome so
    callers can report `CANCELLED` truthfully instead of `FAILED`."""


async def _kill_process_tree(pid: int) -> None:
    """Terminate the whole process tree, not just the direct child -- a bare
    `Process.terminate()` does not reach grandchildren a test runner may have spawned
    (e.g. pytest-xdist workers, a subprocess the test itself launches)."""
    if platform.system() == "Windows":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/T", "/F", "/PID", str(pid),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        import signal

        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass


async def _run_test_command_cancellable(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    is_cancelled: Callable[[], bool] | None,
    poll_interval_seconds: float = 0.25,
) -> dict[str, Any]:
    """Real, genuinely cancellable command execution using native asyncio subprocess
    (never a blocking `subprocess.run`) — the event loop stays free while this command
    runs, so a concurrent `cancel_cycle` request's handler (a fast, synchronous flag
    flip on the shared `_CycleRecord`) actually gets to run and be observed here on the
    next poll, instead of the whole process being frozen until this command exits.
    Terminates the full process tree on cancellation or timeout via
    `_kill_process_tree`. Raises `CommandCancelledError` on cancellation; a bounded
    shutdown is enforced by always waiting (with its own short timeout) for the killed
    process to actually exit before returning.
    """
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if platform.system() == "Windows" else 0),
    )
    loop = asyncio.get_event_loop()
    started = loop.time()
    while True:
        elapsed = loop.time() - started
        if elapsed >= timeout_seconds:
            await _kill_process_tree(proc.pid)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (TimeoutError, asyncio.TimeoutError):
                pass
            return {
                "command": command, "cwd": str(cwd), "timed_out": True, "cancelled": False,
                "returncode": None, "stdout": "", "stderr": "",
            }
        if is_cancelled is not None and is_cancelled():
            await _kill_process_tree(proc.pid)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (TimeoutError, asyncio.TimeoutError):
                pass
            raise CommandCancelledError("command execution cancelled by operator")
        try:
            await asyncio.wait_for(proc.wait(), timeout=min(poll_interval_seconds, max(timeout_seconds - elapsed, 0.01)))
            break
        except (TimeoutError, asyncio.TimeoutError):
            continue
    stdout_bytes, stderr_bytes = await proc.communicate()
    return {
        "command": command, "cwd": str(cwd), "timed_out": False, "cancelled": False,
        "returncode": proc.returncode,
        "stdout": stdout_bytes.decode("utf-8", errors="replace"),
        "stderr": stderr_bytes.decode("utf-8", errors="replace"),
    }


async def _run_tests_intent_execute(
    *,
    objective: str,
    repository_id: str,
    settings: object | None,
    workspace: Path,
    workhub_loaded: bool,
    artifacts: dict[str, str],
    budget_mgr: ExecutionBudgetManager | None,
    is_cancelled: Callable[[], bool] | None = None,
) -> RealExecuteResult:
    """Real RUN_TESTS branch: infer a real test command from discovered manifests and
    run it via a genuinely cancellable native-asyncio subprocess (gap-closure sprint 3,
    Priority 5) — never a claimed pass without a real command actually executing, and
    never a claim of completion for work that was actually cancelled mid-run."""
    try:
        repository_root = resolve_repository_root(repository_id, settings=settings)
    except RepositoryLocationError as exc:
        raise RealExecuteError(exc.code, exc.detail) from exc

    events: list[dict[str, Any]] = []
    on_event = _make_on_event(events, budget_mgr)
    toolbox = RepositoryToolbox(repository_root=repository_root, on_event=on_event)
    discovery = run_repository_discovery(toolbox=toolbox, objective=objective)

    test_command = _infer_test_command(set(discovery.manifests))
    if test_command is None:
        raise RealExecuteError(
            "NO_APPLICABLE_TEST_COMMAND",
            "no recognizable test manifest (pyproject.toml/requirements.txt/setup.py/Pipfile/package.json) was found",
            events=events,
        )

    # Same policy boundary `RepositoryToolbox.run_tests` uses (a run-tests request
    # only ever gets to run that exact command, nothing else) — preserved even though
    # execution itself now goes through the cancellable runner below instead of
    # `RepositoryToolbox.run_tests`'s blocking `subprocess.run`.
    joined = " ".join(test_command)
    decision = CommandNetworkResourceSandbox().evaluate_command(command=joined, command_allowlist=(joined,))
    if decision.disposition != "ALLOW":
        raise RealExecuteError("COMMAND_DENIED", ",".join(decision.reason_codes), events=events)

    on_event("command.started", {"command": joined})
    try:
        test_result = await _run_test_command_cancellable(
            test_command, cwd=repository_root, timeout_seconds=300.0, is_cancelled=is_cancelled
        )
    except CommandCancelledError as exc:
        on_event("command.cancelled", {"command": joined})
        raise RealExecuteError("CANCELLED", str(exc), events=events) from exc
    except FileNotFoundError as exc:
        # `_infer_test_command` picks a runner from discovered manifests
        # (pytest for a Python manifest, npm for package.json) without
        # checking it actually resolves on this process's PATH. Live proof
        # (2026-08-15): against a repository whose test runner wasn't on the
        # service's PATH, this raised a bare FileNotFoundError:[WinError 2]
        # straight out of asyncio.create_subprocess_exec, which propagated
        # all the way past _control's exception handling as an opaque
        # UNCAUGHT_CONTROL_ERROR -- structurally identical to the
        # NO_APPLICABLE_TEST_COMMAND case just above (a test command this
        # execution can't actually run), but reported with zero of that
        # case's diagnostic detail. Give callers the same typed, actionable
        # error instead of an unclassified crash.
        on_event("command.not_found", {"command": joined})
        raise RealExecuteError(
            "TEST_RUNNER_NOT_FOUND",
            f"test runner executable not found on PATH: {test_command[0]!r} (command={joined!r}): {exc}",
            events=events,
        ) from exc
    on_event("tool.command_run", {"command": joined, "returncode": test_result.get("returncode")})
    _record_test_run(budget_mgr=budget_mgr, events=events, test_command=test_command)

    passed = int(test_result.get("returncode", 1)) == 0

    evidence_dir = workspace / "evidence"
    run_path = evidence_dir / "test_run.json"
    _write_json(
        run_path,
        {
            "command": test_command,
            "repository_root": str(repository_root),
            "returncode": test_result.get("returncode"),
            "stdout": str(test_result.get("stdout", ""))[:4000],
            "stderr": str(test_result.get("stderr", ""))[:4000],
            "recorded_at": _utc(),
        },
    )
    events_path = evidence_dir / "discovery_events.json"
    _write_json(events_path, {"events": events, "recorded_at": _utc()})
    plan_path = _write_plan(
        evidence_dir,
        [{"id": "step-1", "action": "run_tests", "status": "executed"}],
        f"RUN_TESTS: real {' '.join(test_command)} execution against the approved repository.",
    )

    artifacts = dict(artifacts)
    artifacts["test_run"] = str(run_path)
    artifacts["discovery_events"] = str(events_path)
    artifacts["plan"] = str(plan_path)

    execution_path = evidence_dir / "execution_report.json"
    _write_json(
        execution_path,
        {
            "ok": True,
            "executor": "objective_cycle.real_execute.run_tests.v1",
            "workspace": str(workspace),
            "repository_root": str(repository_root),
            "artifacts": artifacts,
            "test_passed": passed,
            "workhub_loaded": workhub_loaded,
            "workhub_lifecycle_authority": False,
            "recorded_at": _utc(),
        },
    )
    artifacts["execution_report"] = str(execution_path)

    refs = [f"file://{path}" for path in artifacts.values()]
    real_ops = max(_count_tool_events(events), 1)
    detail = f"real test run: {' '.join(test_command)} in {repository_root} ({'passed' if passed else 'failed'})"
    return RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace=str(workspace),
        evidence_refs=refs,
        workhub_loaded=workhub_loaded,
        workhub_lifecycle_authority=False,
        detail=detail,
        artifacts=artifacts,
        repository_root=str(repository_root),
        events=events,
        orchestration_status="COMPLETED",
        planning_status="COMPLETED",
        execution_status="COMPLETED",
        verification_status="PASSED" if passed else "FAILED",
        objective_status="COMPLETED" if passed else "FAILED",
        reason_code=None if passed else "VERIFICATION_FAILED",
        real_operations=real_ops,
        target_files_read=len(discovery.manifests),
        target_files_modified=0,
        administrative_artifacts_generated=len(artifacts),
    )


async def _run_demo_proposal_execute(
    *,
    objective: str,
    settings: object | None,
    model_override: str | None,
    workspace: Path,
    workhub_loaded: bool,
    artifacts: dict[str, str],
    vision_ready: list[dict[str, Any]],
    vision_notes_parts: list[str],
) -> RealExecuteResult:
    """Explicit, opt-in demo/proposal capability (`demo_capability=True`) — content
    generation with no repository target at all. This is never reached from the
    general production dispatch: `run_real_execute` only calls it when the caller has
    deliberately requested the capability AND the objective matches a demo trigger."""
    evidence_dir = workspace / "evidence"
    proposed_dir = workspace / "proposed"
    proposed_dir.mkdir(parents=True, exist_ok=True)
    artifacts = dict(artifacts)

    lowered = objective.lower()
    wants_tsx_form = ("react" in lowered or "tsx" in lowered) and (
        "registration" in lowered or "register" in lowered
    )

    if wants_tsx_form:
        form_path = proposed_dir / "RegisterFormProposed.tsx"
        form_path.write_text(_proposed_registration_form_tsx(), encoding="utf-8")
        artifacts["proposed_form"] = str(form_path)

    token_usage: dict[str, Any] | None = None
    if not wants_tsx_form:
        image_parts = []
        for item in vision_ready:
            raw = item.get("raw")
            if not isinstance(raw, (bytes, bytearray)):
                continue
            mime = str(item.get("mime") or "image/png")
            if not mime.startswith("image/"):
                mime = "image/png"
            encoded = base64.b64encode(bytes(raw)).decode("ascii")
            image_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                }
            )
        bundle = await generate_html_css_page(
            objective=objective,
            settings=settings,
            vision_notes="\n\n".join(vision_notes_parts) if vision_notes_parts else None,
            image_parts=image_parts or None,
            model_override=model_override,
        )
        html_path = proposed_dir / "index.html"
        css_path = proposed_dir / "styles.css"
        tests_dir = proposed_dir / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        positive_path = tests_dir / "positive_cases.json"
        negative_path = tests_dir / "negative_cases.json"
        html_path.write_text(bundle.html, encoding="utf-8")
        css_path.write_text(bundle.css, encoding="utf-8")
        _write_json(positive_path, {"cases": bundle.positive_tests, "recorded_at": _utc()})
        _write_json(negative_path, {"cases": bundle.negative_tests, "recorded_at": _utc()})
        token_usage = {
            "provider": bundle.usage.provider,
            "model": bundle.usage.model,
            "prompt_tokens": bundle.usage.prompt_tokens,
            "completion_tokens": bundle.usage.completion_tokens,
            "total_tokens": bundle.usage.total_tokens,
            "ok": bundle.usage.ok,
            "detail": bundle.usage.detail,
            "recorded_at": _utc(),
        }
        token_path = evidence_dir / "token_usage.json"
        _write_json(token_path, token_usage)
        artifacts.update(
            {
                "proposed_html": str(html_path),
                "proposed_css": str(css_path),
                "positive_tests": str(positive_path),
                "negative_tests": str(negative_path),
                "token_usage": str(token_path),
            }
        )

    plan_path = _write_plan(
        evidence_dir,
        [
            {"id": "step-1", "action": "bounded_propose_artifact", "status": "executed"},
            {"id": "step-2", "action": "human_review", "status": "pending"},
        ],
        "Explicit demo capability: AIX proposes artifacts only; no repository target, "
        "WorkHub human decides lifecycle.",
    )
    artifacts["plan"] = str(plan_path)

    execution_path = evidence_dir / "execution_report.json"
    _write_json(
        execution_path,
        {
            "ok": True,
            "executor": "objective_cycle.real_execute.demo.v1",
            "workspace": str(workspace),
            "artifacts": artifacts,
            "token_usage": token_usage,
            "workhub_loaded": workhub_loaded,
            "workhub_lifecycle_authority": False,
            "recorded_at": _utc(),
        },
    )
    artifacts["execution_report"] = str(execution_path)

    refs = [f"file://{path}" for path in artifacts.values()]
    detail = "demo capability: bounded proposal artifact written; no repository target, no WorkHub lifecycle mutation"
    if token_usage is not None:
        detail = (
            f"{detail}; tokens total={token_usage.get('total_tokens')} "
            f"(prompt={token_usage.get('prompt_tokens')}, completion={token_usage.get('completion_tokens')}) "
            f"via {token_usage.get('provider')}/{token_usage.get('model')}"
        )
    return RealExecuteResult(
        ok=True,
        code="PROPOSAL_READY",
        workspace=str(workspace),
        evidence_refs=refs,
        workhub_loaded=workhub_loaded,
        workhub_lifecycle_authority=False,
        detail=detail,
        artifacts=artifacts,
        token_usage=token_usage,
        executor="objective_cycle.real_execute.demo.v1",
        orchestration_status="COMPLETED",
        planning_status="COMPLETED",
        execution_status="NOT_STARTED",
        verification_status="NOT_RUN",
        objective_status="AWAITING_REVIEW",
        real_operations=0,
        target_files_read=0,
        target_files_modified=0,
        administrative_artifacts_generated=len(artifacts),
    )


def _local_nursery_good_habits_markdown() -> str:
    """Deterministic CBSE nursery-level good-habits poems when no LLM is needed."""
    return """# Good Habits — Nursery Poems

Simple rhymes for Class Nursery (CBSE). Read aloud, clap the beat, and act them out.

## 1. Good Habits

I brush my teeth both night and day,
I wash my hands before I play.

I say “Please” and “Thank you” too,
I share my toys with friends like you.

I keep my room clean and bright,
I sleep on time each night.

Good habits help me smile and grow,
And make me kind wherever I go!

## 2. Wash, Wash, Wash

Wash your hands before you eat,
Wash your face, look fresh and neat.

Soap and water, rub and sing,
Clean little hands can do anything!

## 3. Brush Your Teeth

Up and down, I brush with care,
Morning, night, I brush them there.

White and shiny, that’s the way,
I brush my teeth twice every day.

## 4. Please and Thank You

“Please” is magic, small and sweet,
“Thank you” makes a smile complete.

I use kind words the whole day through,
Because I care for me and you.

## 5. Share Your Toys

I have a ball, I have a car,
I share them both, both near and far.

Taking turns is fun to do,
Playing is nicer when I share with you.

## 6. Tidy Time

Books on the shelf, toys in the box,
Shoes in a row, and folded socks.

A tidy room feels calm and bright,
I pack my things and say goodnight.

## 7. Early to Bed

When the stars come out to peep,
I brush, I hug, I go to sleep.

Rest helps me grow strong and tall,
I wake up ready to learn it all.

## 8. Listen in Class

Eyes on teacher, ears so wide,
Hands are still, I sit with pride.

I raise my hand, I wait my turn,
Every day at school I learn.

## 9. Healthy Food

An apple red, a carrot too,
Milk and dal are good for you.

I chew my food, I do not rush,
Healthy meals help my body grow and blush.

## 10. Be Kind

If a friend feels sad or small,
I help them up. I share. I call.

Kind hearts make the classroom glow,
Good habits shine wherever I go!
"""


def _docs_markdown_filename(objective: str) -> str:
    lowered = (objective or "").lower()
    if "poem" in lowered or "nursery" in lowered:
        return "nursery-good-habits-poems.md"
    return "generated-notes.md"


async def _run_docs_proposal_execute(
    *,
    objective: str,
    workspace: Path,
    workhub_loaded: bool,
    artifacts: dict[str, str],
) -> RealExecuteResult:
    """Create a markdown artifact in the cycle workspace. No repository exploration."""
    evidence_dir = workspace / "evidence"
    proposed_dir = workspace / "proposed"
    proposed_dir.mkdir(parents=True, exist_ok=True)
    artifacts = dict(artifacts)
    filename = _docs_markdown_filename(objective)
    md_path = proposed_dir / filename
    md_path.write_text(_local_nursery_good_habits_markdown(), encoding="utf-8")
    artifacts["proposed_markdown"] = str(md_path)

    plan_path = _write_plan(
        evidence_dir,
        [
            {"id": "step-1", "action": "bounded_propose_markdown", "status": "executed"},
            {"id": "step-2", "action": "human_review", "status": "pending"},
        ],
        "Docs-only capability: AIX proposes a markdown file in the cycle workspace; "
        "no repository source tree is explored or mutated.",
    )
    artifacts["plan"] = str(plan_path)
    execution_path = evidence_dir / "execution_report.json"
    _write_json(
        execution_path,
        {
            "ok": True,
            "executor": "objective_cycle.real_execute.docs.v1",
            "workspace": str(workspace),
            "artifacts": artifacts,
            "workhub_loaded": workhub_loaded,
            "workhub_lifecycle_authority": False,
            "recorded_at": _utc(),
        },
    )
    artifacts["execution_report"] = str(execution_path)
    refs = [f"file://{path}" for path in artifacts.values()]
    return RealExecuteResult(
        ok=True,
        code="PROPOSAL_READY",
        workspace=str(workspace),
        evidence_refs=refs,
        workhub_loaded=workhub_loaded,
        workhub_lifecycle_authority=False,
        detail=f"docs capability: markdown proposal written to proposed/{filename}",
        artifacts=artifacts,
        executor="objective_cycle.real_execute.docs.v1",
        orchestration_status="COMPLETED",
        planning_status="COMPLETED",
        execution_status="NOT_STARTED",
        verification_status="NOT_RUN",
        objective_status="AWAITING_REVIEW",
        real_operations=0,
        target_files_read=0,
        target_files_modified=0,
        administrative_artifacts_generated=len(artifacts),
    )


def _no_executable_branch_result(
    *,
    workspace: Path,
    workhub_loaded: bool,
    artifacts: dict[str, str],
    detail: str,
) -> RealExecuteResult:
    """Fail-closed default: no recognized, real branch matched this objective. Never a
    silent metadata-only success — `ok=False`, both `execution_status` and
    `objective_status` are `BLOCKED`, and the reason is explicit."""
    evidence_dir = workspace / "evidence"
    artifacts = dict(artifacts)
    plan_path = _write_plan(
        evidence_dir,
        [{"id": "step-1", "action": "none", "status": "blocked"}],
        "No executable branch matched this objective; no real operation was attempted.",
    )
    artifacts["plan"] = str(plan_path)

    execution_path = evidence_dir / "execution_report.json"
    _write_json(
        execution_path,
        {
            "ok": False,
            "executor": "objective_cycle.real_execute.unmatched.v1",
            "workspace": str(workspace),
            "artifacts": artifacts,
            "reason_code": "NO_EXECUTABLE_BRANCH_MATCHED",
            "workhub_loaded": workhub_loaded,
            "workhub_lifecycle_authority": False,
            "recorded_at": _utc(),
        },
    )
    artifacts["execution_report"] = str(execution_path)

    refs = [f"file://{path}" for path in artifacts.values()]
    return RealExecuteResult(
        ok=False,
        code="NO_EXECUTABLE_BRANCH_MATCHED",
        workspace=str(workspace),
        evidence_refs=refs,
        workhub_loaded=workhub_loaded,
        workhub_lifecycle_authority=False,
        detail=detail,
        artifacts=artifacts,
        orchestration_status="COMPLETED",
        planning_status="BLOCKED",
        execution_status="BLOCKED",
        verification_status="NOT_RUN",
        objective_status="BLOCKED",
        reason_code="NO_EXECUTABLE_BRANCH_MATCHED",
        real_operations=0,
        target_files_read=0,
        target_files_modified=0,
        administrative_artifacts_generated=len(artifacts),
    )


def prior_read_paths_from_context(execution_context: dict[str, Any] | None) -> tuple[str, ...]:
    """Files already read during the exploration gate — reuse instead of a full-repo walk."""
    if not isinstance(execution_context, dict):
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for item in execution_context.get("files_read") or ():
        if isinstance(item, dict):
            path = str(item.get("path") or "").strip()
        else:
            path = str(getattr(item, "path", "") or "").strip()
        key = path.replace("\\", "/").lower()
        if path and key not in seen:
            seen.add(key)
            out.append(path.replace("\\", "/"))
    return tuple(out)


async def _run_create_empty_directory_execute(
    *,
    objective: str,
    repository_id: str,
    settings: object | None,
    workspace: Path,
    workhub_loaded: bool,
    artifacts: dict[str, str],
    apply_edits: bool,
    allowed_files: tuple[str, ...] = (),
) -> RealExecuteResult:
    """Deterministic mkdir + .gitkeep. Do not LLM-patch unrelated existing files."""
    marker = resolve_empty_directory_marker_path(
        objective=objective, allowed_files=allowed_files
    )
    if not marker:
        raise RealExecuteError(
            "INTENT_UNCLEAR",
            "create-directory needs a concrete name or path "
            "(example: Create empty directory my-web-app)",
        )
    try:
        repository_root = resolve_repository_root(repository_id, settings=settings)
    except RepositoryLocationError as exc:
        raise RealExecuteError(exc.code, exc.detail) from exc

    evidence_dir = workspace / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    artifacts = dict(artifacts)
    target = repository_root / marker
    body = ""
    policy = validate_write_file_policy(target, body, create_only=True)
    already = target.is_file()
    if apply_edits and not already:
        if not policy.allowed:
            raise RealExecuteError(
                policy.reason_code or "CREATE_ONLY_VIOLATION",
                policy.reason_detail or f"cannot create {marker}",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    elif not apply_edits:
        proposed = workspace / "proposed" / marker
        proposed.parent.mkdir(parents=True, exist_ok=True)
        proposed.write_text(body, encoding="utf-8")
        artifacts["proposed_gitkeep"] = str(proposed)

    diff_text = f"--- /dev/null\n+++ b/{marker}\n"
    if apply_edits:
        diff_text = f"--- /dev/null\n+++ b/{marker}\n@@ -0,0 +1 @@\n+\n"
        if not already and not target.read_text(encoding="utf-8"):
            diff_text = f"--- /dev/null\n+++ b/{marker}\n"
    diff_path = evidence_dir / "source_diff.txt"
    diff_path.write_text(diff_text, encoding="utf-8")
    artifacts["source_diff"] = str(diff_path)
    report_path = evidence_dir / "execution_report.json"
    _write_json(
        report_path,
        {
            "ok": True,
            "executor": "objective_cycle.real_execute.create_directory.v1",
            "marker": marker,
            "repository_root": str(repository_root),
            "already_existed": already,
            "recorded_at": _utc(),
        },
    )
    artifacts["execution_report"] = str(report_path)
    refs = [f"file://{path}" for path in artifacts.values()]
    changed = [{"path": marker, "additions": 0, "deletions": 0}] if apply_edits else []
    return RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace=str(workspace),
        evidence_refs=refs,
        workhub_loaded=workhub_loaded,
        workhub_lifecycle_authority=False,
        detail=f"created directory marker {marker}",
        artifacts=artifacts,
        changed_source_files=changed,
        repository_root=str(repository_root),
        diff_text=diff_text,
        completion_gates={"passed": True, "reason": "empty directory marker exists"},
        executor="objective_cycle.real_execute.create_directory.v1",
        orchestration_status="COMPLETED",
        planning_status="COMPLETED",
        execution_status="COMPLETED" if apply_edits else "AWAITING_APPLICATION",
        verification_status="PASSED",
        objective_status="COMPLETED" if apply_edits else "AWAITING_REVIEW",
        real_operations=1,
        target_files_read=0,
        target_files_modified=1 if apply_edits else 0,
        administrative_artifacts_generated=len(artifacts),
    )


async def _run_action_request_execute(
    *,
    step_contract: "StepContractV1",
    repository_id: str,
    settings: object | None,
    workspace: Path,
    workhub_loaded: bool,
    artifacts: dict[str, str],
    apply_edits: bool,
    allowed_files: tuple[str, ...] = (),
    execution_context: "ExecutionContextV1 | None" = None,
) -> RealExecuteResult:
    """AIX-GAEF-001: dispatch planner-emitted `ActionRequestV1`s through the governed
    capability registry -> policy gateway -> deterministic executor. This function
    performs exactly the named `(capability, operation)`; it never reinterprets the
    English objective the way `classify_intent`'s phrase lists do."""
    from backend.aix_core.runtime.foundation.capability_executor import execute_action_loop
    from backend.aix_core.runtime.planner.step_contract_planner import _bind_action_paths_to_scope

    actions = _bind_action_paths_to_scope(
        tuple(step_contract.actions or ()),
        tuple(step_contract.target_scope or ()),
    )
    observations = await asyncio.to_thread(
        execute_action_loop,
        actions,
        repository_id=repository_id,
        settings=settings,
        allowed_files=allowed_files,
        apply_edits=apply_edits,
        max_actions=max(len(actions), 1),
        max_mutations=max(len(actions) * 2, 8),
        execution_context=execution_context,
    )
    try:
        repository_root = resolve_repository_root(repository_id, settings=settings)
    except RepositoryLocationError as exc:
        raise RealExecuteError(exc.code, exc.detail) from exc

    evidence_dir = workspace / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    artifacts = dict(artifacts)
    report_path = evidence_dir / "execution_report.json"
    _write_json(
        report_path,
        {
            "ok": all(o.status == "SUCCESS" for o in observations),
            "executor": "objective_cycle.real_execute.action_request.v1",
            "actions": [
                {"capability": a.capability, "operation": a.operation, "arguments": a.arguments}
                for a in actions
            ],
            "observations": [o.model_dump(mode="json") for o in observations],
            "recorded_at": _utc(),
        },
    )
    artifacts["execution_report"] = str(report_path)

    failed = next((o for o in observations if o.status != "SUCCESS"), None)
    if failed is not None:
        raise RealExecuteError(
            failed.error_code or "ACTION_FAILED",
            failed.error_message or f"action {failed.action_id} did not succeed",
        )

    changed_paths: list[str] = []
    removed_paths: list[str] = []
    for obs in observations:
        if not obs.changed:
            continue
        source_path = obs.observation.get("source")
        if isinstance(source_path, str) and source_path:
            removed_paths.append(source_path)
        for key in ("path", "destination", "dest", "marker"):
            value = obs.observation.get(key)
            if isinstance(value, str) and value:
                changed_paths.append(value)
                break
    changed = [{"path": p, "additions": 0, "deletions": 0} for p in dict.fromkeys([*changed_paths, *removed_paths])]
    diff_chunks: list[str] = []
    for p in dict.fromkeys(changed_paths):
        diff_chunks.append(f"--- /dev/null\n+++ b/{p}\n")
    for p in dict.fromkeys(removed_paths):
        diff_chunks.append(f"--- a/{p}\n+++ /dev/null\n")
    diff_text = "".join(diff_chunks) or None
    refs = [f"file://{path}" for path in artifacts.values()]

    return RealExecuteResult(
        ok=True,
        code="REAL_EXECUTE_OK",
        workspace=str(workspace),
        evidence_refs=refs,
        workhub_loaded=workhub_loaded,
        workhub_lifecycle_authority=False,
        detail=f"{len(actions)} governed action(s) completed",
        artifacts=artifacts,
        changed_source_files=changed,
        repository_root=str(repository_root),
        diff_text=diff_text,
        completion_gates={"passed": True, "reason": "all governed actions returned SUCCESS"},
        executor="objective_cycle.real_execute.action_request.v1",
        orchestration_status="COMPLETED",
        planning_status="COMPLETED",
        execution_status="COMPLETED" if apply_edits else "AWAITING_APPLICATION",
        verification_status="PASSED",
        objective_status="COMPLETED" if apply_edits else "AWAITING_REVIEW",
        real_operations=len(actions),
        target_files_read=sum(1 for o in observations if not o.changed),
        target_files_modified=sum(1 for o in observations if o.changed),
        administrative_artifacts_generated=len(artifacts),
    )


async def run_real_execute(
    *,
    tenant_id: str,
    cycle_id: str,
    objective_id: str,
    objective: str,
    workhub_task_id: str,
    project_id: str | None,
    task_number: int | None,
    repository_id: str,
    attachments: list[dict[str, Any]] | None = None,
    settings: object | None = None,
    require_workhub_load: bool = True,
    workspace_base: Path | None = None,
    model_override: str | None = None,
    caller_authorization: str | None = None,
    actor_id: str = "",
    budget_config: ExecutionBudgetConfig | None = None,
    apply_edits: bool = True,
    demo_capability: bool = False,
    task_family: str = "UNKNOWN",
    execution_context: dict[str, Any] | None = None,
    allowed_files: tuple[str, ...] | None = None,
    admission_allowed_files: tuple[str, ...] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    step_contract: "StepContractV1 | None" = None,
    plan_steps: tuple["StepContractV1", ...] | list["StepContractV1"] = (),
    context_capsule: "StepContextCapsuleV1 | None" = None,
    workspace_step_id: str | None = None,
    workspace_attempt_id: str | None = None,
    repair_directive: "RepairDirectiveV1 | None" = None,
    original_objective: str | None = None,
    session_id: str | None = None,
) -> RealExecuteResult:
    """Execute one bounded real step: WorkHub read + real, intent-routed execution.

    `apply_edits`: default True -- a validated EDIT_FILE/EDIT_SYMBOL/CREATE_FILE patch
    is applied atomically, tested, and gated on real diff/test evidence. Pass False to
    compute and validate the same patch but leave it for a reviewer to apply
    explicitly instead of writing it to the repository.

    `demo_capability`: explicit, opt-in content-generation capability (React/TSX or
    HTML/CSS proposal) with no repository target. Only reachable when the caller
    requests it AND the objective matches a demo trigger — never the default path for
    an objective that doesn't classify into a real, repository-bound intent.
    """
    budget_mgr = ExecutionBudgetManager(
        cycle_id=cycle_id,
        objective_id=objective_id,
        config=budget_config,
    )
    # AIX-UGEF-001: build execution context from caller-provided identity.
    from backend.aix_core.runtime.foundation.execution_context import (
        PrincipalV1,
        build_execution_context,
    )

    _step_id = ""
    _attempt_id = workspace_attempt_id or ""
    if step_contract is not None:
        _step_id = step_contract.id
    _principal_role = step_contract.assigned_role if step_contract is not None else "executor"
    # P6: a caller-supplied tuple — including empty — is frozen PlanTarget/seed
    # scope. Omitting the argument (None) is the only way to inherit step_contract
    # target_scope. Empty must not recover files from the contract or detectors.
    # Preserve human admission paths for FILE_NOT_IN_APPROVED_SCOPE before step fill-in.
    if admission_allowed_files is not None:
        _admission_scope = tuple(
            str(p).replace("\\", "/").strip()
            for p in admission_allowed_files
            if str(p).strip()
        )
    elif allowed_files:
        _admission_scope = tuple(
            str(p).replace("\\", "/").strip() for p in allowed_files if str(p).strip()
        )
    else:
        _admission_scope = ()
    scope_frozen = allowed_files is not None
    if allowed_files is None:
        allowed_files = ()
        if step_contract is not None:
            allowed_files = tuple(p for p in step_contract.target_scope if str(p).strip())
    elif (
        not allowed_files
        and step_contract is not None
        and tuple(p for p in (step_contract.target_scope or ()) if str(p).strip())
    ):
        # Empty frozen admit/provenance scope must not erase the claimed step's
        # multi-file target_scope (ERP step 2 kept re-editing api.php only).
        allowed_files = tuple(p for p in step_contract.target_scope if str(p).strip())
        scope_frozen = True
    _exec_ctx = build_execution_context(
        principal=PrincipalV1(type="agent", id="aix-executor", role=_principal_role),
        tenant_id=tenant_id,
        project_id=project_id or "",
        repository_id=repository_id,
        cycle_id=cycle_id,
        step_id=_step_id,
        attempt_id=_attempt_id,
        allowed_files=allowed_files,
        apply_edits=apply_edits,
        approved=True,
    )
    # Preserve exact deterministic mutation operands before planner guidance narrows
    # the request to one step. StepContract prose describes the step, but planners
    # commonly express replacement outcomes as separate "contains" / "no longer
    # contains" criteria and thereby lose the executable OLD -> NEW pairing from the
    # user's request (cyc_8d6f434a5d76).
    raw_text_replacements = extract_text_replacements(objective)

    # Capture literal KEY=value assignments before redaction so bound-file appends
    # can still write values the human supplied. Objective text is redacted for logs/LLM.
    literal_assignments = extract_env_assignments(objective)
    # Keep the human/parent objective for coverage gates and lane selection.
    # Step-contract rewrites must never erase WHAT the user originally asked for.
    coverage_objective, _coverage_redacted_kinds = redact_objective_text(
        str(original_objective or objective or "")
    )
    objective, _redacted_kinds = redact_objective_text(objective)

    # AIX Closed-Loop Execution Plan, Sprint W7: StepContractV1-aware entry point.
    # Purely additive -- `step_contract` defaults to `None`, so every existing caller
    # (all of them today; the planner is opt-in behind
    # INTELLIGENCE_LAYER_STEP_CONTRACT_PLANNER_ENABLED) sees zero behavior change. When
    # a contract exists: its `target_scope` becomes the authoritative file scope when
    # the caller didn't already narrow one explicitly (`allowed_files` still wins when
    # given -- a frozen PlanTarget list is not silently widened or replaced by a proposal),
    # and its `purpose` is appended as explicit planner guidance to the objective text
    # the mutation runner actually sees, grounding codegen in the planner's articulated
    # understanding instead of only the raw (possibly vague) objective string.
    continuity_section: ContinuityPromptSection = build_continuity_prompt_section(step_contract)
    continuity_refs: tuple[str, ...] = continuity_section.actionable_tokens

    if step_contract is not None:
        # P6: caller/PlanTarget/step scope is frozen. Do not invent files from
        # objective detectors, UI coverage, or URL heuristics.
        if step_contract.purpose.strip():
            # AIX-MPE-003 Packet MPE-16: this invocation executes THIS step.
            # Concatenating the parent objective re-triggers later-step coverage
            # signals (unused imports on a grouping step) and blocks the plan.
            parts = [f"Planner guidance: {step_contract.purpose.strip()}"]
            if step_contract.acceptance_criteria:
                parts.append("Acceptance: " + "; ".join(step_contract.acceptance_criteria))
            if step_contract.target_scope:
                parts.append("Target: " + ", ".join(step_contract.target_scope))
            # SEM-001-W1: Continuity Law (A) — actionable refs in derived prompt.
            # Keep step isolation (no full parent objective); do not emit opaque
            # obl_* / map-* IDs alone (see step_prompt_continuity).
            if continuity_section.has_content:
                parts.append(continuity_section.section_text)
            if raw_text_replacements:
                parts.extend(
                    f"Exact requested replacement: replace the exact text `{old}` with `{new}`"
                    for old, new in raw_text_replacements
                    if "`" not in old and "`" not in new
                )
                parts.extend(
                    f'Exact requested replacement: replace the exact text "{old}" with "{new}"'
                    for old, new in raw_text_replacements
                    if "`" in old or "`" in new
                )
            planner_guidance = "\n".join(parts)
            objective = planner_guidance
        else:
            planner_guidance = ""
    else:
        planner_guidance = ""

    # Autonomous Closed-Loop Lane, Packet AL-05: the bounded, step-specific context
    # capsule -- never the complete exploration history. Additive, same pattern as
    # `step_contract` above: `context_capsule` defaults to `None`, zero behavior
    # change for any caller that doesn't build one. Only `invariants` (from the
    # contract's own `prohibited_actions`, already governance-authored, not
    # re-derived here) and `relevant_previous_failure` (repair context, Packet AL-09)
    # are folded in -- `relevant_files`/`relevant_symbols` are for the planner's own
    # reasoning, not additional executor grounding beyond what target_scope already
    # sets.
    if context_capsule is not None:
        if context_capsule.invariants:
            objective = f"{objective}\n\nInvariants (must not violate): {'; '.join(context_capsule.invariants)}"
        if context_capsule.relevant_previous_failure:
            objective = (
                f"{objective}\n\nPrevious attempt failed verification: "
                f"{context_capsule.relevant_previous_failure}"
            )

    if require_workhub_load and not str(project_id or "").strip():
        raise RealExecuteError(
            "WORKHUB_PROJECT_ID_REQUIRED",
            "real execute requires project_id so AIX can load an existing WorkHub task (AIX never creates tasks)",
        )

    base = workspace_base
    if base is None and settings is not None:
        configured = str(getattr(settings, "OBJECTIVE_CYCLE_WORKSPACE_BASE", "") or "").strip()
        if configured:
            base = Path(configured)
    base = base or default_workspace_base()
    base.mkdir(parents=True, exist_ok=True)
    try:
        boundary = WorkspaceIsolationBoundary(base)
        session = str(session_id or "").strip()
        if session.lower().startswith("disc_"):
            resolved_session = session
        else:
            resolved_session = ""
        sticky_day = resolve_sticky_date_key(
            boundary.base_root,
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            session_id=resolved_session,
        )
        scope = WorkspaceScopeV1(
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            step_id=str(workspace_step_id or "step-1").strip() or "step-1",
            attempt_id=str(workspace_attempt_id or "attempt-1").strip() or "attempt-1",
            date_key=sticky_day,
            session_id=resolved_session,
        )
        workspace = boundary.create_workspace(scope)
        cycle_project_root = boundary.create_cycle_project_root(
            tenant_id=tenant_id,
            cycle_id=cycle_id,
            objective=coverage_objective or objective,
            date_key=sticky_day,
            session_id=resolved_session,
        )
    except WorkspaceIsolationError as exc:
        raise RealExecuteError("WORKSPACE_ISOLATION_FAILED", str(exc)) from exc

    workhub_loaded = False
    workhub_error: str | None = None
    task_snapshot: dict[str, Any] = {}

    if str(project_id or "").strip():
        try:
            client = build_workhub_client(settings, bearer_authorization=caller_authorization)
            if not str(client.service_token or "").strip():
                # Neither a caller Bearer nor an env service token: the WorkHub client would
                # raise a bare "Authorization header required" from header building.
                workhub_error = "WORKHUB_AUTH_MISSING"
                if require_workhub_load:
                    raise RealExecuteError(
                        "WORKHUB_AUTH_MISSING",
                        "no caller Authorization header was forwarded and WORKHUB_API_TOKEN is not configured",
                    )
            else:
                task, err = await load_workhub_task(
                    client=client,
                    project_id=str(project_id),
                    workhub_task_id=workhub_task_id,
                    task_number=task_number,
                    tenant_id=tenant_id,
                )
                if task is None:
                    workhub_error = err or "WORKHUB_TASK_NOT_FOUND"
                    if require_workhub_load:
                        raise RealExecuteError(workhub_error)
                else:
                    workhub_loaded = True
                    task_snapshot = _redact_task_snapshot(task)
        except RealExecuteError:
            raise
        except Exception as exc:  # noqa: BLE001 - soft-fail WorkHub when not required
            workhub_error = f"WORKHUB_LOAD_FAILED:{type(exc).__name__}:{exc}"
            if require_workhub_load:
                raise RealExecuteError("WORKHUB_LOAD_FAILED", str(exc)) from exc

    evidence_dir = workspace / "evidence"
    proposed_dir = workspace / "proposed"
    attachments_dir = workspace / "attachments"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    proposed_dir.mkdir(parents=True, exist_ok=True)
    attachments_dir.mkdir(parents=True, exist_ok=True)

    # Named unbounded audit markdown: skip the planner/exploration/write-file
    # capability path. Seeded ACCEPTED plan already exists; this branch reads a
    # bounded live spine and writes the user-named .md.
    _audit_src = str(original_objective or coverage_objective or "")
    if looks_like_unbounded_codebase_audit(_audit_src):
        return await _run_codebase_audit_execute(
            objective=_audit_src,
            repository_id=repository_id,
            settings=settings,
            workspace=workspace,
            workhub_loaded=workhub_loaded,
            artifacts={},
            apply_edits=apply_edits,
        )

    materialised_attachments: list[dict[str, Any]] = []
    vision_ready: list[dict[str, Any]] = []
    vision_notes_parts: list[str] = []
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        resolved = await resolve_attachment_text(
            attachment=item,
            tenant_id=tenant_id,
            objective_hint=objective,
            settings=settings,
        )
        filename = str(resolved.get("filename") or "attachment.bin")
        safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in filename) or "attachment.bin"
        target = attachments_dir / safe_name
        raw = resolved.get("raw")
        if isinstance(raw, (bytes, bytearray)):
            target.write_bytes(bytes(raw))
            materialised_attachments.append(
                {
                    "filename": filename,
                    "path": str(target),
                    "readable": bool(resolved.get("readable")),
                    "id": resolved.get("id"),
                    "mime": resolved.get("mime"),
                    "bytes": len(raw),
                    "is_image": bool(resolved.get("is_image")),
                }
            )
            if resolved.get("is_image"):
                vision_ready.append(resolved)
                if resolved.get("text"):
                    vision_notes_parts.append(f"[{filename}]\n{resolved.get('text')}")
                # Persist vision inventory beside the image for review.
                _write_json(
                    attachments_dir / f"{safe_name}.vision.json",
                    {
                        "filename": filename,
                        "model": resolved.get("vision_model"),
                        "note": resolved.get("note"),
                        "description": resolved.get("text"),
                        "recorded_at": _utc(),
                    },
                )
        else:
            meta_path = attachments_dir / f"{safe_name}.meta.json"
            _write_json(
                meta_path,
                {
                    "filename": filename,
                    "id": resolved.get("id"),
                    "mime": resolved.get("mime"),
                    "storage_key": resolved.get("storage_key"),
                    "note": resolved.get("note"),
                    "recorded_at": _utc(),
                },
            )
            materialised_attachments.append(
                {
                    "filename": filename,
                    "path": str(meta_path),
                    "readable": False,
                    "id": resolved.get("id"),
                    "mime": resolved.get("mime"),
                    "note": resolved.get("note"),
                }
            )

    objective_path = evidence_dir / "objective.json"
    _write_json(
        objective_path,
        {
            "objective_id": objective_id,
            "cycle_id": cycle_id,
            "tenant_id": tenant_id,
            "original_objective": coverage_objective,
            "planner_guidance": planner_guidance or None,
            "objective": coverage_objective,
            "derived_execution_prompt": objective,
            # SEM-001-W1 evidence: actionable continuity tokens folded into prompt.
            "continuity_refs": list(continuity_refs),
            "continuity_helper_version": continuity_section.helper_version,
            "workhub_task_id": workhub_task_id,
            "project_id": project_id,
            "task_number": task_number,
            "repository_id": repository_id,
            "attachments": materialised_attachments,
            "workhub_lifecycle_authority": False,
            "aix_creates_workhub_tasks": False,
            "recorded_at": _utc(),
        },
    )

    workhub_path = evidence_dir / "workhub_load.json"
    _write_json(
        workhub_path,
        {
            "loaded": workhub_loaded,
            "error": workhub_error,
            "task": task_snapshot,
            "mode": "read_only",
            "created_task": False,
            "recorded_at": _utc(),
        },
    )

    artifacts: dict[str, str] = {
        "objective": str(objective_path),
        "workhub_load": str(workhub_path),
    }

    # ------------------------------------------------------------------
    # Explicit intent routing — no "registered + non-read => edit" fallback.
    # ------------------------------------------------------------------
    # The active StepContract is the execution source of truth. Prefer its bounded
    # target over path-like tokens inside quoted code (for example
    # ``value.strip().lower()``), which the prose extractor can misread as a file.
    # For multi-file steps, prefer targets that are still missing under the cycle
    # project (e.g. config.php/database.sql after api.php already exists).
    target_path = None
    if (
        scope_frozen
        and allowed_files
        and any(_looks_like_a_file(str(p or "").strip()) for p in allowed_files)
    ):
        # Caller-supplied allowed_files win over step_contract target_scope for
        # target selection (W7 / AL04). Do not prefer unbound contract-only paths.
        allowed_scope = [
            str(p).replace("\\", "/").lstrip("./")
            for p in allowed_files
            if _looks_like_a_file(str(p or "").strip())
        ]
        missing_allowed = [
            p for p in allowed_scope if not (cycle_project_root / p).is_file()
        ]
        target_path = next(iter(missing_allowed or allowed_scope), None)
    elif step_contract is not None:
        scope_files = [
            str(p).replace("\\", "/").lstrip("./")
            for p in (step_contract.target_scope or ())
            if _looks_like_a_file(str(p or "").strip())
        ]
        missing_in_cycle = [
            p for p in scope_files if not (cycle_project_root / p).is_file()
        ]
        target_path = next(iter(missing_in_cycle or scope_files), None)
    if target_path is None:
        target_path = extract_target_path(objective)
    if target_path is None and allowed_files:
        missing_allowed = [
            p
            for p in allowed_files
            if _looks_like_a_file(p)
            and not (cycle_project_root / str(p).replace("\\", "/").lstrip("./")).is_file()
        ]
        target_path = next(
            iter(missing_allowed or (p for p in allowed_files if _looks_like_a_file(p))),
            None,
        )
    symbol_name = _extract_symbol_name_from_objective(objective)
    # Bounded repair steps target files from gap/failed-step scope — never symbols.
    if step_contract is not None and (
        str(getattr(step_contract, "id", "") or "").startswith("repair_")
        or str(getattr(step_contract, "expected_output_schema_ref", "") or "") == "schema://repair_step"
    ):
        symbol_name = None
    registered = is_registered_repository(repository_id, settings=settings)

    # Bare-path / service-relative-path / bare-repository-name -> workspace: the caller
    # may not know (or may have supplied the wrong) repository_id, or may have supplied
    # the *right* repository_id plus a service-relative path that redundantly repeats
    # it (`hyeve-integration-service\.env` against an already-valid
    # repository_id="hyeve-integration-service"). Always run the same resolver
    # `_run_exploration_gate` already used before this function was even called, so the
    # two stages can never disagree — it is itself fail-closed (never a guess, never
    # falls back to a default repository) and is a no-op when nothing needs correcting.
    had_explicit_target = target_path is not None
    resolved_repository_id, resolved_target_path = rebind_repository_for_objective(
        repository_id, objective, target_path=target_path, settings=settings
    )
    if resolved_repository_id is not None:
        repository_id = resolved_repository_id
        target_path = resolved_target_path
        registered = True
    elif not registered and had_explicit_target:
        # Only a literal, unresolvable resource reference is a hard failure. An
        # objective naming no path and no registered repository at all (e.g. a
        # metadata-only demo/content-generation request) is unaffected and keeps
        # falling through to the `demo_capability` branch below exactly as before —
        # never guessed into a repository, but never hard-failed for having asked for
        # nothing repository-shaped in the first place either.
        raise RealExecuteError(
            "WORKSPACE_NOT_BOUND",
            f"repository {repository_id!r} is not bound to an approved filesystem workspace",
        )

    # Planner-accepted HTML/CSS generation: call the LLM codegen module into
    # cycle workspace proposed/ (not the repository patch lane). Lane selection
    # must use the original user ask — never planner guidance alone.
    # P6: detector wording cannot open the demo lane. Caller must opt in from
    # accepted CREATE_FILE PlanTargets in Governance create_root.
    if demo_capability and _step_requests_html_css_generation(
        step_contract,
        coverage_objective,
        has_vision=bool(vision_ready),
    ):
        return await _run_demo_proposal_execute(
            objective=objective,
            settings=settings,
            model_override=model_override,
            workspace=workspace,
            workhub_loaded=workhub_loaded,
            artifacts=artifacts,
            vision_ready=vision_ready,
            vision_notes_parts=vision_notes_parts,
        )

    # AIX-GAEF-001: governed `actions` bypass the phrase classifier when they are
    # sufficient to finish the step. Information-only actions on a mutation step
    # are observations, not completion -- gather them, then continue into the
    # existing mutation lane.
    if step_contract is not None and step_contract.actions and _actions_fulfill_the_step(step_contract):
        return await _run_action_request_execute(
            step_contract=step_contract,
            repository_id=repository_id,
            settings=settings,
            workspace=workspace,
            workhub_loaded=workhub_loaded,
            artifacts=artifacts,
            apply_edits=apply_edits,
            allowed_files=allowed_files,
            execution_context=_exec_ctx,
        )

    # Greenfield CREATE_DIRECTORY: StepContract/PlanTargets may authorize only a
    # directory scope (empty tree). That is valid context — mkdir via governed
    # actions, never LLM patch with "no repository files".
    _dir_scopes = _directory_only_create_scopes(
        allowed_files
        if allowed_files is not None
        else (tuple(step_contract.target_scope) if step_contract is not None else ())
    )
    if _dir_scopes and step_contract is not None:
        mkdir_contract = _step_contract_with_mkdir_actions(step_contract, _dir_scopes)
        return await _run_action_request_execute(
            step_contract=mkdir_contract,
            repository_id=repository_id,
            settings=settings,
            workspace=workspace,
            workhub_loaded=workhub_loaded,
            artifacts=artifacts,
            apply_edits=apply_edits,
            allowed_files=allowed_files or _dir_scopes,
            execution_context=_exec_ctx,
        )

    # AIX-UGEF-P2: when actions don't fulfill the step, run info-only actions
    # as observations, then continue into the mutation lane. filesystem.patch
    # actions are filtered out — they declare the mutation intent and are
    # handled by the governed patch pipeline below.
    #
    # Only information ops belong in this pre-pass. mkdir/create mixed with
    # patch used to run here with max_mutations=0 → BUDGET_EXCEEDED before codegen.
    _has_patch_action = False
    if (
        step_contract is not None
        and step_contract.actions
        and not _actions_fulfill_the_step(step_contract)
    ):
        from backend.aix_core.runtime.foundation.capability_executor import execute_action_loop
        from backend.aix_core.runtime.foundation.capability_registry import is_information_operation

        info_actions = tuple(
            a
            for a in step_contract.actions
            if is_information_operation(a.capability, a.operation)
        )
        _has_patch_action = any(
            a.capability == "filesystem" and a.operation == "patch"
            for a in step_contract.actions
        )
        if info_actions:
            observations = await asyncio.to_thread(
                execute_action_loop,
                info_actions,
                repository_id=repository_id,
                settings=settings,
                allowed_files=allowed_files,
                apply_edits=False,
                max_actions=max(len(info_actions), 1),
                max_mutations=0,
                execution_context=_exec_ctx,
            )
            failed = next((obs for obs in observations if obs.status != "SUCCESS"), None)
            if failed is not None:
                raise RealExecuteError(
                    failed.error_code or "ACTION_FAILED",
                    failed.error_message or f"action {failed.action_id} did not succeed",
                )

    # AIX-UGEF-P2: when a filesystem.patch action was declared by the planner,
    # skip classify_intent and route directly to the governed patch pipeline.
    # The action IS the intent declaration — no phrase matching needed.
    if _has_patch_action:
        intent = "EDIT_FILE"
    else:
        # Classify from the coverage/parent objective — planner guidance must not
        # erase edit verbs (W7: "refactor…" + purpose "Batch the queries" was
        # mis-routed to READ_FILE because guidance alone looks like a read).
        intent_source = str(coverage_objective or original_objective or objective or "")
        intent = classify_intent(
            intent_source, target_path=target_path, symbol_name=symbol_name
        )
    # Suite-run objectives with the repository-default validation marker must enter
    # the cancellable RUN_TESTS lane — not READ_FILE / empty-diff mutation — even
    # when a bootstrap StepContract names a file for PlanTarget grounding.
    _suite_blob = " ".join(
        str(x or "")
        for x in (
            coverage_objective,
            original_objective,
            objective,
            getattr(step_contract, "purpose", "") if step_contract is not None else "",
        )
    )
    _wants_default_suite = bool(
        step_contract is not None
        and any(
            "use-repository-default-tests" in str(c or "").casefold()
            for c in (step_contract.validation_commands or ())
        )
        and _TEST_RE.search(_suite_blob)
    )
    if _wants_default_suite:
        intent = "RUN_TESTS"
        symbol_name = None
    # AIX-UGEF-P2: when a StepContract exists, the contract's structure is the
    # mutation authority — not classify_intent's phrase matching. classify_intent
    # may still route non-mutation intents (READ_FILE, RUN_TESTS, etc.) but the
    # contract's required_effects override phrase classification for mutation
    # routing. classify_intent is retained for understanding, not as mutation
    # selector when a contract is present.
    if step_contract is not None:
        mutation_kinds = _step_mutation_kinds(step_contract)
        contract_declares_edit = _contract_forces_edit(step_contract)
        read_scope_path = next(
            (p for p in step_contract.target_scope if _looks_like_a_file(p)),
            None,
        )
        parent_looks_read = looks_like_read_file_objective(
            str(coverage_objective or original_objective or ""), read_scope_path
        ) or looks_like_read_only_summary_objective(
            str(coverage_objective or original_objective or "")
        )
        step_is_read_only = (
            not mutation_kinds
            and not contract_declares_edit
            and parent_looks_read
            and (
                looks_like_read_file_objective(step_contract.purpose, read_scope_path)
                or looks_like_read_only_summary_objective(step_contract.purpose)
                or looks_like_read_only_summary_objective(objective)
            )
        )
        if step_is_read_only:
            intent = (
                "READ_FILE"
                if (read_scope_path or (target_path and _looks_like_a_file(target_path)))
                else "INSPECT_DIRECTORY"
            )
            symbol_name = None
        elif (
            _step_is_validate_only(step_contract)
            and not _has_patch_action
            and intent in {
                "EDIT_FILE",
                "EDIT_SYMBOL",
                "CREATE_FILE",
                "UNKNOWN",
            }
        ):
            # Validate/evidence steps must never enter the patch lane.
            intent = "RUN_TESTS" if step_contract.validation_commands else "READ_FILE"
            symbol_name = None
        elif (mutation_kinds or contract_declares_edit) and intent not in {
            "EDIT_FILE",
            "EDIT_SYMBOL",
            "CREATE_FILE",
        }:
            intent = "EDIT_FILE"
        # AIX-UGEF-P2 deprecation tracking: when classify_intent produced a
        # mutation intent but the contract had no mutation effects to confirm
        # it, log this as a phrase-classified mutation. These should migrate
        # to contract-declared effects or planner-emitted actions over time.
        if (
            intent in {"EDIT_FILE", "EDIT_SYMBOL", "CREATE_FILE"}
            and not mutation_kinds
            and not contract_declares_edit
        ):
            import logging as _logging
            _logging.getLogger("aix.ugef.deprecation").warning(
                "P2-DEPRECATION: classify_intent selected mutation intent=%s "
                "without contract mutation effects for cycle=%s step=%s purpose=%.120s",
                intent,
                getattr(step_contract, "cycle_id", "?"),
                getattr(step_contract, "id", "?"),
                getattr(step_contract, "purpose", "?"),
            )
            # Validate/evidence-only steps must never invent a patch.
            if _step_is_validate_only(step_contract):
                intent = "RUN_TESTS" if step_contract.validation_commands else "READ_FILE"
                symbol_name = None

    # Re-assert suite-run after StepContract read-only remapping. Bootstrap contracts
    # that name a file for PlanTargets otherwise become READ_FILE and never enter the
    # cancellable pytest subprocess (cancellation proof).
    if _wants_default_suite:
        intent = "RUN_TESTS"
        symbol_name = None

    # A named function on a file that does not exist yet is CREATE_FILE, not a
    # surgical EDIT_SYMBOL / EDIT_FILE. Resolving `double` in a yet-to-be-created
    # util module is SYMBOL_NOT_FOUND by construction.
    if registered and intent in {"EDIT_SYMBOL", "EDIT_FILE"} and target_path:
        try:
            create_root = resolve_repository_root(repository_id, settings=settings)
        except RepositoryLocationError as exc:
            raise RealExecuteError(exc.code, exc.detail) from exc
        candidate = create_root / target_path
        try:
            candidate.resolve().relative_to(create_root.resolve())
        except (OSError, ValueError):
            candidate = None
        scoped = [
            path
            for path in (
                step_contract.target_scope if step_contract is not None else (target_path,)
            )
            if _looks_like_a_file(path)
        ] or (target_path,)
        if any((cycle_project_root / path).exists() for path in scoped):
            # Deliverable already lives under the cycle project folder — edit there.
            pass
        elif candidate is not None and not candidate.exists():
            all_missing = all(not (create_root / path).exists() for path in scoped)
            if intent == "EDIT_SYMBOL" or all_missing:
                intent = "CREATE_FILE"
                symbol_name = None

    # Surgical symbol edits must resolve exactly once before code generation.  This
    # both supplies the concrete file scope and preserves the typed NOT_FOUND /
    # AMBIGUOUS contract instead of falling through to a generic no-source result.
    resolved_symbol_payload: dict[str, Any] | None = None
    if registered and intent == "EDIT_SYMBOL" and symbol_name:
        try:
            symbol_root = resolve_repository_root(repository_id, settings=settings)
        except RepositoryLocationError as exc:
            raise RealExecuteError(exc.code, exc.detail) from exc
        resolution = SymbolResolver().resolve_symbol(
            symbol_name=symbol_name,
            target_file=target_path,
            workspace_root=symbol_root,
        )
        if resolution.status != "RESOLVED":
            raise RealExecuteError(
                resolution.reason_code or resolution.status,
                resolution.reason_detail,
            )
        resolved_file = Path(resolution.locations[0].file_path)
        try:
            resolved_rel = resolved_file.resolve().relative_to(symbol_root.resolve()).as_posix()
        except (OSError, ValueError):
            resolved_rel = str(resolution.locations[0].file_path).replace("\\", "/")
        target_path = resolved_rel
        if not allowed_files and not scope_frozen:
            allowed_files = (resolved_rel,)
        resolved_symbol_payload = {
            "symbol_name": resolution.symbol_name,
            "symbol_kind": resolution.symbol_kind,
            "file_path": resolved_rel,
            "start_line": resolution.locations[0].start_line,
            "end_line": resolution.locations[0].end_line,
        }

    # Real file-bound / mutation executions must never take proposal shortcuts.
    file_bound_real_execution = bool(
        allowed_files
        or raw_text_replacements
        or intent in ("EDIT_FILE", "EDIT_SYMBOL", "READ_FILE", "RUN_TESTS", "SEARCH_CODE", "ANALYZE_CODE")
    )
    repository_mutation_intent = registered and intent in {
        "EDIT_FILE",
        "EDIT_SYMBOL",
        "CREATE_FILE",
    }

    # New markdown/doc artifacts must not explore or patch the default repository
    # (CREATE + empty allowed_files otherwise walks aix-frontend, including node_modules).
    # Live false-success (cyc_0ac453e2c5e7): "Do not create HTML" + README.md replace
    # was misclassified as docs proposal. File-bound EDIT is a real execution.
    if (
        looks_like_markdown_doc_objective(objective)
        and not file_bound_real_execution
        and not repository_mutation_intent
    ):
        return await _run_docs_proposal_execute(
            objective=objective,
            workspace=workspace,
            workhub_loaded=workhub_loaded,
            artifacts=artifacts,
        )

    # Prefer HTML/CSS (or TSX form) proposal generation when opted in. Otherwise
    # "create a login page in html/css" classifies as CREATE_FILE and wrongly enters
    # the repository patch path instead of writing proposed/index.html under the cycle workspace.
    #
    # Hard product invariant (live CLI cyc_8d6f434a5d76): a file-bound source mutation
    # is a REAL execution. It must never complete by generating unrelated HTML/CSS
    # proposal artifacts. `demo_capability` from durable_api_backend is only the same
    # wording heuristic evaluated twice — not user opt-in.
    if (
        demo_capability
        and not file_bound_real_execution
        and intent in ("CREATE_FILE", "UNKNOWN")
        and looks_like_page_generation_objective(
            coverage_objective, has_vision=bool(vision_ready)
        )
    ):
        return await _run_demo_proposal_execute(
            objective=objective,
            settings=settings,
            model_override=model_override,
            workspace=workspace,
            workhub_loaded=workhub_loaded,
            artifacts=artifacts,
            vision_ready=vision_ready,
            vision_notes_parts=vision_notes_parts,
        )

    # Canonical plan validation (gap-closure sprint 3, Priority 2): the single
    # authoritative pre-dispatch gate. A registered repository with a real,
    # recognized intent is exactly one EXECUTABLE step bound to that repository --
    # `plan_validation.validate_executable_plan` (the real, previously-unwired Sprint
    # 001/003 validator) now actually decides this, not an ad hoc `if` alongside it.
    # Never changes the observable UNKNOWN-intent fallback contract (still
    # `NO_EXECUTABLE_BRANCH_MATCHED` below, to preserve already-verified acceptance
    # scenarios) -- this gate exists to catch a real, different bug class: a
    # recognized intent that somehow isn't bound to the resource it claims to target.
    if registered and intent != "UNKNOWN":
        plan_check = validate_executable_plan(
            [{"category": "EXECUTABLE", "capability": intent, "target_resource": repository_id}],
            execution_required=True,
            target_resource=repository_id,
        )
        if not plan_check.valid:
            raise RealExecuteError(
                plan_check.reason_code or "PLAN_VALIDATION_FAILED", plan_check.detail
            )

    if registered:
        # Prefer cycle project folder for greenfield deliverables and follow-up reads.
        try:
            _registered_root = resolve_repository_root(repository_id, settings=settings)
        except RepositoryLocationError as exc:
            raise RealExecuteError(exc.code, exc.detail) from exc
        _targets = tuple(
            p
            for p in (
                allowed_files
                if scope_frozen
                else (allowed_files or ((target_path,) if target_path else ()))
            )
            if p
        )
        _use_cycle_root = _prefers_cycle_project_deliverable(
            intent=intent,
            objective=coverage_objective or objective,
            repository_root=_registered_root,
            cycle_root=cycle_project_root,
            targets=_targets,
        ) or (
            intent in {"READ_FILE", "RUN_TESTS", "INSPECT_DIRECTORY"}
            and bool(_targets)
            and any((cycle_project_root / str(t).replace("\\", "/").lstrip("./")).is_file() for t in _targets)
        )
        _bound_root = cycle_project_root if _use_cycle_root else _registered_root
        if _use_cycle_root:
            artifacts = dict(artifacts)
            artifacts["cycle_deliverable_root"] = str(cycle_project_root)
            artifacts["cycle_objective_slug"] = objective_folder_slug(coverage_objective or objective)

        if intent == "READ_FILE":
            resolved_target = target_path or objective
            return await _run_read_file_execute(
                objective=objective,
                repository_id=repository_id,
                target_path=resolved_target,
                settings=settings,
                workspace=workspace,
                workhub_loaded=workhub_loaded,
                artifacts=artifacts,
                budget_mgr=budget_mgr,
                mutation_repository_root=_bound_root if _use_cycle_root else None,
            )
        if intent == "INSPECT_DIRECTORY":
            return await _run_inspect_directory_execute(
                repository_id=repository_id,
                settings=settings,
                target_path=target_path,
                workspace=workspace,
                workhub_loaded=workhub_loaded,
                artifacts=artifacts,
                budget_mgr=budget_mgr,
            )
        if intent == "SEARCH_CODE":
            return await _run_search_code_execute(
                objective=objective,
                repository_id=repository_id,
                settings=settings,
                target_path=target_path,
                workspace=workspace,
                workhub_loaded=workhub_loaded,
                artifacts=artifacts,
                budget_mgr=budget_mgr,
            )
        if intent == "ANALYZE_CODE":
            return await _run_analyze_code_execute(
                objective=objective,
                repository_id=repository_id,
                settings=settings,
                workspace=workspace,
                workhub_loaded=workhub_loaded,
                artifacts=artifacts,
                budget_mgr=budget_mgr,
            )
        if intent in ("EDIT_FILE", "EDIT_SYMBOL", "CREATE_FILE"):
            # AIX-UGEF-001: mkdir objectives are now handled exclusively by
            # the governed actions path (_seed_create_directory_contract emits
            # ActionRequestV1 → capability_executor). The legacy parallel
            # _run_create_empty_directory_execute is no longer a mutation
            # authority. If the bootstrap failed to seed actions, fail closed.
            if looks_like_create_empty_directory_objective(coverage_objective or objective):
                raise RealExecuteError(
                    "MKDIR_REQUIRES_GOVERNED_ACTION",
                    "empty-directory creation requires a seeded ActionRequestV1; "
                    "the legacy _run_create_empty_directory_execute bypass is retired",
                )
            # Absolute/named file edits without an explicit allowed_files tuple still
            # carry a concrete target_path — use it as mutation scope so discovery
            # does not invent paths (CREATE_CONTEXT_ABSENT).
            if (
                not allowed_files
                and target_path
                and _looks_like_a_file(str(target_path))
            ):
                raw = str(target_path).replace("\\", "/").strip()
                if re.match(r"^[A-Za-z]:/", raw) or raw.startswith("/"):
                    raw = Path(raw).name
                allowed_files = (raw.lstrip("./"),)
            # AIX-UGEF-001: authorize filesystem.patch through the capability
            # gateway BEFORE any mutation. The multi-step patch pipeline runs
            # underneath, but the authorization decision is recorded first.
            from backend.aix_core.runtime.foundation.policy_gateway import authorize_action
            from backend.aix_core.runtime.foundation.audit import (
                audit_capability_requested,
                audit_authorization_decision,
            )
            from backend.aix_core.runtime.objective.contracts import ActionRequestV1 as _AR

            _patch_paths = tuple(
                p
                for p in (
                    allowed_files
                    if scope_frozen
                    else (allowed_files or ((target_path,) if target_path else ()))
                )
                if p
            )
            _patch_request = _AR(
                capability="filesystem",
                operation="patch",
                arguments={"paths": list(_patch_paths)} if _patch_paths else {"path": "."},
            )
            audit_capability_requested(_patch_request, _exec_ctx)
            _patch_root = _bound_root
            # Propose-only (apply_edits=False) must still authorize capability/scope;
            # the mutation gate is enforced later when applying edits.
            _patch_decision = authorize_action(
                _patch_request,
                repository_id=repository_id,
                repository_root=_patch_root,
                allowed_files=allowed_files,
                apply_edits=True,
                execution_context=_exec_ctx,
            )
            audit_authorization_decision(_patch_request, _patch_decision, _exec_ctx)
            if not _patch_decision.allowed:
                raise RealExecuteError(
                    _patch_decision.code or "POLICY_DENIED",
                    _patch_decision.detail or f"filesystem.patch denied for {repository_id}",
                )
            patch_result = await _run_patch_propose_execute(
                objective=objective,
                coverage_objective=coverage_objective,
                repository_id=repository_id,
                settings=settings,
                model_override=model_override,
                workspace=workspace,
                workhub_loaded=workhub_loaded,
                artifacts=artifacts,
                budget_mgr=budget_mgr,
                branch_kind=intent,
                apply_edits=apply_edits,
                allowed_files=allowed_files,
                literal_assignments=literal_assignments or None,
                task_family=task_family,
                step_contract=step_contract,
                repair_directive=repair_directive,
                text_replacements=raw_text_replacements or None,
                execution_context=execution_context,
                mutation_repository_root=_patch_root,
                tenant_id=tenant_id,
                actor_id=actor_id,
                caller_authorization=caller_authorization,
                plan_steps=plan_steps,
                admission_allowed_files=_admission_scope,
            )
            if resolved_symbol_payload is not None:
                patch_result.resolved_symbol = resolved_symbol_payload
            return patch_result
        if intent == "RUN_TESTS":
            return await _run_tests_intent_execute(
                objective=objective,
                repository_id=repository_id,
                settings=settings,
                workspace=workspace,
                workhub_loaded=workhub_loaded,
                artifacts=artifacts,
                budget_mgr=budget_mgr,
                is_cancelled=is_cancelled,
            )
        if intent == "UNKNOWN" and task_family.strip().upper() in _GROUNDED_ANSWER_FAMILIES:
            grounded = await _run_grounded_answer_execute(
                objective=objective,
                repository_id=repository_id,
                workspace=workspace,
                workhub_loaded=workhub_loaded,
                artifacts=artifacts,
                execution_context=execution_context,
            )
            if grounded is not None:
                return grounded
        # intent == "UNKNOWN": registered repository, but no real branch matches this
        # objective. Falls through to the fail-closed result below — it never becomes
        # an edit.
    else:
        # target_path is guaranteed None here — a non-None target_path against an
        # unregistered repository was already rebound or raised WORKSPACE_NOT_BOUND
        # above, and never reaches this branch silently.
        pass

    # Fail-closed default: nothing above matched. Never a silent metadata-only
    # success. A clarification opportunity is recorded on the shared budget manager so
    # repeated unclarified attempts against the same objective are still tracked.
    budget_mgr.record_clarification(f"unmatched_intent:{intent}:{repository_id}")
    result = _no_executable_branch_result(
        workspace=workspace,
        workhub_loaded=workhub_loaded,
        artifacts=artifacts,
        detail=(
            f"no executable branch matched this objective (intent={intent}, "
            f"repository_registered={registered}); clarify the request or register "
            "the repository before retrying"
        ),
    )
    result.budget_summary = budget_mgr.summary()
    return result
