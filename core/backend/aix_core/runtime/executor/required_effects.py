"""AIX-EC-004 Packet EC-01: typed required effects for a StepContract.

Planner JSON is the primary source. When the model omits `required_effects`, a
conservative inferencer maps obligation/purpose *once* onto RequiredEffectV1 so
dispatch and verification never re-scan keywords from the live objective string.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from backend.aix_core.runtime.objective.contracts import (
    MethodGroupSpecV1,
    ObjectiveObligationV1,
    RequiredEffectKind,
    RequiredEffectV1,
    StepContractV1,
)

_KIND_ALIASES: dict[str, RequiredEffectKind] = {
    "GROUP_METHODS": "GROUP_METHODS",
    "GROUP": "GROUP_METHODS",
    "GROUPING": "GROUP_METHODS",
    "MOVE_METHODS": "MOVE_METHODS",
    "MOVE": "MOVE_METHODS",
    "REORDER": "MOVE_METHODS",
    "EXTRACT_HELPER": "EXTRACT_HELPER",
    "EXTRACT": "EXTRACT_HELPER",
    "HELPER": "EXTRACT_HELPER",
    "EXTRACT_TO_FILES": "EXTRACT_TO_FILES",
    "EXTRACT_TO_FILE": "EXTRACT_TO_FILES",
    "EXTRACT_TO_CLASSES": "EXTRACT_TO_FILES",
    "EXTRACT_TO_SERVICES": "EXTRACT_TO_FILES",
    "SPLIT_TO_FILES": "EXTRACT_TO_FILES",
    "SPLIT_TO_CLASSES": "EXTRACT_TO_FILES",
    "STRIP_UNUSED_IMPORTS": "STRIP_UNUSED_IMPORTS",
    "UNUSED_IMPORTS": "STRIP_UNUSED_IMPORTS",
    "IMPORTS": "STRIP_UNUSED_IMPORTS",
    "LITERAL_REPLACE": "LITERAL_REPLACE",
    "PRESERVE_PUBLIC_INTERFACE": "PRESERVE_PUBLIC_INTERFACE",
    "PRESERVE": "PRESERVE_PUBLIC_INTERFACE",
    "RUN_DECLARED_VALIDATION": "RUN_DECLARED_VALIDATION",
    "VALIDATE": "RUN_DECLARED_VALIDATION",
}

_GROUP_KEYS_RE = re.compile(
    r"(?:grouped?\s+(?:as|by|under|into)|domains?)\s*[:\(]\s*([^)\n]{3,240})",
    re.IGNORECASE,
)
_PAREN_LIST_RE = re.compile(r"\(([^)]{3,240})\)")
_EXAMPLE_LIST_RE = re.compile(
    r"(?:for example|e\.g\.|eg\.|such as)\s+([^.\n]{8,400})",
    re.IGNORECASE,
)
_CAMEL_RE = re.compile(r"[A-Z][a-z]+|[a-z]{4,}")

_ARCHITECTURAL_EXTRACT_TOKENS = (
    "service class",
    "service classes",
    "layered architecture",
    "thin orchestrator",
    "domain-focused",
    "domain focused",
    "into those services",
    "into dedicated",
    "into a dedicated",
    "separated responsibilities",
    "extract at least",
    "new class",
    "new classes",
    "real layered",
)


def _as_kind(raw: str) -> RequiredEffectKind | None:
    key = re.sub(r"[^A-Z_]", "", str(raw or "").upper().replace(" ", "_"))
    return _KIND_ALIASES.get(key)


def _split_list(raw: str) -> tuple[str, ...]:
    parts = [p.strip() for p in re.split(r"\s*,\s*", str(raw or "")) if p.strip()]
    cleaned: list[str] = []
    for part in parts:
        part = re.sub(r"\s*\(\d+\)\s*$", "", part).strip()
        if part:
            cleaned.append(part)
    return tuple(cleaned)


def parse_grouping_keys(*texts: str) -> tuple[str, ...]:
    for text in texts:
        match = _GROUP_KEYS_RE.search(str(text or ""))
        if match:
            keys = _split_list(match.group(1))
            if keys:
                return keys
        example = _EXAMPLE_LIST_RE.search(str(text or ""))
        if example:
            raw = example.group(1)
            # "(for example a, b, and c), rest of sentence" — keep only the example list.
            if ")" in raw:
                raw = raw.split(")", 1)[0]
            raw = re.sub(r"\band\b", ",", raw, flags=re.IGNORECASE)
            keys = _split_list(raw)
            cleaned: list[str] = []
            _IMPERATIVE = (
                "move", "keep", "update", "run", "do", "create", "ensure", "preserve",
                "including", "while", "then", "also",
            )
            for key in keys:
                key = key.strip(" .;:")
                key = re.sub(r"[)]+$", "", key).strip()
                first = key.casefold().split(None, 1)[0] if key else ""
                if first in _IMPERATIVE:
                    continue
                if key and len(key) >= 3 and len(key) <= 80:
                    cleaned.append(key)
            if len(cleaned) >= 2:
                return tuple(cleaned[:8])
        paren = _PAREN_LIST_RE.search(str(text or ""))
        if paren and any(tok in str(text or "").casefold() for tok in ("for example", "e.g", "domains")):
            keys = _split_list(paren.group(1))
            keys = tuple(k.strip(" .;:)") for k in keys if k.strip())
            if len(keys) >= 2:
                return keys[:8]
    return ()


def is_architectural_extract_objective(*texts: str) -> bool:
    blob = "\n".join(str(t or "") for t in texts).casefold()
    if any(tok in blob for tok in _ARCHITECTURAL_EXTRACT_TOKENS):
        return True
    if re.search(r"move .{0,60}into .{0,40}service", blob):
        return True
    if re.search(r"(extract|split)\s+.{0,80}into .{0,40}(service|class|file)", blob):
        return True
    if "god class" in blob or "god-class" in blob:
        return True
    return False


_CLEANUP_TOKENS = (
    "without changing behavior",
    "without changing existing",
    "without changing behaviour",
    "preserve behavior",
    "preserving behavior",
    "preserve behaviour",
    "behavior-preserving",
    "behaviour-preserving",
    "nonfunctional",
    "non-functional",
    "non functional",
    "formatting",
    "import cleanup",
    "import organisation",
    "import organization",
    "project standards",
    "coding standards",
    "code standards",
)


def is_behavior_preserving_cleanup_objective(*texts: str) -> bool:
    """True for in-file standards/import/format cleanup that must not split files.

    Architectural god-class splits remain EXTRACT_TO_FILES even when they also
    say "preserve public signatures".
    """
    if is_architectural_extract_objective(*texts):
        return False
    blob = "\n".join(str(t or "") for t in texts).casefold()
    return any(tok in blob for tok in _CLEANUP_TOKENS)


def parse_required_effects(raw: Any) -> tuple[RequiredEffectV1, ...]:
    """Accept planner JSON: a list of strings or objects."""
    if not raw:
        return ()
    items = raw if isinstance(raw, list) else [raw]
    out: list[RequiredEffectV1] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            kind = _as_kind(item)
            if kind and kind not in seen:
                seen.add(kind)
                out.append(RequiredEffectV1(kind=kind))
            continue
        if not isinstance(item, dict):
            continue
        kind = _as_kind(str(item.get("kind") or item.get("effect") or ""))
        if not kind or kind in seen:
            continue
        seen.add(kind)
        groups: list[MethodGroupSpecV1] = []
        for g in item.get("groups") or []:
            if not isinstance(g, dict):
                continue
            key = str(g.get("key") or g.get("name") or "").strip()
            if not key:
                continue
            methods = tuple(str(m).strip() for m in (g.get("methods") or []) if str(m).strip())
            tokens = tuple(str(t).strip() for t in (g.get("match_tokens") or []) if str(t).strip())
            groups.append(MethodGroupSpecV1(key=key, methods=methods, match_tokens=tokens))
        keys = tuple(str(k).strip() for k in (item.get("grouping_keys") or []) if str(k).strip())
        if not keys and groups:
            keys = tuple(g.key for g in groups)
        min_sites = item.get("min_call_sites", 2)
        try:
            min_call_sites = max(2, int(min_sites))
        except (TypeError, ValueError):
            min_call_sites = 2
        helper = str(item.get("helper_name") or "").strip() or None
        out.append(
            RequiredEffectV1(
                kind=kind,
                groups=tuple(groups),
                grouping_keys=keys,
                min_call_sites=min_call_sites,
                helper_name=helper,
            )
        )
    return tuple(out)


def _text_blob(
    purpose: str,
    acceptance: Iterable[str],
    obligations: Iterable[ObjectiveObligationV1],
) -> str:
    parts = [purpose, *acceptance, *(o.description for o in obligations)]
    return "\n".join(str(p) for p in parts if str(p).strip())


def infer_required_effects(
    *,
    purpose: str,
    acceptance_criteria: Iterable[str] = (),
    obligations: Iterable[ObjectiveObligationV1] = (),
    validation_commands: Iterable[str] = (),
) -> tuple[RequiredEffectV1, ...]:
    """Keyword hints only. Never authoritative over planner-emitted effects.

    HTTP verbs, 'boilerplate', 'helper endpoint', and comment cleanup must not
    independently select EXTRACT_HELPER or deletion semantics.
    """
    blob = _text_blob(purpose, acceptance_criteria, obligations).casefold()
    if any(tok in blob for tok in ("delete endpoint", "change delete", "http delete")):
        blob = blob.replace("delete", "http-delete")
    if any(tok in blob for tok in ("without extracting", "not extract", "do not extract")):
        blob = blob.replace("extract", "leave-inline")
    if "boilerplate" in blob and "extract helper" not in blob:
        blob = blob.replace("boilerplate", "dead-comments")
    if "helper endpoint" in blob:
        blob = blob.replace("helper endpoint", "endpoint")
    out: list[RequiredEffectV1] = []
    texts = (purpose, *acceptance_criteria, *(o.description for o in obligations))
    grouping_keys = parse_grouping_keys(*texts)

    architectural = is_architectural_extract_objective(*texts)
    cleanup = is_behavior_preserving_cleanup_objective(*texts)
    if architectural:
        out.append(
            RequiredEffectV1(
                kind="EXTRACT_TO_FILES",
                grouping_keys=grouping_keys,
                groups=tuple(
                    MethodGroupSpecV1(key=k, methods=(), match_tokens=()) for k in grouping_keys
                ),
            )
        )
        out.append(RequiredEffectV1(kind="PRESERVE_PUBLIC_INTERFACE"))
    elif cleanup:
        out.append(RequiredEffectV1(kind="STRIP_UNUSED_IMPORTS"))
        out.append(RequiredEffectV1(kind="PRESERVE_PUBLIC_INTERFACE"))
    else:
        if any(tok in blob for tok in ("group ", "grouped", "regroup", "reorganize", "organise", "organize by")):
            out.append(RequiredEffectV1(kind="GROUP_METHODS", grouping_keys=grouping_keys))
        elif any(tok in blob for tok in ("move method", "reorder method", "method order")):
            out.append(RequiredEffectV1(kind="MOVE_METHODS", grouping_keys=grouping_keys))

        if any(tok in blob for tok in ("extract", "boilerplate", "helper method", "helper methods", "repeated")):
            # FE shared panel/component factoring is not PHP EXTRACT_HELPER (live FE#7).
            if not any(
                tok in blob
                for tok in (
                    "operator",
                    "panels",
                    "empty-state",
                    "empty state",
                    "reusable helpers or components",
                    "fetch, error",
                    "loading, error",
                )
            ):
                out.append(RequiredEffectV1(kind="EXTRACT_HELPER", min_call_sites=2))

    if any(tok in blob for tok in ("unused import", "unused use-import", "unused use import")):
        out.append(RequiredEffectV1(kind="STRIP_UNUSED_IMPORTS"))

    if not architectural and any(
        tok in blob
        for tok in (
            "public method name",
            "public names",
            "preserve all public",
            "do not change public",
            "signature",
            "middleware",
            "http/middleware",
        )
    ):
        out.append(RequiredEffectV1(kind="PRESERVE_PUBLIC_INTERFACE"))

    if any(str(c).strip() for c in validation_commands):
        out.append(RequiredEffectV1(kind="RUN_DECLARED_VALIDATION"))

    return tuple(out)


def merge_required_effects(*groups: tuple[RequiredEffectV1, ...]) -> tuple[RequiredEffectV1, ...]:
    """Union by kind. Prefer the first effect that already has grouping specs."""
    by_kind: dict[str, RequiredEffectV1] = {}
    for group in groups:
        for effect in group:
            existing = by_kind.get(effect.kind)
            if existing is None:
                by_kind[effect.kind] = effect
                continue
            if effect.kind == "GROUP_METHODS" and not existing.groups and not existing.grouping_keys:
                if effect.groups or effect.grouping_keys:
                    by_kind[effect.kind] = effect
    return tuple(by_kind.values())


def _step_scope_is_python_only(step: StepContractV1) -> bool:
    """True when every scoped path is a Python source file (not a directory)."""
    files = [
        str(p).replace("\\", "/").strip()
        for p in (step.target_scope or ())
        if str(p).strip() and not str(p).endswith("/")
    ]
    if not files:
        return False
    return all(f.endswith(".py") for f in files)


def effects_for_step(
    step: StepContractV1 | None,
    *,
    obligations: Iterable[ObjectiveObligationV1] = (),
) -> tuple[RequiredEffectV1, ...]:
    if step is None:
        return ()
    inferred = infer_required_effects(
        purpose=step.purpose,
        acceptance_criteria=step.acceptance_criteria,
        obligations=obligations,
        validation_commands=step.validation_commands,
    )
    merged = merge_required_effects(step.required_effects, inferred)
    sanitized = sanitize_required_effects(
        merged,
        step.purpose,
        *step.acceptance_criteria,
        *(o.description for o in obligations),
    )
    # EXTRACT_TO_FILES is PHP-only (structural_execute). Python service delegation
    # refactors must use patch/codegen (LITERAL_REPLACE), not structural split.
    if _step_scope_is_python_only(step):
        sanitized = tuple(e for e in sanitized if e.kind != "EXTRACT_TO_FILES")
    return sanitized


def sanitize_required_effects(
    effects: Iterable[RequiredEffectV1],
    *texts: str,
) -> tuple[RequiredEffectV1, ...]:
    """Planner JSON cannot force EXTRACT_TO_FILES on a cleanup-only objective."""
    kept = tuple(effects)
    if is_architectural_extract_objective(*texts):
        return kept
    if is_behavior_preserving_cleanup_objective(*texts):
        return tuple(e for e in kept if e.kind != "EXTRACT_TO_FILES")
    return kept


def effect_kinds(effects: Iterable[RequiredEffectV1]) -> frozenset[str]:
    return frozenset(e.kind for e in effects)


def attach_effects(step: StepContractV1, effects: tuple[RequiredEffectV1, ...]) -> StepContractV1:
    merged = merge_required_effects(step.required_effects, effects)
    if merged == step.required_effects:
        return step
    data = step.model_dump()
    data["required_effects"] = [e.model_dump() for e in merged]
    return StepContractV1.model_validate(data)


def grouping_tokens(key: str) -> tuple[str, ...]:
    parts = re.split(r"[/\s_\-]+", str(key or ""))
    tokens: list[str] = []
    for part in parts:
        part = part.strip()
        if len(part) >= 3:
            tokens.append(part.casefold())
            # Soft singularize so "notifications" matches Notification* methods.
            if part.casefold().endswith("s") and len(part) > 4:
                tokens.append(part.casefold()[:-1])
        tokens.extend(m.group(0).casefold() for m in _CAMEL_RE.finditer(part) if len(m.group(0)) >= 3)
    return tuple(dict.fromkeys(tokens))


__all__ = [
    "attach_effects",
    "effect_kinds",
    "effects_for_step",
    "grouping_tokens",
    "infer_required_effects",
    "is_architectural_extract_objective",
    "is_behavior_preserving_cleanup_objective",
    "merge_required_effects",
    "parse_grouping_keys",
    "parse_required_effects",
    "sanitize_required_effects",
]
