"""Real read-only file executor.

Closes the exact gap the "AIX Real Execution Sprint" problem statement names: an
objective such as ``D:\\...\\service\\.env read this file`` must resolve to a real
``filesystem.read_file`` call against the approved target repository, never to a
metadata-only ``bounded_propose_artifact`` plan.

Scope is deliberately narrow (read-only, one file, inside an already-approved
repository root — see `repository_location.py`). Never resolves a path outside that
root, never returns a result claiming a file was read without having actually called
`RepositoryToolbox.read_file`, and never places a raw secret value (env or otherwise
sensitive) into the returned evidence — only key names, presence and a coarse inferred
type. This is intentionally stricter than `secret_boundary.redact_objective_text`
(which only catches a fixed keyword regex): for `.env`-shaped files every key
classified as sensitive is withheld by name-based classification, not by pattern
matching on the value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.aix_core.intelligence.repository.repository_tools import RepositoryToolbox, RepositoryToolError

_READ_INTENT_PHRASES = (
    "read this file",
    "read the file",
    "read this",
    "please read",
    "show me the contents",
    "show the contents",
    "what's in",
    "whats in",
    "what is in",
    "inspect this file",
    "view this file",
    "display this file",
    "summarize this file",
    "summarise this file",
    "check the file",
    "check this file",
    "can you check",
    "could you check",
    "look at the file",
    "look at this file",
    "can you look at",
    "take a look at",
    "look into",
)

# An edit verb anywhere in the objective disqualifies read-intent classification —
# a read request never also asks for a change, so ambiguity here must fail closed
# toward the existing edit/demo paths rather than silently downgrading a real edit
# request to a read.
_EDIT_INTENT_PHRASES = (
    "add ",
    "append ",
    "update ",
    "change ",
    "modify ",
    "fix ",
    "implement ",
    "create ",
    "delete ",
    "remove ",
    "patch ",
    "edit ",
    "rewrite ",
    "replace ",
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

# Prohibition / invariant prose ("do not modify the README") must not disqualify a
# read-only step or be mistaken for an actionable edit verb (AIX-GAEF-001 Test A).
_NEGATED_EDIT_VERB_RE = re.compile(
    r"\b(?:do\s+not|don't|doesn't|never|must\s+not|without|shall\s+not)\s+(?:\w+\s+){0,4}",
    re.IGNORECASE,
)


def _scrub_negated_edit_prohibitions(text: str) -> str:
    """Remove negated edit clauses so governance invariants do not masquerade as edits."""
    return _NEGATED_EDIT_VERB_RE.sub(" ", text or "")


# Deliberately checked before any URL-style parsing would apply: `D:\...`/`D:/...`
# must never be treated as a URL scheme, and a quoted path must keep internal spaces.
_QUOTED_PATH_RE = re.compile(r"\"([^\"]+)\"|'([^']+)'")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"']+")
# Lookbehind keeps this from matching a mid-word `/` in ordinary prose ("read/write",
# "and/or") once the bare-relative-path check below has already rejected it as prose --
# a genuine POSIX path's leading `/`/`./`/`../` is never itself preceded by a letter.
_POSIX_PATH_RE = re.compile(r"(?<![\w.\-])(?:\.{1,2}/|/)[^\s\"']+")

# Bare relative / service-relative path tokens: no drive letter, no leading `./`, `../`
# or `/` -- e.g. `hyeve-integration-service\.env` or `hyeve\services\hyeve-integration-
# service`. Ungoverned by the two regexes above, which both require an anchor
# (`X:` or a leading separator) the token doesn't have. Matches any run of 2+
# `\`/`/`-delimited segments; `_looks_like_bare_relative_path` below then filters out
# ordinary English slash idioms ("read/write", "and/or") so this stays a path
# extractor, not a prose-scanning heuristic.
_BARE_RELATIVE_PATH_RE = re.compile(
    r"(?<![\\/\w])[A-Za-z0-9_.][A-Za-z0-9_.\-]*(?:[\\/][A-Za-z0-9_.][A-Za-z0-9_.\-]*)+"
)
_NON_PATH_SLASH_IDIOMS = frozenset(
    {
        "and/or", "on/off", "yes/no", "read/write", "pass/fail", "before/after",
        "true/false", "up/down", "in/out", "his/her", "he/she",
    }
)


def _looks_like_bare_relative_path(candidate: str) -> bool:
    """Reject prose that merely contains a slash (`and/or`) while accepting genuine
    multi-segment relative paths, without hardcoding path shapes -- either the token
    has 3+ segments (very unlikely in ordinary prose) or its last segment reads as a
    filename (has an extension, or is a dotfile like `.env`)."""
    if candidate.casefold() in _NON_PATH_SLASH_IDIOMS:
        return False
    segments = [s for s in re.split(r"[\\/]", candidate) if s]
    if len(segments) < 2:
        return False
    last = segments[-1]
    looks_like_filename = bool(re.match(r"^\.[A-Za-z0-9][A-Za-z0-9_.\-]*$", last)) or bool(
        re.match(r"^[A-Za-z0-9_\-]+\.[A-Za-z0-9]{1,10}$", last)
    )
    return len(segments) >= 3 or looks_like_filename

_SENSITIVE_KEY_SUBSTRINGS = (
    "PASSWORD",
    "PASS",
    "SECRET",
    "TOKEN",
    "API_KEY",
    "PRIVATE_KEY",
    "CLIENT_SECRET",
    "ACCESS_KEY",
    "AUTH",
    "CREDENTIAL",
    "CONNECTION_STRING",
    "DATABASE_URL",
)

_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class ReadFileError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


def _looks_like_a_file(path: str) -> bool:
    """True for a concrete file name (`.env`, `config.py`), false for a bare directory
    path with no filename component (`D:\\services\\hyeve-integration-service`)."""
    name = Path(path).name
    if not name:
        return False
    if name.startswith(".") and name.count(".") == 1:
        return True  # dotfile such as `.env` — pathlib reports no suffix for these
    return bool(Path(path).suffix)


def looks_like_read_file_objective(objective: str, target_path: str | None = None) -> bool:
    """Read-intent classifier: no edit verb anywhere in the text, plus either an
    explicit read phrase ("read this file", "check the .env file", ...) or — when no
    such phrase is present — a concrete file path with no edit verb, which covers
    real-world phrasing this fixed phrase list will never fully enumerate ("what does
    the .env say", "D:\\...\\.env please"). A bare directory path never counts on its
    own — that is `INSPECT_DIRECTORY`, a different, not-yet-built intent; explicit
    phrases still apply to a directory since the sprint's own contract only rejects the
    *default* path-shape assumption, not a user who unambiguously asked to read one."""
    lowered = objective.lower()
    scrubbed = _scrub_negated_edit_prohibitions(lowered)
    if any(phrase in scrubbed for phrase in _EDIT_INTENT_PHRASES):
        return False
    if any(phrase in lowered for phrase in _READ_INTENT_PHRASES):
        return True
    return bool(target_path and _looks_like_a_file(target_path))


def _looks_like_quoted_path_candidate(candidate: str) -> bool:
    """Quoted spans are often prose (``\"# SMTP ...\"``), not files. Accept only
    candidates that look like a real path or concrete filename."""
    text = str(candidate or "").strip()
    if not text or text.startswith("#"):
        return False
    if "\\" in text or "/" in text:
        return True
    return _looks_like_a_file(text)


def normalize_repo_relative_path(path: str) -> str:
    """Normalize a repo-relative path without destroying leading-dot filenames.

    ``\".env\".lstrip(\"./\")`` incorrectly yields ``\"env\"`` because ``str.lstrip``
    treats its argument as a *set* of characters. Only strip an optional ``./``
    prefix (repeatedly), never individual leading dots.
    """
    relative = str(path or "").strip().replace("\\", "/")
    while relative.startswith("./"):
        relative = relative[2:]
    return relative.lstrip("/")


def sanitize_allowed_file_entry(path: str) -> str:
    """Strip planner prose from allowed_files entries (e.g. ``.env (inspection only)``)."""
    text = str(path or "").strip().strip("\"'")
    if not text:
        return ""
    if re.fullmatch(r"(?i)(?:.*[/\\])?\.env(?:\s*\([^)]*\))?", text):
        return ".env"
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
    if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("/"):
        name = Path(text.replace("\\", "/")).name
        return normalize_repo_relative_path(name)
    if " " in text:
        # Refuse space-containing leftovers that are not a known special file.
        return ""
    return normalize_repo_relative_path(text)


def extract_target_path(objective: str) -> str | None:
    """Extract a literal file path from objective text. Quoted paths (with internal
    spaces) win first; then an absolute Windows-drive path; then a bare relative or
    service-relative path (`hyeve-integration-service\\.env`, `hyeve\\services\\hyeve-
    integration-service`) filtered against ordinary prose; then a POSIX-style path
    token as a final fallback."""
    quoted = _QUOTED_PATH_RE.search(objective)
    if quoted:
        candidate = (quoted.group(1) or quoted.group(2) or "").strip()
        if candidate and _looks_like_quoted_path_candidate(candidate):
            return candidate
    windows_match = _WINDOWS_PATH_RE.search(objective)
    if windows_match:
        return windows_match.group(0).rstrip(".,;:")
    for bare_match in _BARE_RELATIVE_PATH_RE.finditer(objective):
        candidate = bare_match.group(0).rstrip(".,;:")
        if _looks_like_bare_relative_path(candidate):
            return candidate
    posix_match = _POSIX_PATH_RE.search(objective)
    if posix_match:
        return posix_match.group(0).rstrip(".,;:")
    return None


def resolve_relative_path(target_path: str, repository_root: Path) -> str | None:
    """Return `target_path` expressed relative to `repository_root`, or `None` when it
    does not resolve inside the root. Fail closed — never guess, never clip."""
    root = Path(repository_root).resolve()
    candidate = Path(target_path)
    base = candidate if candidate.is_absolute() else (root / candidate)
    try:
        resolved = base.resolve()
        return str(resolved.relative_to(root)).replace("\\", "/")
    except ValueError:
        return None


_NAMED_FILE_TOKEN_RE = re.compile(
    r"(?<![\w/.-])([\w-]+(?:/[\w-]+)*\.(?:py|php|ts|tsx|js|jsx|json|yaml|yml|md|toml|ini|cfg|env))(?![\w])"
)

_EXISTENCE_ASSERTED_RE = re.compile(
    r"\bexisting\b|\bin (?:the|this|that) file\b|\bmodify\b|\bfix\b|\bedit\b|\bupdate\b",
    re.IGNORECASE,
)
_CREATE_SHAPED_RE = re.compile(
    r"\bcreate\b|\bnew file\b|\badd a new\b|\bgenerate\b|\bscaffold\b",
    re.IGNORECASE,
)


def is_test_like_path(relative_path: str) -> bool:
    """True for test-suite paths (tests/ dirs or test_*.py/*_test.py files)."""
    norm = str(relative_path or "").replace("\\", "/").strip().lstrip("/")
    if not norm:
        return False
    if norm == "tests" or norm.startswith("tests/"):
        return True
    base = norm.rsplit("/", 1)[-1]
    return base.startswith("test_") or base.endswith("_test.py")


def objective_asserts_existing_file(objective: str) -> bool:
    """True when the objective talks about changing something that already
    exists (not a create-shaped ask where absence is expected)."""
    text = str(objective or "")
    return bool(_EXISTENCE_ASSERTED_RE.search(text)) and not bool(
        _CREATE_SHAPED_RE.search(text)
    )


def split_named_objective_files(
    objective: str,
    repository_root: Path,
    *,
    limit: int = 8,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(existing, missing): repo files explicitly named in objective prose.

    Generic planner-grounding helper: scans extension-bearing file tokens
    (prose shapes like ``In the existing file ship.py`` that the anchored
    path extractor misses), resolves them inside ``repository_root``.
    ``existing`` lists real files with non-test sources first, then named
    tests, then others; ``missing`` lists tokens resolving to nothing.
    Never invents paths; bounded output.
    """
    existing: list[str] = []
    missing: list[str] = []
    try:
        text = str(objective or "")
        if not text:
            return (), ()
        root = Path(str(repository_root))
        seen: set[str] = set()
        for token in sorted(set(_NAMED_FILE_TOKEN_RE.findall(text)), key=len, reverse=True):
            if token in seen:
                continue
            seen.add(token)
            try:
                rel = resolve_relative_path(token, root)
            except (OSError, ValueError):
                continue
            if not rel:
                if token not in missing:
                    missing.append(token)
                continue
            try:
                is_file = (root / rel).is_file()
            except OSError:
                continue
            if is_file:
                if rel not in existing:
                    existing.append(rel)
            elif token not in missing:
                missing.append(token)
            if len(existing) + len(missing) >= max(1, int(limit)):
                break
        ordered = (
            [p for p in existing if not is_test_like_path(p)]
            + [p for p in existing if is_test_like_path(p)]
        )
        return tuple(ordered), tuple(missing)
    except (OSError, ValueError, AttributeError, TypeError):
        return (), ()


def is_env_like_path(relative_path: str) -> bool:
    name = Path(relative_path).name
    return name == ".env" or name.startswith(".env.")


def is_sensitive_key(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _SENSITIVE_KEY_SUBSTRINGS)


def _infer_value_type(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return "empty"
    if stripped.lower() in ("true", "false"):
        return "boolean"
    if stripped.isdigit():
        return "numeric"
    if "://" in stripped:
        return "url"
    return "string"


@dataclass
class EnvKeySummary:
    name: str
    present: bool
    sensitive: bool
    value_type: str | None  # only set for non-sensitive keys


@dataclass
class EnvFileSummary:
    keys: list[EnvKeySummary] = field(default_factory=list)
    duplicate_keys: list[str] = field(default_factory=list)
    malformed_lines: list[int] = field(default_factory=list)
    line_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "keys": [
                {"name": k.name, "present": k.present, "sensitive": k.sensitive, "value_type": k.value_type}
                for k in self.keys
            ],
            "duplicate_keys": self.duplicate_keys,
            "malformed_lines": self.malformed_lines,
            "line_count": self.line_count,
        }


def summarize_env_content(content: str) -> EnvFileSummary:
    """Parse `KEY=VALUE` lines without ever retaining a sensitive raw value. Duplicate
    keys and malformed lines are recorded, not silently dropped or guessed at."""
    keys: list[EnvKeySummary] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    malformed: list[int] = []
    lines = content.splitlines()
    for idx, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE_RE.match(line)
        if not match:
            malformed.append(idx)
            continue
        name, value = match.group(1), match.group(2)
        if name in seen:
            if name not in duplicates:
                duplicates.append(name)
            continue
        seen.add(name)
        sensitive = is_sensitive_key(name)
        keys.append(
            EnvKeySummary(
                name=name,
                present=bool(value.strip()),
                sensitive=sensitive,
                value_type=None if sensitive else _infer_value_type(value),
            )
        )
    return EnvFileSummary(keys=keys, duplicate_keys=duplicates, malformed_lines=malformed, line_count=len(lines))


@dataclass
class ReadFileExecutionResult:
    relative_path: str
    bytes_read: int
    line_count: int
    truncated: bool
    env_summary: EnvFileSummary | None
    preview: str | None  # never set when env_summary is present (no raw value exposure)

    def verification(self) -> dict[str, bool]:
        return {
            "resource_resolved": True,
            "file_read_non_empty": self.bytes_read > 0,
            "target_resource_accessed": True,
            "secret_values_redacted": self.env_summary is not None or self.preview is None,
        }


def execute_read_file(*, toolbox: RepositoryToolbox, target_path: str, repository_root: Path) -> ReadFileExecutionResult:
    """Resolve `target_path` against `repository_root` and read it for real via
    `toolbox`. Raises `ReadFileError` (never returns a result) on any failure —
    matching `real_execute.py`'s existing fail-closed convention for the source-edit
    path."""
    relative_path = resolve_relative_path(target_path, repository_root)
    if relative_path is None:
        raise ReadFileError("PATH_NOT_ALLOWED", f"{target_path!r} does not resolve inside the approved repository root")

    try:
        result = toolbox.read_file(relative_path)
    except FileNotFoundError as exc:
        raise ReadFileError("PATH_NOT_FOUND", relative_path) from exc
    except IsADirectoryError as exc:
        raise ReadFileError("PATH_INVALID", f"{relative_path} is a directory, not a file") from exc
    except RepositoryToolError as exc:
        raise ReadFileError(exc.code, exc.detail) from exc

    content = str(result.get("content") or "")
    if not content.strip():
        raise ReadFileError("RESULT_EMPTY", f"{relative_path} was read but is empty")

    env_summary: EnvFileSummary | None = None
    preview: str | None = None
    if is_env_like_path(relative_path):
        env_summary = summarize_env_content(content)
    else:
        # Non-env files may still carry credentials; withhold any raw preview and
        # report structure only. A future workstream can add per-file-type-aware
        # previews; until then, fail closed toward "no raw content in evidence".
        preview = None

    return ReadFileExecutionResult(
        relative_path=relative_path,
        bytes_read=int(result.get("size_bytes") or 0),
        line_count=len(content.splitlines()),
        truncated=bool(result.get("truncated")),
        env_summary=env_summary,
        preview=preview,
    )


__all__ = [
    "ReadFileError",
    "EnvKeySummary",
    "EnvFileSummary",
    "ReadFileExecutionResult",
    "looks_like_read_file_objective",
    "extract_target_path",
    "normalize_repo_relative_path",
    "resolve_relative_path",
    "is_env_like_path",
    "is_sensitive_key",
    "summarize_env_content",
    "execute_read_file",
]
