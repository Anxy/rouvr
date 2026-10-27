"""Product-class objective detection for OutcomeContract enforcement.

Product-class = multi-facet application outcomes (UI + API + persistence), not a
single-file edit. Pure heuristics; no LLM.
"""
from __future__ import annotations

import re

_UI = re.compile(
    r"\b(frontend|ui|html|css|javascript|\.js\b|react|vue|dashboard|page|screens?)\b",
    re.I,
)
_API = re.compile(
    r"\b(api|backend|php|endpoint|rest|crud|router|service layer|fastapi|flask)\b",
    re.I,
)
_DB = re.compile(
    r"\b(database|mysql|postgres|sqlite|schema|\.sql\b|persistence|tables?)\b",
    re.I,
)
_FULLSTACK = re.compile(
    r"\b(full[- ]?stack|greenfield|mini[- ]?system|erp|saas|web app|application)\b",
    re.I,
)
_SINGLE_FILE = re.compile(
    r"\b(single file|one file|rename|typo|comment only|docs? only)\b",
    re.I,
)
_CREATE_VERB = re.compile(r"(?i)\b(build|create|generate|make|implement|scaffold)\b")
_NAMED_PRODUCT = re.compile(
    r"(?i)\b(?:a |an |the )?(?:complete |full[- ]?stack )?"
    r"([a-z][\w]+(?:[ -][a-z][\w]+){0,3})\s+"
    r"(?:web[ -]?app|webapp|application|mini[- ]?system)\b"
)
_NAMED_SYSTEM = re.compile(
    r"(?i)\b(?:a |an |the )?(?:complete )?"
    r"([a-z][\w]+(?:[ -][a-z][\w]+){0,2})\s+system\b"
)
_TITLE_PRODUCT = re.compile(r"\b([A-Z][a-z]+(?:[ -][A-Z][a-z]+)+)\b")
_SLUG_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "complete",
        "full",
        "stack",
        "fullstack",
        "greenfield",
        "using",
        "with",
        "and",
        "for",
        "new",
        "my",
        "our",
        "html",
        "css",
        "javascript",
        "python",
        "php",
        "mysql",
        "sqlite",
        "fastapi",
        "backend",
        "frontend",
    }
)


def infer_greenfield_product_slug(*, objective: str) -> str | None:
    """Delivery folder name under create_root for product-class CREATE/BUILD.

    Never invents SCOPE_IN. Used only to name proposed/<slug>/ when Intent
    omitted an explicit proposed/ path. Non-product objectives return None.
    """
    text = str(objective or "").strip()
    if not text or not is_product_class_objective(objective=text):
        return None
    if not _CREATE_VERB.search(text):
        return None
    named = re.search(r"(?i)\bproposed[/\\]([\w.-]+)\b", text)
    if named:
        slug = named.group(1).strip(".-").lower()
        if "." in slug:
            slug = slug.split(".", 1)[0]
        return slug or None
    match = _NAMED_PRODUCT.search(text) or _NAMED_SYSTEM.search(text)
    raw = match.group(1) if match else None
    if not raw:
        title = _TITLE_PRODUCT.search(text)
        raw = title.group(1) if title else None
    if not raw:
        return "web-app"
    parts = [
        part
        for part in re.split(r"[^a-z0-9]+", raw.casefold())
        if part and part not in _SLUG_STOP
    ]
    if not parts:
        return "web-app"
    slug = "-".join(parts)[:48].strip("-")
    if slug in {"app", "system", "application", "web", "backend", "frontend"}:
        return "web-app"
    return slug


def is_product_class_objective(
    *,
    objective: str,
    task_class: str = "",
) -> bool:
    """True when the objective implies a multi-facet product, not a patch."""
    text = str(objective or "")
    if not text.strip():
        return False
    if _SINGLE_FILE.search(text) and not _FULLSTACK.search(text):
        return False
    tc = str(task_class or "").casefold()
    if tc in {"docs_only_change", "verification_only", "source_edit", "code_change"}:
        return False
    facets = sum(bool(rx.search(text)) for rx in (_UI, _API, _DB))
    if facets >= 2:
        return True
    if _FULLSTACK.search(text) and facets >= 1:
        return True
    if _FULLSTACK.search(text) and ("create" in text.casefold() or "build" in text.casefold()):
        return True
    return False


def requires_outcome_contract(
    *,
    objective: str,
    task_class: str = "",
    settings: object | None = None,
    contract_already_present: bool = False,
) -> bool:
    """Whether plan accept must have a sealed OutcomeContractV1.

    - Explicit setting REQUIRE_OUTCOME_CONTRACT=True → always (except empty obj)
    - Product-class objectives → required by default
    - If a contract is already present → coverage always enforced (caller-side)
    """
    if contract_already_present:
        return True
    if settings is not None and bool(getattr(settings, "REQUIRE_OUTCOME_CONTRACT", False)):
        return True
    # Default enforcement for product-class (escape hatch only via non-product).
    return is_product_class_objective(objective=objective, task_class=task_class)


__all__ = [
    "infer_greenfield_product_slug",
    "is_product_class_objective",
    "requires_outcome_contract",
]
