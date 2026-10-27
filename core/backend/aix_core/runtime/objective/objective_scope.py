"""Generic objective scope: extract required work kinds and validate plans cover them.

Technology-agnostic. Refines HOW elsewhere; this module only preserves WHAT.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Literal

TaskMode = Literal[
    "STATIC_PAGE",
    "FRONTEND_APP",
    "BACKEND_TASK",
    "FULL_STACK",
    "GENERAL_REPO_TASK",
]

RequirementKind = Literal[
    "frontend",
    "backend",
    "database",
    "api",
    "tests",
    "migration",
    "config",
    "integration",
    "files",
    "constraint",
    "docs",
]


@dataclass(frozen=True)
class ScopeRequirement:
    kind: RequirementKind
    label: str
    evidence_terms: tuple[str, ...]


@dataclass(frozen=True)
class ScopeCoverageResult:
    ok: bool
    code: str
    detail: str
    missing: tuple[ScopeRequirement, ...] = ()
    covered: tuple[ScopeRequirement, ...] = ()
    original_objective: str = ""
    plan_steps_inspected: tuple[str, ...] = ()


_META_DOD = frozenset(
    {
        "scoped change complete",
        "validation evidence attached",
        "mutation summary",
        "repository state exists",
        "requested repository state",
        "evidence-accurate",
    }
)

# Page / UI surface signals (not sufficient alone for demo lane).
_PAGE_SURFACE = re.compile(
    r"\b(html|css|landing\s+page|web\s+page|static\s+page|frontend|front-end|"
    r"ui|website|web\s+app|dashboard|page|form|mockup)\b",
    re.IGNORECASE,
)

# Anything that means the work is not a static HTML/CSS proposal.
_NON_STATIC_SCOPE = re.compile(
    r"\b("
    r"backend|server[- ]?side|api|endpoint|router|controller|service\s+layer|"
    r"database|db\b|sql|postgres|postgresql|mysql|mariadb|mongodb|sqlite|redis|"
    r"orm|persistence|schema|migration|migrate|alembic|prisma|sequelize|"
    r"php|python|fastapi|django|flask|node\.?js|express|nestjs|golang|go\b|"
    r"java\b|spring|ruby|rails|rust\b|\.net|csharp|c#|"
    r"graphql|grpc|websocket|queue|worker|cron|"
    r"auth(?:entication|orization)?|jwt|oauth|"
    r"pytest|unittest|integration\s+test|"
    r"dockerfile|kubernetes|terraform|ci/?cd|"
    r"refactor|bug\s*fix|patch\b|edit\b|mutate|"
    r"model\b|repository\s+pattern"
    r")\b",
    re.IGNORECASE,
)

_FRONTEND_RE = re.compile(
    r"\b(html|css|frontend|front-end|ui|landing\s+page|web\s+page|static\s+page|"
    r"website|react|vue|angular|svelte|tsx|jsx|dashboard|page)\b",
    re.IGNORECASE,
)
_BACKEND_RE = re.compile(
    r"\b(backend|server[- ]?side|php|python|fastapi|django|flask|node\.?js|express|"
    r"nestjs|golang|\bgo\b|java\b|spring|ruby|rails|rust\b|laravel|symfony)\b",
    re.IGNORECASE,
)
_API_RE = re.compile(
    r"\b(api|endpoint|router|route|http|rest|graphql|grpc|controller)\b",
    re.IGNORECASE,
)
_DB_RE = re.compile(
    r"\b(database|db\b|sql|postgres|postgresql|mysql|mariadb|mongodb|sqlite|redis|"
    r"orm|persistence|schema|model\b)\b",
    re.IGNORECASE,
)
_MIGRATION_RE = re.compile(
    r"\b(migration|migrate|alembic|prisma\s+migrate|schema\s+change)\b",
    re.IGNORECASE,
)
_TEST_RE = re.compile(
    r"\b(tests?|pytest|unittest|coverage|spec\b|jest|vitest|phpunit)\b",
    re.IGNORECASE,
)
_CONFIG_RE = re.compile(
    r"\b(config(?:uration|urable)?|settings|\.env|environment\s+variable|flag|timeout)\b",
    re.IGNORECASE,
)
_INTEGRATION_RE = re.compile(
    r"\b(integrat(?:e|ion)|webhook|third[- ]party|oauth|smtp|s3|stripe)\b",
    re.IGNORECASE,
)
_DOCS_RE = re.compile(
    r"\b(documentation|readme|docs?\b|docstring|phpdoc)\b",
    re.IGNORECASE,
)
_CONSTRAINT_RE = re.compile(
    r"\b(do not|don't|must not|without (?:changing|modifying)|preserving|no other changes)\b",
    re.IGNORECASE,
)
_FILE_PATH_RE = re.compile(
    r"(?<![a-z0-9_/\\-])([a-z0-9_./\\-]+\.[a-z0-9]{1,10})\b",
    re.IGNORECASE,
)

_KIND_PLAN_SIGNALS: dict[RequirementKind, tuple[str, ...]] = {
    "frontend": (
        "html", "css", "frontend", "front-end", "ui", "page", "react", "vue",
        "angular", "svelte", "tsx", "jsx", "proposed", "styles.css", "index.html",
        "landing", "website", "dashboard",
    ),
    "backend": (
        "backend", "server", "php", "python", "fastapi", "django", "flask",
        "node", "express", "nestjs", "golang", "java", "spring", "rails",
        "laravel", "service", "worker",
    ),
    "api": (
        "api", "endpoint", "router", "route", "rest", "graphql", "grpc",
        "controller", "http",
    ),
    "database": (
        "database", "db", "sql", "postgres", "mysql", "mongodb", "sqlite",
        "redis", "orm", "persistence", "schema", "model",
    ),
    "migration": ("migration", "migrate", "alembic", "prisma", "schema"),
    "tests": ("test", "pytest", "unittest", "coverage", "spec", "jest", "phpunit", "validation"),
    "config": ("config", "settings", ".env", "environment", "flag", "timeout"),
    "integration": ("integrat", "webhook", "oauth", "smtp", "stripe", "third-party"),
    "docs": ("documentation", "readme", "docs", "docstring", "phpdoc"),
    "files": (),
    "constraint": (),
}


def is_generic_meta_dod(description: str) -> bool:
    text = re.sub(r"\s+", " ", str(description or "")).strip().casefold()
    if not text:
        return False
    if text in _META_DOD:
        return True
    return any(marker in text for marker in _META_DOD)


def classify_task_mode(objective: str) -> TaskMode:
    text = str(objective or "")
    has_page = bool(_PAGE_SURFACE.search(text))
    has_non_static = bool(_NON_STATIC_SCOPE.search(text))
    has_frontend = bool(_FRONTEND_RE.search(text))
    has_backendish = bool(_BACKEND_RE.search(text) or _API_RE.search(text) or _DB_RE.search(text))

    if has_page and not has_non_static:
        return "STATIC_PAGE"
    if has_frontend and has_backendish:
        return "FULL_STACK"
    if has_backendish and not has_frontend:
        return "BACKEND_TASK"
    if has_frontend and not has_backendish:
        return "FRONTEND_APP"
    return "GENERAL_REPO_TASK"


def is_demo_compatible_page_objective(objective: str, *, has_vision: bool = False) -> bool:
    """True only when the whole objective is compatible with static HTML/CSS proposal.

    Words like html/frontend alone are not enough when backend/DB/API/etc. are also asked.
    """
    mode = classify_task_mode(objective)
    if mode == "STATIC_PAGE":
        return True
    if has_vision and mode in {"STATIC_PAGE", "FRONTEND_APP"} and not _NON_STATIC_SCOPE.search(
        objective or ""
    ):
        return True
    lowered = (objective or "").casefold()
    wants_tsx_form = ("react" in lowered or "tsx" in lowered) and (
        "registration" in lowered or "register" in lowered
    )
    if wants_tsx_form and not _NON_STATIC_SCOPE.search(objective or ""):
        return True
    return False


def extract_scope_requirements(objective: str) -> tuple[ScopeRequirement, ...]:
    """Derive durable, technology-agnostic required scope facets from the objective."""
    text = str(objective or "").strip()
    if not text:
        return ()
    found: list[ScopeRequirement] = []
    seen: set[str] = set()

    def add(kind: RequirementKind, label: str, terms: tuple[str, ...]) -> None:
        key = f"{kind}:{label.casefold()}"
        if key in seen:
            return
        seen.add(key)
        found.append(ScopeRequirement(kind=kind, label=label, evidence_terms=terms))

    if _FRONTEND_RE.search(text):
        add("frontend", "Frontend / UI surface", _KIND_PLAN_SIGNALS["frontend"])
    if _BACKEND_RE.search(text):
        add("backend", "Backend / server runtime", _KIND_PLAN_SIGNALS["backend"])
    if _API_RE.search(text):
        add("api", "API / endpoints", _KIND_PLAN_SIGNALS["api"])
    if _DB_RE.search(text):
        add("database", "Database / persistence", _KIND_PLAN_SIGNALS["database"])
    if _MIGRATION_RE.search(text):
        add("migration", "Schema migration", _KIND_PLAN_SIGNALS["migration"])
    if _TEST_RE.search(text):
        add("tests", "Tests / verification", _KIND_PLAN_SIGNALS["tests"])
    if _CONFIG_RE.search(text):
        add("config", "Configuration", _KIND_PLAN_SIGNALS["config"])
    if _INTEGRATION_RE.search(text):
        add("integration", "External integration", _KIND_PLAN_SIGNALS["integration"])
    if _DOCS_RE.search(text) and not (_BACKEND_RE.search(text) or _API_RE.search(text)):
        add("docs", "Documentation", _KIND_PLAN_SIGNALS["docs"])

    for match in _FILE_PATH_RE.finditer(text):
        path = match.group(1)
        if path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            continue
        add("files", f"Named path {path}", (path.casefold(), path.replace("\\", "/").casefold()))

    if _CONSTRAINT_RE.search(text):
        add("constraint", "Explicit constraints must be preserved", ("do not", "must not", "preserving"))

    return tuple(found)


def _plan_corpora(plan_steps: Iterable[Any]) -> tuple[str, str, str, tuple[str, ...]]:
    """Split plan text into delivery paths, prose, and prohibitions."""
    path_chunks: list[str] = []
    prose_chunks: list[str] = []
    prohibited_chunks: list[str] = []
    inspected: list[str] = []
    for step in plan_steps:
        purpose = str(getattr(step, "purpose", "") or "")
        scope = " ".join(str(p) for p in (getattr(step, "target_scope", ()) or ()))
        acceptance = " ".join(str(c) for c in (getattr(step, "acceptance_criteria", ()) or ()))
        validation = " ".join(str(c) for c in (getattr(step, "validation_commands", ()) or ()))
        prohibited = " ".join(str(p) for p in (getattr(step, "prohibited_actions", ()) or ()))
        action_bits: list[str] = []
        for action in getattr(step, "actions", ()) or ():
            capability = str(getattr(action, "capability", "") or "")
            operation = str(getattr(action, "operation", "") or "")
            action_bits.append(f"{capability}.{operation}")
            args = getattr(action, "arguments", None) or {}
            if isinstance(args, dict):
                for key in ("paths", "path", "files", "file"):
                    value = args.get(key)
                    if isinstance(value, (list, tuple)):
                        action_bits.extend(str(item) for item in value)
                    elif value:
                        action_bits.append(str(value))
        path_chunks.append(" ".join([scope, " ".join(action_bits), validation]))
        prose_chunks.append(" ".join([purpose, acceptance]))
        prohibited_chunks.append(prohibited)
        inspected.append(f"{getattr(step, 'id', '?')}:{purpose[:80]}")
    return (
        " ".join(path_chunks).casefold(),
        " ".join(prose_chunks).casefold(),
        " ".join(prohibited_chunks).casefold(),
        tuple(inspected),
    )


def _plan_corpus(plan_steps: Iterable[Any]) -> tuple[str, tuple[str, ...]]:
    paths, prose, prohibited, inspected = _plan_corpora(plan_steps)
    return " ".join([paths, prose, prohibited]), inspected


def _facet_explicitly_prohibited(req: ScopeRequirement, prohibited_corpus: str) -> bool:
    """True when a step forbids creating/adding this facet (not mere path allowlists)."""
    if not prohibited_corpus:
        return False
    signals = tuple(
        term.casefold()
        for term in (req.evidence_terms or _KIND_PLAN_SIGNALS.get(req.kind, ()))
        if term
    )
    if not signals:
        return False
    # Evaluate prohibition clauses separately so "do not modify files other than schema.sql"
    # does not look like a ban on database/schema delivery.
    clauses = re.split(r"[.;]\s*|\n+", prohibited_corpus)
    add_ban = re.compile(
        r"\b(?:do\s+not|don't|must\s+not)\s+(?:add|create|include|implement|write|generate)\b|"
        r"\bwithout\s+(?:adding|creating|including|implementing)\b|"
        r"\bdoes\s+not\s+add\b",
        re.IGNORECASE,
    )
    for clause in clauses:
        text = str(clause or "").casefold().strip()
        if not text or not add_ban.search(text):
            continue
        if any(term in text for term in signals):
            return True
    return False


def _requirement_covered(
    req: ScopeRequirement,
    corpus: str,
    *,
    paths_corpus: str = "",
    prohibited_corpus: str = "",
) -> bool:
    if req.kind in {"constraint", "files"}:
        # Constraints are preserved via prohibited_actions elsewhere; files need path hit.
        if req.kind == "files":
            return any(term in corpus for term in req.evidence_terms if term)
        return True
    signals = req.evidence_terms or _KIND_PLAN_SIGNALS.get(req.kind, ())
    structural = {
        "frontend",
        "backend",
        "database",
        "api",
        "tests",
        "migration",
        "integration",
    }
    if req.kind in structural:
        if _facet_explicitly_prohibited(req, prohibited_corpus):
            return False
        # Structural facets must be delivered via target_scope / action paths / validations —
        # purpose/acceptance prose alone is not enough (blocks schema-only FE/BE name-drops).
        if any(term.casefold() in paths_corpus for term in signals if term):
            return True
        return False
    return any(term.casefold() in corpus for term in signals if term)


def validate_objective_scope_coverage(
    *,
    original_objective: str,
    plan_steps: Iterable[Any],
) -> ScopeCoverageResult:
    """Reject plans that drop meaningful required scope from the original objective."""
    requirements = extract_scope_requirements(original_objective)
    # Only enforce structural product facets — not soft constraints/docs-only noise.
    enforceable = tuple(
        r
        for r in requirements
        if r.kind
        in {
            "frontend",
            "backend",
            "database",
            "api",
            "tests",
            "migration",
            "config",
            "integration",
            "files",
        }
    )
    paths_corpus, prose_corpus, prohibited_corpus, inspected = _plan_corpora(plan_steps)
    corpus = " ".join([paths_corpus, prose_corpus, prohibited_corpus])
    if not enforceable:
        return ScopeCoverageResult(
            ok=True,
            code="OBJECTIVE_SCOPE_OK",
            detail="no enforceable multi-facet scope requirements",
            original_objective=original_objective,
            plan_steps_inspected=inspected,
        )

    covered: list[ScopeRequirement] = []
    missing: list[ScopeRequirement] = []
    for req in enforceable:
        if _requirement_covered(
            req,
            corpus,
            paths_corpus=paths_corpus,
            prohibited_corpus=prohibited_corpus,
        ):
            covered.append(req)
        else:
            missing.append(req)

    if missing:
        return ScopeCoverageResult(
            ok=False,
            code="OBJECTIVE_SCOPE_NOT_COVERED",
            detail=(
                "accepted plan drops required scope from the original objective: "
                + ", ".join(f"{m.kind}({m.label})" for m in missing)
            ),
            missing=tuple(missing),
            covered=tuple(covered),
            original_objective=original_objective,
            plan_steps_inspected=inspected,
        )
    return ScopeCoverageResult(
        ok=True,
        code="OBJECTIVE_SCOPE_OK",
        detail="plan covers required objective scope",
        covered=tuple(covered),
        original_objective=original_objective,
        plan_steps_inspected=inspected,
    )


def selected_execution_lane(
    *,
    original_objective: str,
    plan_steps: Iterable[Any],
    demo_requested: bool,
) -> str:
    """Observability label for the lane the accepted plan should use."""
    if demo_requested and is_demo_compatible_page_objective(original_objective):
        corpus, _ = _plan_corpus(plan_steps)
        if "generate_html_css" in corpus or "proposed" in corpus:
            return "objective_cycle.real_execute.demo.v1"
    return "objective_cycle.real_execute.v1"


def render_plan_markdown(
    *,
    original_objective: str,
    understanding_summary: str,
    obligations: Iterable[Any],
    constraints: Iterable[str],
    plan: Any,
    execution_lane: str,
    warnings: Iterable[str] = (),
    coverage: ScopeCoverageResult | None = None,
) -> str:
    """Human-readable plan.md derived only from structured accepted plan + snapshots."""
    lines: list[str] = []
    lines.append("# AIX Accepted Plan")
    lines.append("")
    lines.append("## Original Objective")
    lines.append("")
    lines.append(str(original_objective or "").strip() or "_(empty)_")
    lines.append("")
    lines.append("## Derived Understanding")
    lines.append("")
    lines.append(str(understanding_summary or "").strip() or "_(none)_")
    lines.append("")
    lines.append("## Required Obligations")
    lines.append("")
    obs = list(obligations or ())
    if not obs:
        lines.append("- _(none)_")
    else:
        for o in obs:
            desc = getattr(o, "description", None) or (o.get("description") if isinstance(o, dict) else str(o))
            oid = getattr(o, "id", None) or (o.get("id") if isinstance(o, dict) else "")
            lines.append(f"- `{oid}`: {desc}")
    lines.append("")
    lines.append("## Constraints")
    lines.append("")
    cons = [str(c).strip() for c in (constraints or ()) if str(c).strip()]
    if not cons:
        lines.append("- _(none)_")
    else:
        for c in cons:
            lines.append(f"- {c}")
    lines.append("")
    lines.append("## Planned Steps")
    lines.append("")
    steps = list(getattr(plan, "steps", ()) or ())
    if not steps:
        lines.append("- _(none)_")
    for step in steps:
        sid = getattr(step, "id", "?")
        purpose = getattr(step, "purpose", "")
        lines.append(f"### Step `{sid}`")
        lines.append("")
        lines.append(f"- Purpose: {purpose}")
        scope = list(getattr(step, "target_scope", ()) or ())
        lines.append(f"- Target Scope: {', '.join(scope) if scope else '_(none)_'}")
        actions = list(getattr(step, "actions", ()) or ())
        if actions:
            rendered = [
                f"{getattr(a, 'capability', '')}.{getattr(a, 'operation', '')}" for a in actions
            ]
            lines.append(f"- Actions / Capabilities: {', '.join(rendered)}")
        else:
            lines.append("- Actions / Capabilities: _(none)_")
        criteria = list(getattr(step, "acceptance_criteria", ()) or ())
        lines.append("- Acceptance Criteria:")
        for c in criteria or ["_(none)_"]:
            lines.append(f"  - {c}")
        refs = list(getattr(step, "obligation_refs", ()) or ())
        lines.append(f"- Obligation Refs: {', '.join(refs) if refs else '_(none)_'}")
        lines.append("")
    lines.append("## Requirement Coverage")
    lines.append("")
    if coverage is None:
        lines.append("- _(not evaluated)_")
    else:
        lines.append(f"- Status: `{coverage.code}`")
        if coverage.covered:
            lines.append(
                "- Covered: "
                + ", ".join(f"{c.kind}" for c in coverage.covered)
            )
        if coverage.missing:
            lines.append(
                "- Missing: "
                + ", ".join(f"{m.kind}" for m in coverage.missing)
            )
    lines.append("")
    lines.append("## Selected Execution Lane")
    lines.append("")
    lines.append(f"`{execution_lane}`")
    lines.append("")
    lines.append("## Warnings")
    lines.append("")
    warns = [str(w).strip() for w in (warnings or ()) if str(w).strip()]
    if not warns:
        lines.append("- _(none)_")
    else:
        for w in warns:
            lines.append(f"- {w}")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "ScopeCoverageResult",
    "ScopeRequirement",
    "TaskMode",
    "classify_task_mode",
    "extract_scope_requirements",
    "is_demo_compatible_page_objective",
    "is_generic_meta_dod",
    "render_plan_markdown",
    "selected_execution_lane",
    "validate_objective_scope_coverage",
]
