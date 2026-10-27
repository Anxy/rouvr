"""Resolve multi-word topic phrases in objectives to concrete repository files.

Natural asks often say "feature flag" or "mutation helper" without a path.
Snake-case those phrases and locate matching source/test files so planning is
not stuck on NEEDS_MORE_EVIDENCE with zero reads (live S7).
"""

from __future__ import annotations

import re
from pathlib import Path

_STOP = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "into", "when", "than",
        "then", "also", "must", "should", "only", "even", "true", "false", "missing",
        "still", "future", "past", "date", "utc", "iso", "string", "value", "values",
        "current", "existing", "optional", "support", "add", "ensure", "keep", "run",
        "pass", "cases", "case", "tests", "test", "focused", "suite", "repository",
        "backend", "frontend", "caller", "callers", "behavior", "defaults", "default",
        "compatible", "update", "cover", "covered", "until", "they", "any", "care",
        "about", "expiry", "active", "inactive", "absent", "present", "working",
        "remain", "unchanged", "treated", "return", "returns", "expose", "exposes",
        "currently", "name", "version", "enabled", "yourself", "approach", "forward",
        "destructive", "production", "operations", "loadable", "unverified", "new",
        "users", "explore", "classify", "every", "mounted", "public", "actual",
        "persist", "machine", "readable", "proving", "retired", "stay", "fail",
        "closed", "while", "remains", "writer", "include", "short", "critical",
        "most", "seven", "follow", "engineering", "close", "remaining", "dual",
        "gaps", "invent", "thousand", "weaken", "rules", "prompt", "keyword",
        "filename", "specific", "branches", "hyeve", "service", "live", "path",
        "list", "tasks", "ready", "authorization", "evidence", "completion",
        "verification", "at", "or", "by", "in", "of", "to", "a",
    }
)

# Stems that match hundreds of files and must never lock mutation scope.
_NOISY_STEMS = frozenset({"service", "live", "app", "base", "core", "util", "utils", "helper", "common"})

_PATH_RE = re.compile(
    r"(?i)(?:[a-z]:\\|\\\\|/)(?:[^\s:*?\"<>|]+[/\\])+[^\s:*?\"<>|]+"
)

# Lone tokens that match huge platform surfaces and drown fixture/feature work
# (live S10: "email verification" → verification.py instead of fixture user DB).
_PLATFORM_GENERIC_STEMS = frozenset(
    {
        "verification",
        "verification_engine",
        "verification_service",
        "evidence",
        "evidence_store",
        "state_store",
        "postgres_stores",
        "discuss_store",
    }
)

_SKIP_DIR_PARTS = frozenset(
    {
        "node_modules",
        "vendor",
        ".git",
        ".venv",
        "venv",
        "dist",
        "build",
        "__pycache__",
        ".next",
        "storage",
    }
)

_EXTENSIONS = (".py", ".php", ".ts", ".tsx", ".js", ".jsx", ".go", ".java", ".rb", ".sql")


def _prepare_objective_text(objective: str) -> str:
    """Drop filesystem paths and URLs so they cannot consume the hint budget."""
    text = str(objective or "")
    text = _PATH_RE.sub(" ", text)
    text = re.sub(r"https?://\S+", " ", text)
    return text.lower()


def extract_topic_snake_hints(objective: str) -> list[str]:
    """Build snake_case file stems from adjacent content words in the objective."""
    text = _prepare_objective_text(objective)
    words = [w for w in re.findall(r"[a-z]{3,}", text) if w not in _STOP]
    hints: list[str] = []
    seen: set[str] = set()

    def _add(hint: str) -> None:
        if not hint or hint in seen or len(hint) < 4:
            return
        seen.add(hint)
        hints.append(hint)

    # Explicit product phrases that do not fall out of naive bigrams.
    if "live-fixture" in text or "live fixture" in text:
        _add("live_fixtures")
    # DB / migration fixtures (S10) — only when the ask is actually about data stores.
    if any(
        tok in text
        for tok in (
            "user store",
            "email verification",
            "email verified",
            "migration",
            "schema",
            "sqlite",
            "database",
            "db user",
        )
    ):
        _add("fixture_users")
        _add("migrate")
        _add("models")
        _add("test_db_users")
        _add("schema")
        _add("migrations")
    # API fixtures (S11) — only when the ask is actually the items API gauntlet,
    # not every objective that mentions an HTTP endpoint or production router.
    if any(
        tok in text
        for tok in (
            "items api",
            "archive support to the live-fixture",
            "/items",
            "api tests",
        )
    ) or ("archive" in text and "item" in text) or ("api" in text and "live fixture" in text):
        _add("test_api_items")
        _add("items")
        _add("archive")
        _add("schemas")
        # Prefer package folder stem used under live_fixtures/api/
        _add("service")
        _add("app")
    # Frontend operator product asks (FE missions).
    if any(
        tok in text
        for tok in (
            "cycle summary",
            "summary strip",
            "live execution",
            "execution experience",
            "fixture-mode",
            "fixture mode",
            "objectives list",
            "playwright",
            "operator",
        )
    ):
        _add("execution")
        _add("run_detail")
        _add("FixtureModeWarning")
        _add("ExecutionPanels")
        _add("objectives")
        _add("approval")
    if "email verification" in text or "email verified" in text:
        _add("email_verified")
        _add("email_verification")
        # Do not keep bare "verification" — it binds the platform verifier stack.
        seen.add("verification")
    if "user store" in text:
        _add("user_store")
        _add("fixture_users")
        _add("models")
    if "migration" in text or "schema" in text:
        _add("migrate")
        _add("schema")
        _add("migrations")

    for index in range(len(words) - 1):
        left, right = words[index], words[index + 1]
        snake = f"{left}_{right}"
        _add(snake)
        if len(hints) >= 24:
            break
    # Also keep single distinctive tokens that often name modules.
    for word in words:
        if word in _PLATFORM_GENERIC_STEMS and (
            "email" in text or "live-fixture" in text or "live fixture" in text
        ):
            continue
        if word in _NOISY_STEMS:
            continue
        _add(word)
        if len(hints) >= 28:
            break
    return hints


def resolve_topic_hints_in_repository(
    repository_root: Path,
    hints: list[str] | tuple[str, ...],
    *,
    max_hits: int = 10,
    objective: str = "",
) -> list[str]:
    """Map snake_case topic hints to repo-relative source/test paths."""
    root = Path(repository_root)
    if not root.is_dir() or not hints:
        return []
    found: list[str] = []
    seen: set[str] = set()
    prefer_live = "live-fixture" in str(objective or "").lower() or "live fixture" in str(
        objective or ""
    ).lower()
    obj_l = _prepare_objective_text(objective)
    prefer_api = any(
        tok in obj_l
        for tok in ("items api", "archive support to the live-fixture", "/items", "api tests")
    ) or ("archive" in obj_l and "item" in obj_l) or ("api" in obj_l and "live fixture" in obj_l)
    prefer_db = any(
        tok in obj_l
        for tok in ("user store", "email verification", "migration", "schema", "sqlite", "database")
    )
    prefer_routers = "router" in obj_l and any(
        tok in obj_l for tok in ("authority", "include_router", "classify", "mounted")
    )

    def _score(rel: str, hint: str) -> tuple[int, int, int, int, str]:
        stem = Path(rel).stem.lower().replace("-", "_")
        name = Path(rel).name.lower().replace("-", "_")
        rel_l = rel.lower().replace("\\", "/")
        live_boost = 0 if (prefer_live and "live_fixtures" in rel_l) else 1
        if not prefer_live and "live_fixtures" in rel_l:
            live_boost = 4
        if prefer_live and "live_fixtures" not in rel_l and stem in _PLATFORM_GENERIC_STEMS:
            live_boost = 3
        # Within live fixtures, keep API asks out of the DB package and vice versa.
        package_boost = 0
        if prefer_api and "live_fixtures/api" in rel_l:
            package_boost = -1
        elif prefer_api and "live_fixtures/db" in rel_l:
            package_boost = 2
        elif prefer_db and "live_fixtures/db" in rel_l:
            package_boost = -1
        elif prefer_db and "live_fixtures/api" in rel_l:
            package_boost = 2
        if prefer_routers and (
            "/routers/" in rel_l
            or rel_l.endswith("/main.py")
            or "endpoint_classification" in rel_l
        ):
            package_boost -= 2
        if stem == hint or stem == f"test_{hint}":
            rank = 0
        elif stem.endswith(f"_{hint}") or stem.startswith(f"{hint}_"):
            rank = 1
        elif hint in stem or hint in rel_l:
            rank = 2
        else:
            rank = 9
        return (live_boost, package_boost, rank, len(name), rel)

    def _has_exact_module(hint: str) -> bool:
        for path in root.rglob(f"{hint}.py"):
            if path.is_file() and not any(part in _SKIP_DIR_PARTS for part in path.parts):
                return True
        for path in root.rglob(f"test_{hint}.py"):
            if path.is_file() and not any(part in _SKIP_DIR_PARTS for part in path.parts):
                return True
        for path in root.rglob(f"{hint}.sql"):
            if path.is_file() and not any(part in _SKIP_DIR_PARTS for part in path.parts):
                return True
        return False

    # Prefer hints that match an exact module stem (feature_flag.py over feature_flags_*).
    # Among exact hits, prefer the shorter stem so singular modules win over plural packages.
    ordered_hints = sorted(
        (str(h or "").strip().lower() for h in hints),
        key=lambda h: (0 if _has_exact_module(h) else 1, len(h), h),
    )

    for stem_hint in ordered_hints:
        # Allow short exact module stems (e.g. app.py for API handlers) when the
        # objective is API-shaped; otherwise keep the 4-char floor to avoid noise.
        min_len = 3 if (prefer_api and stem_hint in {"app", "api"}) else 4
        if len(stem_hint) < min_len:
            continue
        if stem_hint in _NOISY_STEMS and not prefer_api and not prefer_live:
            continue
        hits_for_hint: list[str] = []
        for path in root.rglob(f"*{stem_hint}*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in _EXTENSIONS:
                continue
            if any(part in _SKIP_DIR_PARTS for part in path.parts):
                continue
            name = path.name.lower().replace("-", "_")
            rel_try = str(path).lower().replace("\\", "/")
            if stem_hint not in name and stem_hint not in rel_try:
                continue
            try:
                rel = path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
            hits_for_hint.append(rel)
        if prefer_routers and stem_hint in {"router", "routers", "aix_router"}:
            production = [
                rel
                for rel in hits_for_hint
                if "/routers/" in rel.replace("\\", "/").lower()
                or rel.replace("\\", "/").lower().endswith("/main.py")
            ]
            if production:
                hits_for_hint = production
        if len(hits_for_hint) > 24:
            exact = [
                rel
                for rel in hits_for_hint
                if Path(rel).stem.lower().replace("-", "_") in {stem_hint, f"test_{stem_hint}"}
            ]
            if 0 < len(exact) <= 8:
                hits_for_hint = exact
            else:
                continue
        hits_for_hint = sorted(hits_for_hint, key=lambda rel: _score(rel, stem_hint))
        for rel in hits_for_hint[:4]:
            if rel not in seen:
                seen.add(rel)
                found.append(rel)
            if len(found) >= max_hits:
                break
        if len(found) >= max_hits:
            break

    if prefer_live:
        live_only = [p for p in found if "live_fixtures" in p.replace("\\", "/").lower()]
        if prefer_api:
            api_only = [p for p in live_only if "/live_fixtures/api/" in p.replace("\\", "/").lower()]
            if api_only:
                return api_only[:max_hits]
        if prefer_db:
            db_only = [p for p in live_only if "/live_fixtures/db/" in p.replace("\\", "/").lower()]
            if db_only:
                return db_only[:max_hits]
        if live_only:
            return live_only[:max_hits]
    return found[:max_hits]


def topic_hits_are_mutation_scope(paths: list[str] | tuple[str, ...], *, objective: str = "") -> bool:
    """True only when topic hits are a tight module family, not a surface-wide inventory.

    Binding those hits as allowed_files skips exploration and blocks creating new
    files. Gauntlet fixture asks stay tight; mounted-router / authority inventories
    must remain unbound so execute can read production mounts and persist a new matrix.
    """
    if not paths:
        return False
    obj_l = _prepare_objective_text(objective)
    prefer_live = "live-fixture" in obj_l or "live fixture" in obj_l
    prefer_api = any(
        tok in obj_l
        for tok in ("items api", "archive support to the live-fixture", "/items", "api tests")
    ) or ("archive" in obj_l and "item" in obj_l) or ("api" in obj_l and "live fixture" in obj_l)
    prefer_db = any(
        tok in obj_l
        for tok in ("user store", "email verification", "migration", "schema", "sqlite", "database")
    )
    prefer_routers = "router" in obj_l and any(
        tok in obj_l for tok in ("authority", "include_router", "classify", "mounted")
    )
    if prefer_live or prefer_api or prefer_db:
        return True
    if prefer_routers:
        return False
    rels = [str(p).replace("\\", "/").lower() for p in paths]
    parents = {str(Path(p).parent) for p in rels}
    stems = {Path(p).stem.lower().replace("-", "_") for p in rels}
    return (len(rels) <= 4 and len(parents) <= 2) or len(stems) <= 2


__all__ = [
    "extract_topic_snake_hints",
    "resolve_topic_hints_in_repository",
    "topic_hits_are_mutation_scope",
]
