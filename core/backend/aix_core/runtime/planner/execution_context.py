"""Per-objective execution memory for repository exploration tools.

The context is intentionally an in-memory/JSON-friendly value object.  Callers may
project :meth:`ExecutionContext.to_dict` into the existing cycle/evidence payload;
this module does not introduce a second persistence system.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from backend.aix_core.runtime.objective.ulid_compat import new_ulid


def _canonical_text(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _canonical_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").rstrip("/").casefold() or "."


def search_fingerprint(query: str, scope: str = ".") -> str:
    """Return a stable, payload-free identity for a bounded search."""
    raw = json.dumps(
        {"query": _canonical_text(query), "scope": _canonical_path(scope)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_fingerprint(path: str, file_hash: str = "") -> str:
    """Return a stable read identity; the hash distinguishes changed file versions."""
    raw = json.dumps(
        {"path": _canonical_path(path), "file_hash": str(file_hash or "").strip().casefold()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DirectoryRecord:
    path: str
    tool_call_id: str
    entry_count: int | None = None


@dataclass(frozen=True)
class FileReadRecord:
    path: str
    file_hash: str
    fingerprint: str
    tool_call_id: str
    bytes_read: int | None = None
    redaction_applied: bool = False


@dataclass(frozen=True)
class SearchRecord:
    query: str
    scope: str
    fingerprint: str
    tool_call_id: str
    hit_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SymbolRecord:
    name: str
    tool_call_id: str
    locations: tuple[str, ...] = ()


@dataclass(frozen=True)
class FingerprintCheck:
    should_issue: bool
    fingerprint: str
    prior_successes: int
    reason_code: str | None = None


class DuplicateToolCallError(RuntimeError):
    def __init__(self, reason_code: str, fingerprint: str) -> None:
        self.reason_code = reason_code
        self.fingerprint = fingerprint
        super().__init__(f"{reason_code}:{fingerprint}")


@dataclass
class ExecutionContext:
    """Execution memory whose lifetime is exactly one objective."""

    objective_id: str
    current_workspace: str | None = None
    execution_root: str | None = None
    current_branch: str | None = None
    repositories_explored: list[str] = field(default_factory=list)
    directories_listed: list[DirectoryRecord] = field(default_factory=list)
    files_read: list[FileReadRecord] = field(default_factory=list)
    files_modified: list[dict[str, Any]] = field(default_factory=list)
    symbols_resolved: list[SymbolRecord] = field(default_factory=list)
    searches_executed: list[SearchRecord] = field(default_factory=list)
    commands_executed: list[dict[str, Any]] = field(default_factory=list)
    tests_executed: list[dict[str, Any]] = field(default_factory=list)
    generated_patches: list[dict[str, Any]] = field(default_factory=list)
    understanding_complete: bool = False
    last_tool_call_id: str | None = None
    evidence_roots: tuple[str, ...] = ()
    search_fingerprint_counts: dict[str, int] = field(default_factory=dict)
    read_fingerprint_counts: dict[str, int] = field(default_factory=dict)
    duplicate_attempt_counts: dict[str, int] = field(default_factory=dict)

    def check_search(self, query: str, scope: str = ".") -> FingerprintCheck:
        fingerprint = search_fingerprint(query, scope)
        prior = self.search_fingerprint_counts.get(fingerprint, 0)
        return FingerprintCheck(prior == 0, fingerprint, prior, "REPEATED_IDENTICAL_SEARCH" if prior else None)

    def check_read(self, path: str, file_hash: str | None = None) -> FingerprintCheck:
        if file_hash is not None:
            fingerprint = read_fingerprint(path, file_hash)
            prior = self.read_fingerprint_counts.get(fingerprint, 0)
        else:
            canonical = _canonical_path(path)
            matching = [record for record in self.files_read if _canonical_path(record.path) == canonical]
            prior = len(matching)
            fingerprint = matching[-1].fingerprint if matching else read_fingerprint(path)
        return FingerprintCheck(prior == 0, fingerprint, prior, "REPEATED_IDENTICAL_READ" if prior else None)

    def note_duplicate_attempt(self, check: FingerprintCheck) -> None:
        self.duplicate_attempt_counts[check.fingerprint] = self.duplicate_attempt_counts.get(check.fingerprint, 0) + 1

    def invalidate_read(self, path: str) -> None:
        """Allow a caller with external invalidation evidence to re-read a path."""
        canonical = _canonical_path(path)
        retained = [record for record in self.files_read if _canonical_path(record.path) != canonical]
        removed = {record.fingerprint for record in self.files_read if _canonical_path(record.path) == canonical}
        self.files_read = retained
        for fingerprint in removed:
            self.read_fingerprint_counts.pop(fingerprint, None)

    def mark_understanding_complete(self) -> None:
        self.understanding_complete = True

    def record_tool_success(
        self,
        tool_name: str,
        *,
        arguments: Mapping[str, Any] | None,
        result: Mapping[str, Any] | Any,
        tool_call_id: str,
    ) -> None:
        """Atomically fold a successful tool result into the objective context.

        Raw file content and search match text are never retained.  Failed tools must
        not call this method; :class:`ContextToolExecutor` guarantees that ordering.
        """
        args = dict(arguments or {})
        payload = _mapping(result)
        normalized = (
            _canonical_text(tool_name)
            .replace("tool.", "")
            .replace("filesystem.", "")
            .replace("repository.", "")
        )

        if normalized in {"list", "list_directory", "directory_list"}:
            path = _path_from(args, payload, default=".")
            entries = payload.get("entries")
            count = len(entries) if isinstance(entries, list) else _optional_int(payload.get("entry_count"))
            self.directories_listed.append(DirectoryRecord(path=path, tool_call_id=tool_call_id, entry_count=count))
            self._add_repository(path)
        elif normalized in {"exists", "stat"}:
            path = _path_from(args, payload, default=".")
            self.directories_listed.append(DirectoryRecord(path=path, tool_call_id=tool_call_id))
            self._add_repository(path)
        elif normalized in {"search", "search_files"}:
            query = str(args.get("query") or args.get("pattern") or payload.get("query") or "")
            scope = str(args.get("scope") or args.get("relative_path") or payload.get("scope") or ".")
            fingerprint = search_fingerprint(query, scope)
            self.search_fingerprint_counts[fingerprint] = self.search_fingerprint_counts.get(fingerprint, 0) + 1
            self.searches_executed.append(
                SearchRecord(
                    query=query,
                    scope=scope,
                    fingerprint=fingerprint,
                    tool_call_id=tool_call_id,
                    hit_summary=_safe_hit_summary(payload),
                )
            )
        elif normalized in {"read", "read_file", "file_read"}:
            path = _path_from(args, payload)
            if not self._is_evidence_path(path):
                file_hash = _safe_result_hash(payload)
                fingerprint = read_fingerprint(path, file_hash)
                self.read_fingerprint_counts[fingerprint] = self.read_fingerprint_counts.get(fingerprint, 0) + 1
                self.files_read.append(
                    FileReadRecord(
                        path=path,
                        file_hash=file_hash,
                        fingerprint=fingerprint,
                        tool_call_id=tool_call_id,
                        bytes_read=_optional_int(payload.get("bytes_read", payload.get("size_bytes"))),
                        redaction_applied=bool(
                            payload.get("redaction_applied") or payload.get("values_redacted")
                        ),
                    )
                )
        elif normalized in {"resolve_symbol", "symbol_resolve"}:
            name = str(args.get("symbol_name") or args.get("name") or payload.get("symbol_name") or "")
            self.symbols_resolved.append(
                SymbolRecord(name=name, tool_call_id=tool_call_id, locations=_symbol_locations(payload))
            )
        elif normalized in {"status", "repository_status", "git_status"}:
            self.current_branch = str(payload.get("branch") or payload.get("current_branch") or "") or self.current_branch
            workspace = str(payload.get("workspace") or payload.get("execution_root") or "")
            if workspace:
                self.current_workspace = workspace
                self.execution_root = workspace
            self._add_repository(workspace or self.execution_root or self.current_workspace or ".")

        self.last_tool_call_id = tool_call_id

    def _add_repository(self, path: str) -> None:
        repository = self.execution_root or self.current_workspace or path
        if repository and _canonical_path(repository) not in {_canonical_path(item) for item in self.repositories_explored}:
            self.repositories_explored.append(repository)

    def _is_evidence_path(self, path: str) -> bool:
        candidate = _canonical_path(path)
        if self.execution_root and not Path(path).is_absolute():
            candidate = _canonical_path(Path(self.execution_root) / path)
        return any(candidate == _canonical_path(root) or candidate.startswith(f"{_canonical_path(root)}/") for root in self.evidence_roots)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextToolExecutor:
    """Small adapter that deduplicates read/search calls and updates context on success."""

    context: ExecutionContext
    tool_call_id_factory: Callable[[], str] = field(default=lambda: f"toolcall_{new_ulid()}")

    def call(
        self,
        tool_name: str,
        operation: Callable[..., Mapping[str, Any]],
        *,
        arguments: Mapping[str, Any] | None = None,
        force: bool = False,
        tool_call_id: str | None = None,
    ) -> Mapping[str, Any]:
        args = dict(arguments or {})
        normalized = _canonical_text(tool_name)
        check: FingerprintCheck | None = None
        if normalized.endswith("search") or normalized.endswith("search_files"):
            check = self.context.check_search(
                str(args.get("query") or args.get("pattern") or ""),
                str(args.get("scope") or args.get("relative_path") or "."),
            )
        elif normalized.endswith("read") or normalized.endswith("read_file") or normalized.endswith("file_read"):
            check = self.context.check_read(str(args.get("path") or args.get("relative_path") or ""))
        if check is not None and not check.should_issue and not force:
            self.context.note_duplicate_attempt(check)
            raise DuplicateToolCallError(check.reason_code or "REPEATED_IDENTICAL_TOOL_CALL", check.fingerprint)

        result = operation(**args)
        call_id = tool_call_id or self.tool_call_id_factory()
        self.context.record_tool_success(
            tool_name,
            arguments=args,
            result=result,
            tool_call_id=call_id,
        )
        return result


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="python"))
    raise TypeError("tool result must be a mapping, dataclass, or model_dump-compatible object")


def _path_from(arguments: Mapping[str, Any], result: Mapping[str, Any], default: str = "") -> str:
    return str(
        arguments.get("path")
        or arguments.get("relative_path")
        or result.get("path")
        or result.get("relative_path")
        or result.get("resource")
        or default
    )


def _safe_result_hash(payload: Mapping[str, Any]) -> str:
    explicit = payload.get("file_hash") or payload.get("content_hash") or payload.get("sha256")
    if explicit:
        return str(explicit)
    if "content" in payload:
        content = payload.get("content")
        raw = content if isinstance(content, bytes) else str(content or "").encode("utf-8")
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
    safe = {key: value for key, value in payload.items() if key not in {"content", "value", "raw"}}
    encoded = json.dumps(safe, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_hit_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    hits = payload.get("hits")
    if not isinstance(hits, list):
        return {"hit_count": _optional_int(payload.get("hit_count")) or 0}
    paths: list[str] = []
    for hit in hits[:20]:
        if isinstance(hit, Mapping):
            path = hit.get("path") or hit.get("file") or hit.get("relative_path")
            if path:
                paths.append(str(path))
    return {"hit_count": len(hits), "paths": paths}


def _symbol_locations(payload: Mapping[str, Any]) -> tuple[str, ...]:
    locations = payload.get("locations")
    if not isinstance(locations, list):
        return ()
    safe: list[str] = []
    for location in locations:
        if isinstance(location, Mapping):
            path = location.get("file_path") or location.get("path")
            line = location.get("start_line") or location.get("line")
            if path:
                safe.append(f"{path}:{line}" if line else str(path))
        elif isinstance(location, str):
            safe.append(location)
    return tuple(safe)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "ContextToolExecutor",
    "DirectoryRecord",
    "DuplicateToolCallError",
    "ExecutionContext",
    "FileReadRecord",
    "FingerprintCheck",
    "SearchRecord",
    "SymbolRecord",
    "read_fingerprint",
    "search_fingerprint",
]
