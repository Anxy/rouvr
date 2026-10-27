"""AIX-SEM-001-W2 — Scoped upstream discovery for amnesiac steps.

Pure planning helpers (plus deterministic truncation). Discovery I/O stays in
multi_step_execute / discovery.py.

Continuity Law (B): discovery_paths = target_scope ∪ depends_on.target_scope
(read-only upstream). Upstream bodies are byte-capped. Missing upstream fails
closed (NEED_UPSTREAM) — no hallucinated defaults.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable


UPSTREAM_HELPER_VERSION = "sem001.w2.v1"
# Soft cap for read-only upstream bodies in the codegen prompt path.
DEFAULT_UPSTREAM_MAX_BYTES = 12_288


@dataclass(frozen=True)
class UpstreamDiscoveryPlan:
    """Resolved read-only upstream paths for one step."""

    upstream_paths: tuple[str, ...]
    depends_on_step_ids: tuple[str, ...]
    unresolved_depends_on: tuple[str, ...]
    helper_version: str = UPSTREAM_HELPER_VERSION


@dataclass(frozen=True)
class TruncatedUpstreamBody:
    text: str
    original_bytes: int
    kept_bytes: int
    truncated: bool
    content_sha256_12: str


def _norm_path(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("./").strip()


def _looks_like_file(path: str) -> bool:
    p = _norm_path(path)
    if not p or p.endswith("/"):
        return False
    name = p.rsplit("/", 1)[-1]
    return "." in name


def resolve_upstream_target_paths(
    *,
    step_contract: Any | None,
    plan_steps: tuple[Any, ...] | list[Any] = (),
    explicit_upstream_paths: tuple[str, ...] | list[str] = (),
    looks_like_a_file: Callable[[str], bool] | None = None,
) -> UpstreamDiscoveryPlan:
    """Resolve depends_on → prior steps' target_scope file paths (read-only)."""

    file_check = looks_like_a_file or _looks_like_file
    explicit = tuple(
        _norm_path(p) for p in explicit_upstream_paths if _norm_path(p) and file_check(_norm_path(p))
    )

    if step_contract is None:
        return UpstreamDiscoveryPlan(
            upstream_paths=tuple(dict.fromkeys(explicit)),
            depends_on_step_ids=(),
            unresolved_depends_on=(),
        )

    depends = tuple(
        str(x).strip() for x in (getattr(step_contract, "depends_on", ()) or ()) if str(x).strip()
    )
    by_id: dict[str, Any] = {}
    for step in plan_steps or ():
        sid = str(getattr(step, "id", "") or "").strip()
        if sid:
            by_id[sid] = step

    resolved: list[str] = list(explicit)
    unresolved: list[str] = []
    for dep_id in depends:
        prior = by_id.get(dep_id)
        if prior is None:
            unresolved.append(dep_id)
            continue
        for raw in getattr(prior, "target_scope", ()) or ():
            path = _norm_path(str(raw))
            if path and file_check(path):
                resolved.append(path)

    # Never treat *this* step's mutation targets as upstream-only noise in the plan
    # (they remain mutation-bound separately). Deduplicate preserving order.
    self_targets = {
        _norm_path(str(p))
        for p in (getattr(step_contract, "target_scope", ()) or ())
        if _norm_path(str(p))
    }
    upstream = tuple(
        dict.fromkeys(p for p in resolved if p and p not in self_targets)
    )
    return UpstreamDiscoveryPlan(
        upstream_paths=upstream,
        depends_on_step_ids=depends,
        unresolved_depends_on=tuple(unresolved),
    )


def truncate_upstream_content(
    content: str,
    *,
    max_bytes: int = DEFAULT_UPSTREAM_MAX_BYTES,
    path: str = "",
) -> TruncatedUpstreamBody:
    """Deterministic head-extract for upstream bodies (UTF-8 byte budget)."""

    raw = content if isinstance(content, str) else str(content or "")
    encoded = raw.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    limit = max(256, int(max_bytes))
    if len(encoded) <= limit:
        return TruncatedUpstreamBody(
            text=raw,
            original_bytes=len(encoded),
            kept_bytes=len(encoded),
            truncated=False,
            content_sha256_12=digest,
        )
    # Head extract on UTF-8 bytes; avoid splitting a codepoint.
    head = encoded[:limit]
    while head:
        try:
            text = head.decode("utf-8")
            break
        except UnicodeDecodeError:
            head = head[:-1]
    else:
        text = ""
    marker = (
        f"\n\n/* SEM-001-W2 upstream truncated path={_norm_path(path) or '?'} "
        f"kept_bytes={len(head)} original_bytes={len(encoded)} sha256_12={digest} */\n"
    )
    out = text + marker
    return TruncatedUpstreamBody(
        text=out,
        original_bytes=len(encoded),
        kept_bytes=len(head),
        truncated=True,
        content_sha256_12=digest,
    )


def merge_discovery_paths(
    *,
    mutation_paths: tuple[str, ...] | list[str],
    upstream_paths: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    """discovery_paths = mutation targets ∪ upstream (order: targets first)."""

    out: list[str] = []
    seen: set[str] = set()
    for group in (mutation_paths, upstream_paths):
        for raw in group:
            path = _norm_path(str(raw))
            if not path or path.casefold() in seen:
                continue
            seen.add(path.casefold())
            out.append(path)
    return tuple(out)


__all__ = [
    "DEFAULT_UPSTREAM_MAX_BYTES",
    "UPSTREAM_HELPER_VERSION",
    "TruncatedUpstreamBody",
    "UpstreamDiscoveryPlan",
    "merge_discovery_paths",
    "resolve_upstream_target_paths",
    "truncate_upstream_content",
]
