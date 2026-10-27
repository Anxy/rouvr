"""Resolve an objective's `repository_id` to a real, explicitly-approved filesystem root.

No such registry previously existed anywhere in this codebase:
`repo_profile_resolver_service.py` resolves command/drift *policy* for a repo key, not a
repo *location*. This module fills that gap, fail-closed: only `repository_id`s present in
`OBJECTIVE_CYCLE_REPOSITORY_ROOTS` (a JSON object, default `{}`) resolve to a path, and only
if that path exists on disk as a directory. Everything else is `REPOSITORY_ROOT_UNKNOWN` —
never guessed, never inferred from `repository_id` itself.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


class RepositoryLocationError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


def _load_registry(settings: object | None) -> dict[str, str]:
    def _parse_registry(raw: str) -> dict[str, str]:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RepositoryLocationError("REPOSITORY_ROOTS_CONFIG_INVALID", str(exc)) from exc
        if not isinstance(parsed, dict):
            raise RepositoryLocationError("REPOSITORY_ROOTS_CONFIG_INVALID", "must be a JSON object")
        return {str(k): str(v) for k, v in parsed.items()}

    registry: dict[str, str] = {}
    if settings is not None:
        settings_raw = str(getattr(settings, "OBJECTIVE_CYCLE_REPOSITORY_ROOTS", "") or "").strip()
        if settings_raw:
            registry = _parse_registry(settings_raw)
    # Default pydantic settings use "{}" — treat an empty map as "unset" so
    # HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS (tests, local dev) still
    # resolves without mutating the global settings singleton.
    if not registry:
        env_raw = str(os.getenv("HYEVE_AIX_SERVICE_OBJECTIVE_CYCLE_REPOSITORY_ROOTS", "") or "").strip()
        if env_raw:
            registry = _parse_registry(env_raw)
    return registry


def is_registered_repository(repository_id: str, *, settings: object | None = None) -> bool:
    """Cheap membership check used by callers deciding demo-path vs. real-edit-path
    before paying the cost of full resolution/validation."""
    registry = _load_registry(settings)
    return str(repository_id or "").strip() in registry


def resolve_repository_root(repository_id: str, *, settings: object | None = None) -> Path:
    """Return the approved, existing filesystem root for `repository_id`.

    Raises `RepositoryLocationError` (never returns a best-guess path) when:
      - `repository_id` is empty
      - the registry has no entry for it (`REPOSITORY_ROOT_UNKNOWN`)
      - the configured path does not exist / is not a directory (`REPOSITORY_ROOT_MISSING`)
    """
    key = str(repository_id or "").strip()
    if not key:
        raise RepositoryLocationError("REPOSITORY_ID_REQUIRED")
    registry = _load_registry(settings)
    configured = registry.get(key)
    if not configured:
        raise RepositoryLocationError(
            "REPOSITORY_ROOT_UNKNOWN",
            f"repository_id {key!r} is not present in OBJECTIVE_CYCLE_REPOSITORY_ROOTS",
        )
    root = Path(configured).resolve()
    if not root.is_dir():
        raise RepositoryLocationError(
            "REPOSITORY_ROOT_MISSING",
            f"configured root for {key!r} does not exist or is not a directory: {root}",
        )
    return root


def resolve_repository_for_relative_token(
    token: str, *, settings: object | None = None
) -> tuple[str, str] | None:
    """Resolve a bare relative / service-relative path token (no drive letter, not
    resolvable against the current process's CWD) by matching a registered
    `repository_id` against the token's own path segments.

    Handles the two shapes `extract_target_path` now extracts but
    `resolve_repository_for_path` cannot (that function requires the token to already
    resolve to an absolute filesystem path via the CWD):
      - `hyeve-integration-service\\.env`   -> repo IS the first segment
      - `hyeve\\services\\hyeve-integration-service` -> repo is the LAST segment,
        no file beneath it (whole-repository target)

    Never guesses: a registered `repository_id` must appear as an exact,
    case-insensitive path *segment* (not a substring of a longer segment). When more
    than one registered id matches, the longest (most specific) match wins -- ties are
    ambiguous and return `None`. Returns `(repository_id, relative_path_within_repo)`;
    `relative_path_within_repo` is `""` when the token names the repository itself with
    no file/sub-path beneath it.
    """
    registry = _load_registry(settings)
    if not registry:
        return None
    segments = [seg for seg in re.split(r"[\\/]+", token.strip()) if seg not in ("", ".")]
    if not segments:
        return None
    best: tuple[str, int] | None = None  # (repository_id, matched_segment_index)
    for repo_id, configured in registry.items():
        try:
            root = Path(configured).resolve()
        except OSError:
            continue
        if not root.is_dir():
            continue
        for idx, segment in enumerate(segments):
            if segment.casefold() == repo_id.casefold():
                if best is None or len(repo_id) > len(best[0]):
                    best = (repo_id, idx)
                break
    if best is None:
        return None
    repo_id, idx = best
    remainder = "/".join(segments[idx + 1 :])
    return repo_id, remainder


def find_repository_mentioned_in_text(text: str, *, settings: object | None = None) -> str | None:
    """Fail-closed scan for a registered `repository_id` named anywhere in free text,
    independent of path formatting -- covers objectives that name a service by its
    repository id without ever writing a path token at all (e.g. "Integrate SMTP
    settings into hyeve-integration-service"). A whole-word match only (never a
    substring of a longer identifier); when multiple distinct registered ids are
    mentioned, the longest (most specific) one wins; a true tie is ambiguous and
    returns `None` rather than guessing.
    """
    registry = _load_registry(settings)
    if not registry:
        return None
    matches: list[str] = []
    for repo_id in registry:
        pattern = re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(repo_id) + r"(?![A-Za-z0-9_-])", re.IGNORECASE)
        if pattern.search(text):
            matches.append(repo_id)
    if not matches:
        return None
    matches.sort(key=len, reverse=True)
    if len(matches) > 1 and len(matches[0]) == len(matches[1]):
        return None
    return matches[0]


def rebind_repository_for_objective(
    repository_id: str,
    objective_text: str,
    *,
    target_path: str | None = None,
    settings: object | None = None,
) -> tuple[str | None, str | None]:
    """Single canonical resolver for 'which registered repository does this objective
    actually target', used identically by the exploration gate and the real executor
    so the two stages can never disagree.

    `repository_id` is the caller-supplied id (may be wrong or unregistered).
    `target_path` is whatever `extract_target_path` already found in the objective
    text (absolute, bare-relative, or `None`). Returns `(repository_id, target_path)`
    -- both unchanged when `repository_id` is already registered; `(None, target_path)`
    when nothing can be unambiguously resolved (never a guess, never a default like
    the AIX checkout itself). A resolved bare-repository mention (no file beneath it)
    reports `target_path="."` -- a real, meaningful value (the repository root) rather
    than `None`, so downstream intent classification still knows a resource was bound.
    """
    already_registered = is_registered_repository(repository_id, settings=settings)
    looks_absolute = bool(target_path) and bool(
        re.match(r"^[A-Za-z]:[\\/]", target_path) or target_path.startswith(("/", "\\\\"))
    )

    # Absolute path ownership wins over a wrong/default caller repository_id
    # (e.g. CLI default aix-frontend + D:\v2.1\Visions\foo.html).
    if target_path is not None and looks_absolute:
        # If the caller already bound a repo and the path sits inside it, keep that
        # bind and relativize. Nested child registrations (aix-backend under
        # hyeve-aix-service) must not drop the absolute path on the floor — that
        # later becomes PATH_OUTSIDE_SCOPE even though the path is in-scope.
        if already_registered:
            try:
                current_root = resolve_repository_root(repository_id, settings=settings)
                rel = Path(target_path).resolve().relative_to(current_root).as_posix()
                return repository_id, rel or "."
            except (OSError, ValueError, RepositoryLocationError):
                pass
        direct = resolve_repository_for_path(target_path, settings=settings)
        if direct is not None:
            if already_registered and direct.casefold() != repository_id.casefold():
                try:
                    current_root = resolve_repository_root(repository_id, settings=settings)
                    new_root = resolve_repository_root(direct, settings=settings)
                    current_root.resolve().relative_to(new_root.resolve())
                    # New root is an ancestor of the already-bound repo — refuse
                    # silent scope expansion. Genuine escapes stay unbound.
                    return None, target_path
                except (OSError, ValueError, RepositoryLocationError):
                    pass
            try:
                root = resolve_repository_root(direct, settings=settings)
                rel = Path(target_path).resolve().relative_to(root).as_posix()
            except (OSError, ValueError, RepositoryLocationError):
                # Configured root may be absent on this machine; still relativize
                # against the registry path so ownership rebind stays meaningful.
                configured = _load_registry(settings).get(direct)
                if configured:
                    try:
                        rel = (
                            Path(target_path)
                            .resolve()
                            .relative_to(Path(configured).resolve())
                            .as_posix()
                        )
                    except (OSError, ValueError):
                        rel = target_path
                else:
                    rel = target_path
            return direct, rel or "."
        # Absolute path matches no registered root: never keep a wrong registered id
        # (that previously became WORKSPACE_NOT_BOUND after resolving outside the root).
        return None, target_path

    if target_path is not None and not looks_absolute:
        # Service-relative token (`hyeve-integration-service\.env`): strip a redundant
        # leading repository-name segment even when `repository_id` is already valid --
        # otherwise a caller that supplies the correct id *and* a service-relative path
        # ends up double-nesting the path (`<root>/hyeve-integration-service/.env`
        # instead of `<root>/.env`). Only applied when the matched repo agrees with an
        # already-valid `repository_id`; a path naming a *different* registered repo
        # than one already confirmed valid is an ambiguous cross-repo reference, not a
        # silent repo switch. An absolute path is left untouched -- it already resolves
        # unambiguously on its own and segment-matching it could misfire on a repo name
        # that also happens to appear in an unrelated ancestor directory.
        relative = resolve_repository_for_relative_token(target_path, settings=settings)
        if relative is not None:
            matched_id, remainder = relative
            if not already_registered or matched_id.casefold() == repository_id.casefold():
                return matched_id, (remainder or ".")

    if already_registered:
        return repository_id, target_path

    if target_path is not None:
        direct = resolve_repository_for_path(target_path, settings=settings)
        if direct is not None:
            return direct, target_path

    mentioned = find_repository_mentioned_in_text(objective_text, settings=settings)
    if mentioned is not None:
        return mentioned, (target_path or ".")

    return None, target_path


def resolve_repository_for_path(path: str, *, settings: object | None = None) -> str | None:
    """Reverse lookup: which registered `repository_id`'s root contains `path`.

    Closes the "bare-path -> workspace" gap (`AIX-REAL-EXECUTION-002.md`): a caller
    that only knows a literal filesystem path -- not which `repository_id` owns it --
    must not be forced to guess. This never guesses either: it returns a
    `repository_id` only when exactly one registered, existing root is a real
    path-segment ancestor of `path` (via `Path.relative_to`, not a string prefix, so
    an allowed-root collision such as `C:\\repo` vs. `C:\\repository` cannot match).
    Zero or ambiguous (more than one) matches both return `None` -- the caller stays
    fail-closed exactly as when no path is supplied at all.
    """
    registry = _load_registry(settings)
    if not registry:
        return None
    try:
        candidate = Path(path).resolve()
    except OSError:
        return None
    matches: list[str] = []
    for repo_id, configured in registry.items():
        try:
            root = Path(configured).resolve()
        except OSError:
            continue
        # Match configured ownership even when the checkout is absent locally
        # (partial trees / CI). Opening the root for execution still fail-closes later.
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        matches.append(repo_id)
    if not matches:
        # Path may be a parent of registered roots (e.g. D:\v2.1\AIX when only
        # hyeve-aix-service / aix-backend are registered underneath). Prefer an
        # exact `aix` / `hyeve-aix-service` child when present, else the longest
        # registered child — never return the raw filesystem path as an id.
        child_matches: list[tuple[int, str]] = []
        for repo_id, configured in registry.items():
            try:
                root = Path(configured).resolve()
            except OSError:
                continue
            try:
                root.relative_to(candidate)
            except ValueError:
                continue
            if repo_id in {"aix", "hyeve-aix-service"}:
                return repo_id
            child_matches.append((len(str(root)), repo_id))
        if child_matches:
            child_matches.sort(key=lambda item: item[0], reverse=True)
            if len(child_matches) == 1 or child_matches[0][0] != child_matches[1][0]:
                return child_matches[0][1]
        return None
    if len(matches) == 1:
        return matches[0]
    # Nested registered roots (hyeve-aix-service ⊃ aix-backend) are not ambiguous:
    # the most specific existing root is the tighter bound. A true length tie stays
    # fail-closed.
    ranked: list[tuple[int, str]] = []
    for repo_id in matches:
        try:
            ranked.append((len(str(Path(registry[repo_id]).resolve())), repo_id))
        except OSError:
            continue
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None
    return ranked[0][1]


__all__ = [
    "RepositoryLocationError",
    "is_registered_repository",
    "resolve_repository_root",
    "resolve_repository_for_path",
    "resolve_repository_for_relative_token",
    "find_repository_mentioned_in_text",
    "rebind_repository_for_objective",
]
