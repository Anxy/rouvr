"""Mandatory repository discovery: real inspection before any patch is proposed.

Every finding here comes from a real `RepositoryToolbox` call (`list_directory`,
`read_file`, `search_files`) — each of which already emits its own real `tool.*` event.
This module exists so the executor can never legitimately report a workspace as
inspected without having actually inspected it: there is no code path that produces a
`DiscoveryReport` without having made the underlying real calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolbox, RepositoryToolError

_LARGE_MUTATION_FILE_MAX_BYTES = 16_777_216

_MANIFEST_CANDIDATES = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "Pipfile",
)

_SRC_DIR_CANDIDATES = ("src", "backend", "app", "lib")

_CONFIG_NAME_KEYWORDS = ("config", "settings", "env")

_TEST_DIR_CANDIDATES = ("tests", "test", "__tests__")

_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "into",
        "file",
        "files",
        "please",
        "add",
        "append",
        "update",
        "change",
        "modify",
        "create",
        "remove",
        "delete",
        "without",
        "other",
        "keys",
        "under",
        "comment",
        "replace",
        "rename",
        "rewrite",
        "patch",
        "edit",
        "implement",
        "integrate",
        "configure",
        "enable",
        "all",
        "occurrences",
    }
)

_MAX_WALK_NODES = 200


@dataclass
class DiscoveryReport:
    manifests: dict[str, str] = field(default_factory=dict)
    src_tree: dict[str, list[str]] = field(default_factory=dict)
    config_files: dict[str, str] = field(default_factory=dict)
    env_example: str | None = None
    env_example_path: str | None = None
    relevant_module_hits: list[dict[str, Any]] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    keywords: tuple[str, ...] = ()

    @property
    def files_read(self) -> int:
        return len(self.manifests) + len(self.config_files) + (1 if self.env_example is not None else 0)


def _objective_keywords(objective: str) -> tuple[str, ...]:
    """Derive search tokens from the objective text (no fixed domain word list)."""
    import re

    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", str(objective or ""))
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        lowered = token.lower()
        # Preserve the compound identifier and also search its semantic parts.
        # `SMTP_PASSWORD` must locate code mentioning `SMTP`, not require the exact
        # compound token to appear everywhere.
        variants = (lowered, *(part for part in lowered.split("_") if len(part) >= 3))
        for variant in variants:
            if variant in _STOPWORDS or variant in seen:
                continue
            seen.add(variant)
            out.append(variant)
            if len(out) >= 12:
                return tuple(out)
    return tuple(out)


def _walk_bounded(toolbox: RepositoryToolbox, start: str, *, max_depth: int) -> dict[str, list[str]]:
    tree: dict[str, list[str]] = {}
    queue: list[tuple[str, int]] = [(start, 0)]
    visited: set[str] = set()
    while queue and len(tree) < _MAX_WALK_NODES:
        path, level = queue.pop(0)
        if path in visited:
            continue
        visited.add(path)
        try:
            listing = toolbox.list_directory(path)
        except (RepositoryToolError, FileNotFoundError, NotADirectoryError):
            continue
        tree[path] = [entry["name"] for entry in listing["entries"]]
        if level >= max_depth:
            continue
        for entry in listing["entries"]:
            if entry["kind"] == "dir" and not entry["name"].startswith("."):
                child = f"{path}/{entry['name']}" if path != "." else entry["name"]
                queue.append((child, level + 1))
    return tree


def run_repository_discovery(*, toolbox: RepositoryToolbox, objective: str) -> DiscoveryReport:
    """Run every discovery step for real against `toolbox.repository_root` and return
    the accumulated findings. Never returns a "ready" report without having actually
    called `list_directory`/`read_file`/`search_files` at least once."""
    toolbox.on_event("discovery.started", {"objective_preview": objective[:200]})
    report = DiscoveryReport(keywords=_objective_keywords(objective))

    # 1. Build manifest.
    for name in _MANIFEST_CANDIDATES:
        try:
            result = toolbox.read_file(name)
        except (RepositoryToolError, FileNotFoundError, IsADirectoryError):
            continue
        report.manifests[name] = result["content"][:4000]

    # 2. src/backend/app structure, bounded depth.
    try:
        top = toolbox.list_directory(".")
    except RepositoryToolError:
        top = {"entries": []}
    top_dir_names = [entry["name"] for entry in top["entries"] if entry["kind"] == "dir"]
    roots_to_scan = [d for d in top_dir_names if d in _SRC_DIR_CANDIDATES]
    if not roots_to_scan:
        roots_to_scan = [d for d in top_dir_names if not d.startswith(".")][:5]
    for root_name in roots_to_scan:
        report.src_tree.update(_walk_bounded(toolbox, root_name, max_depth=2))

    # 3. .env.example at root — a dotfile, so root must be listed with hidden entries on.
    try:
        root_listing = toolbox.list_directory(".", include_hidden=True)
    except RepositoryToolError:
        root_listing = {"entries": []}
    for entry in root_listing["entries"]:
        if entry["kind"] == "file" and entry["name"] == ".env.example":
            try:
                content = toolbox.read_file(entry["name"])
            except RepositoryToolError:
                continue
            report.env_example = content["content"]
            report.env_example_path = entry["name"]
            break

    # 4. Config/env-validation files — scanned across every directory discovery already
    #    walked (root + every nested dir found in step 2), not just one level deep, so a
    #    config module living at e.g. `backend/hyeve_integration_service/config.py` is
    #    still found.
    candidate_dirs = [".", *report.src_tree.keys()]
    for candidate_dir in candidate_dirs:
        try:
            listing = toolbox.list_directory(candidate_dir)
        except (RepositoryToolError, FileNotFoundError, NotADirectoryError):
            continue
        for entry in listing["entries"]:
            if entry["kind"] != "file":
                continue
            rel = entry["name"] if candidate_dir == "." else f"{candidate_dir}/{entry['name']}"
            name_lower = entry["name"].lower()
            if not any(keyword in name_lower for keyword in _CONFIG_NAME_KEYWORDS):
                continue
            if not name_lower.endswith((".py", ".ts", ".js", ".json")):
                continue
            try:
                content = toolbox.read_file(rel)
            except RepositoryToolError:
                continue
            report.config_files[rel] = content["content"][:4000]

    # 4. Existing mail/notification/integration modules relevant to the objective.
    for keyword in report.keywords:
        try:
            hits = toolbox.search_files(keyword)
        except RepositoryToolError:
            continue
        if hits["hits"]:
            report.relevant_module_hits.append({"keyword": keyword, "hits": hits["hits"][:20]})

    # 5. Existing tests.
    for test_dir in _TEST_DIR_CANDIDATES:
        try:
            listing = toolbox.list_directory(test_dir)
        except (RepositoryToolError, FileNotFoundError, NotADirectoryError):
            continue
        report.test_files.extend(entry["name"] for entry in listing["entries"] if entry["kind"] == "file")

    toolbox.on_event(
        "discovery.completed",
        {
            "manifests_found": len(report.manifests),
            "config_files_found": len(report.config_files),
            "env_example_found": report.env_example is not None,
            "relevant_hits": len(report.relevant_module_hits),
            "test_files_found": len(report.test_files),
        },
    )
    return report


def run_focused_file_discovery(
    *,
    toolbox: RepositoryToolbox,
    target_path: str,
    objective: str = "",
) -> DiscoveryReport:
    """Minimal discovery for an already-bound single-file edit (e.g. append to `.env`).

    Full `run_repository_discovery` walks/search/reads dozens of files and routinely
    trips `EXPLORATION_BUDGET_EXCEEDED` before the patch is even proposed. When the
    human already named a concrete file in `allowed_files` / the objective, only that
    file (plus a root listing) needs to be inspected.
    """
    from backend.aix_core.runtime.executor.read_file_execute import normalize_repo_relative_path

    relative = normalize_repo_relative_path(target_path)
    if not relative:
        raise RepositoryToolError("TARGET_PATH_REQUIRED", "focused discovery needs a concrete relative path")

    toolbox.on_event(
        "discovery.started",
        {"mode": "focused_file", "target_path": relative, "objective_preview": (objective or "")[:200]},
    )
    report = DiscoveryReport(keywords=_objective_keywords(objective or ""))

    try:
        toolbox.list_directory(".", include_hidden=True)
    except RepositoryToolError:
        pass

    try:
        content = toolbox.read_file(relative, max_bytes=_LARGE_MUTATION_FILE_MAX_BYTES)
    except (RepositoryToolError, FileNotFoundError, IsADirectoryError) as exc:
        # CREATE_FILE against a missing path is still valid — leave an empty placeholder
        # so codegen sees the bound path (not an empty discovery → NO_APPLICABLE_SOURCE_FILE).
        toolbox.on_event(
            "discovery.focused_read_miss",
            {"target_path": relative, "detail": str(exc)[:300]},
        )
        report.config_files[relative] = ""
    else:
        # Focused path is the sole mutation target — keep full content so large-file
        # multi-section replacements are not silently truncated mid-file.
        text = str(content.get("content") or "")
        report.config_files[relative] = text
        if relative.endswith(".env.example") or relative == ".env.example":
            report.env_example = text
            report.env_example_path = relative

    for name in _MANIFEST_CANDIDATES:
        try:
            result = toolbox.read_file(name)
        except (RepositoryToolError, FileNotFoundError, IsADirectoryError):
            continue
        report.manifests[name] = result["content"][:4000]

    toolbox.on_event(
        "discovery.completed",
        {
            "mode": "focused_file",
            "target_path": relative,
            "manifests_found": len(report.manifests),
            "config_files_found": len(report.config_files),
        },
    )
    return report


def run_bound_files_discovery(
    *,
    toolbox: RepositoryToolbox,
    target_paths: list[str] | tuple[str, ...],
    objective: str = "",
) -> DiscoveryReport:
    """Focused discovery for a small, admit-time bound file set (multi-file edits).

    Same budget trap as single-file: full `run_repository_discovery` burns searches
    before any patch. When the human already named concrete files in `allowed_files`,
    only those paths (plus root listing / manifests once) are inspected.
    """
    from backend.aix_core.runtime.executor.read_file_execute import normalize_repo_relative_path

    paths = [
        normalize_repo_relative_path(str(p))
        for p in target_paths
        if str(p).strip()
    ]
    paths = [p for p in paths if p]
    if not paths:
        raise RepositoryToolError("TARGET_PATH_REQUIRED", "bound discovery needs at least one path")
    if len(paths) == 1:
        return run_focused_file_discovery(
            toolbox=toolbox, target_path=paths[0], objective=objective
        )

    toolbox.on_event(
        "discovery.started",
        {
            "mode": "bound_files",
            "target_paths": paths[:8],
            "objective_preview": (objective or "")[:200],
        },
    )
    report = DiscoveryReport(keywords=_objective_keywords(objective or ""))
    try:
        toolbox.list_directory(".", include_hidden=True)
    except RepositoryToolError:
        pass

    for relative in paths[:8]:
        try:
            content = toolbox.read_file(relative, max_bytes=_LARGE_MUTATION_FILE_MAX_BYTES)
        except (RepositoryToolError, FileNotFoundError, IsADirectoryError) as exc:
            toolbox.on_event(
                "discovery.focused_read_miss",
                {"target_path": relative, "detail": str(exc)[:300]},
            )
            # Create-target placeholder so multi-file create objectives still have paths.
            if relative not in report.config_files:
                report.config_files[relative] = ""
            continue
        report.config_files[relative] = str(content.get("content") or "")

    for name in _MANIFEST_CANDIDATES:
        try:
            result = toolbox.read_file(name)
        except (RepositoryToolError, FileNotFoundError, IsADirectoryError):
            continue
        report.manifests[name] = result["content"][:4000]

    toolbox.on_event(
        "discovery.completed",
        {
            "mode": "bound_files",
            "target_count": len(paths),
            "manifests_found": len(report.manifests),
            "config_files_found": len(report.config_files),
        },
    )
    return report


__all__ = [
    "DiscoveryReport",
    "run_repository_discovery",
    "run_focused_file_discovery",
    "run_bound_files_discovery",
]
