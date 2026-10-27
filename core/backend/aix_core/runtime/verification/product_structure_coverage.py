"""AIX-SEM-001-W4 — Deterministic product_structure_coverage gate.

When a StepContract carries nonempty continuity refs, STEP_PASSED requires
this check. Fail-closed, token-based, no LLM judge.

Thresholds (v1):
- operation_refs: ALL tokens must appear for ≤20 ops; else majority
- entity_refs: each entity name or CREATE TABLE hint must appear
- UI (ops/maps on html/js/ts assets): api.php path token OR an op/route token
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

COVERAGE_HELPER_VERSION = "sem001.w4.v1"

_UI_SUFFIXES = (
    ".html",
    ".htm",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".vue",
)
_API_SUFFIXES = (".php", ".py", ".go", ".rs", ".java")
_DB_SUFFIXES = (".sql",)


@dataclass(frozen=True)
class ProductStructureCoverageResult:
    ok: bool
    code: str
    detail: str
    checked: bool
    missing_operation_refs: tuple[str, ...] = ()
    missing_entity_refs: tuple[str, ...] = ()
    ui_wiring_ok: bool | None = None
    required_operations: tuple[str, ...] = ()
    required_entities: tuple[str, ...] = ()
    helper_version: str = COVERAGE_HELPER_VERSION
    evidence: dict[str, Any] = field(default_factory=dict)


def _clean_refs(refs: Iterable[str] | None) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in refs or ():
        token = str(raw or "").strip()
        if not token:
            continue
        key = token.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return tuple(out)


def step_has_continuity_refs(step_contract: Any | None) -> bool:
    """True when Continuity Law Part C applies (nonempty refs)."""
    if step_contract is None:
        return False
    return bool(
        _clean_refs(getattr(step_contract, "operation_refs", ()) or ())
        or _clean_refs(getattr(step_contract, "entity_refs", ()) or ())
        or _clean_refs(getattr(step_contract, "mapping_refs", ()) or ())
        or _clean_refs(getattr(step_contract, "obligation_refs", ()) or ())
    )


def _norm_path(path: str) -> str:
    return str(path or "").replace("\\", "/").lstrip("./").strip()


def _corpus_from_files(files: dict[str, str] | None, *extra: str) -> str:
    parts: list[str] = []
    for path, body in (files or {}).items():
        parts.append(_norm_path(path))
        parts.append(str(body or ""))
    for chunk in extra:
        if chunk:
            parts.append(str(chunk))
    return "\n".join(parts)


def _op_variants(op: str) -> tuple[str, ...]:
    raw = str(op or "").strip()
    if not raw:
        return ()
    variants = [
        raw,
        raw.replace(".", "_"),
        raw.replace(".", "/"),
        raw.replace(".", "-"),
        raw.replace(".", ""),
    ]
    # route-ish: auth.login → /auth/login
    if "." in raw:
        variants.append("/" + raw.replace(".", "/"))
    # drop empties / dupes preserving order
    out: list[str] = []
    seen: set[str] = set()
    for v in variants:
        key = v.casefold()
        if not v or key in seen:
            continue
        seen.add(key)
        out.append(v)
    return tuple(out)


def _is_opaque_obligation_id(ref: str) -> bool:
    """True for minted ID refs (ULIDs) that carry no derivable content token.

    derive_obligations mints 26-char Crockford-base32 ULIDs; outcome-contract
    obligations use actionable obl_* tokens. Only the latter can ever match
    deliverable content by derivation.
    """
    return bool(re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{20,}", str(ref or "").strip().upper()))


def _token_present(corpus_cf: str, token: str) -> bool:
    for variant in _op_variants(token):
        if variant.casefold() in corpus_cf:
            return True
    return False


def _entity_present(corpus_cf: str, entity: str) -> bool:
    name = str(entity or "").strip()
    if not name:
        return True
    name_cf = name.casefold()
    if name_cf in corpus_cf:
        return True
    # CREATE TABLE customers / `customers` / "customers"
    patterns = (
        rf"create\s+table\s+[`\"\[]?{re.escape(name_cf)}[`\"\]]?",
        rf"create\s+table\s+if\s+not\s+exists\s+[`\"\[]?{re.escape(name_cf)}[`\"\]]?",
    )
    for pat in patterns:
        if re.search(pat, corpus_cf):
            return True
    return False


def _files_by_suffix(files: dict[str, str] | None, suffixes: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for path, body in (files or {}).items():
        p = _norm_path(path).casefold()
        if any(p.endswith(suf) for suf in suffixes):
            out[_norm_path(path)] = str(body or "")
    return out


def _ui_wiring_ok(corpus_cf: str, operation_refs: tuple[str, ...]) -> bool:
    if "api.php" in corpus_cf:
        return True
    if any(tok in corpus_cf for tok in ("fetch(", "axios.", "xmlhttprequest", "/api/")):
        # still require at least one bound op token if ops declared
        if not operation_refs:
            return True
        return any(_token_present(corpus_cf, op) for op in operation_refs)
    # op route tokens alone can satisfy when UI embeds them
    if operation_refs and any(_token_present(corpus_cf, op) for op in operation_refs):
        return True
    return False


def evaluate_product_structure_coverage(
    *,
    step_contract: Any | None = None,
    operation_refs: Iterable[str] | None = None,
    entity_refs: Iterable[str] | None = None,
    mapping_refs: Iterable[str] | None = None,
    obligation_refs: Iterable[str] | None = None,
    after_files: dict[str, str] | None = None,
    before_files: dict[str, str] | None = None,
    diff_text: str = "",
    target_scope: Iterable[str] | None = None,
) -> ProductStructureCoverageResult:
    """Deterministic coverage check against stamped refs + after content."""

    if step_contract is not None:
        ops = _clean_refs(
            operation_refs
            if operation_refs is not None
            else getattr(step_contract, "operation_refs", ()) or ()
        )
        ents = _clean_refs(
            entity_refs
            if entity_refs is not None
            else getattr(step_contract, "entity_refs", ()) or ()
        )
        maps = _clean_refs(
            mapping_refs
            if mapping_refs is not None
            else getattr(step_contract, "mapping_refs", ()) or ()
        )
        obls = _clean_refs(
            obligation_refs
            if obligation_refs is not None
            else getattr(step_contract, "obligation_refs", ()) or ()
        )
        scope = tuple(
            _norm_path(p)
            for p in (
                target_scope
                if target_scope is not None
                else getattr(step_contract, "target_scope", ()) or ()
            )
            if _norm_path(str(p))
        )
    else:
        ops = _clean_refs(operation_refs)
        ents = _clean_refs(entity_refs)
        maps = _clean_refs(mapping_refs)
        obls = _clean_refs(obligation_refs)
        scope = tuple(_norm_path(p) for p in (target_scope or ()) if _norm_path(str(p)))

    if not (ops or ents or maps or obls):
        return ProductStructureCoverageResult(
            ok=True,
            code="PRODUCT_STRUCTURE_COVERAGE_N_A",
            detail="empty continuity refs; legacy existence path allowed",
            checked=False,
        )

    # Prefer after-image; fall back to before for already-present greenfield files.
    files: dict[str, str] = {}
    for src in (before_files or {}, after_files or {}):
        for path, body in src.items():
            files[_norm_path(path)] = str(body or "")

    # Scope-named files with empty after still count if present in merged map.
    for path in scope:
        files.setdefault(path, files.get(path, ""))

    full_corpus = _corpus_from_files(files, diff_text)
    corpus_cf = full_corpus.casefold()

    api_files = _files_by_suffix(files, _API_SUFFIXES)
    db_files = _files_by_suffix(files, _DB_SUFFIXES)
    ui_files = _files_by_suffix(files, _UI_SUFFIXES)

    missing_ops: list[str] = []
    missing_ents: list[str] = []
    ui_ok: bool | None = None
    details: list[str] = []

    # API / operation coverage — prefer API file corpus when present.
    # UI-primary steps are exempt from API token matching; they use wiring checks.
    ops_failed = False
    scope_is_ui = any(_norm_path(p).casefold().endswith(_UI_SUFFIXES) for p in scope)
    scope_is_api = any(_norm_path(p).casefold().endswith(_API_SUFFIXES) for p in scope)
    scope_is_db = any(_norm_path(p).casefold().endswith(_DB_SUFFIXES) for p in scope)
    if ops and not (scope_is_ui and not scope_is_api and not scope_is_db):
        api_corpus = _corpus_from_files(api_files, diff_text) if api_files else full_corpus
        api_cf = api_corpus.casefold()
        missing_ops = [op for op in ops if not _token_present(api_cf, op)]
        n = len(ops)
        found = n - len(missing_ops)
        need = n if n <= 20 else max(1, math.ceil(n * 0.5))
        ops_failed = found < need
        if ops_failed:
            details.append(
                f"missing operations: {', '.join(missing_ops[:12])} "
                f"(found {found}/{n}, need {need})"
            )

    # DB / entity coverage
    ents_failed = False
    if ents and not (scope_is_ui and not scope_is_api and not scope_is_db):
        db_corpus = _corpus_from_files(db_files, diff_text) if db_files else full_corpus
        db_cf = db_corpus.casefold()
        missing_ents = [e for e in ents if not _entity_present(db_cf, e)]
        ents_failed = bool(missing_ents)
        if ents_failed:
            details.append("missing entities: " + ", ".join(missing_ents[:12]))

    # UI wiring when the step's primary deliverable looks like UI.
    ui_failed = False
    ui_refs = bool(ops or maps)
    if ui_refs and scope_is_ui and not scope_is_api and not scope_is_db:
        ui_corpus = _corpus_from_files(ui_files or files, diff_text)
        ui_ok = _ui_wiring_ok(ui_corpus.casefold(), ops)
        ui_failed = not ui_ok
        if ui_failed:
            details.append(
                "UI wiring missing: need api.php path token or fetch/axios/op route tokens"
            )
    elif maps and not ops and not ents and scope_is_ui:
        ui_corpus = _corpus_from_files(ui_files or files, diff_text)
        ui_ok = _ui_wiring_ok(ui_corpus.casefold(), ())
        ui_failed = not ui_ok
        if ui_failed:
            details.append("UI mapping step missing api.php / client wiring signal")

    # Obligation-only refs with no ops/ents/maps: fail closed unless corpus mentions
    # derived actionable tokens (obl_api_auth_login → auth.login / auth_login).
    # DoD-derived obligation IDs are opaque ULIDs (derive_obligations), not
    # actionable obl_* tokens: no derivable content token can ever match them,
    # so requiring derived hits would fail every such step unconditionally.
    # Enforce derived-token matching only on refs that can carry tokens;
    # opaque ID refs stay continuity-bearing (step_has_continuity_refs still
    # sees them) and their semantic satisfaction is enforced by
    # objective_coverage + world-state obligation coverage instead.
    obl_failed = False
    if obls and not ops and not ents and not maps:
        enforceable = [o for o in obls if not _is_opaque_obligation_id(o)]
        derived_hits = 0
        for obl in enforceable:
            stem = obl
            for prefix in ("obl_api_", "obl_db_", "obl_ui_", "obl_map_"):
                if stem.casefold().startswith(prefix):
                    stem = stem[len(prefix) :]
                    break
            token = stem.replace("__", ".").replace("_", ".")
            if _token_present(corpus_cf, token) or stem.casefold() in corpus_cf:
                derived_hits += 1
        if enforceable and derived_hits == 0:
            obl_failed = True
            details.append("obligation_refs present but no derived tokens found in content")

    ok = not (ops_failed or ents_failed or ui_failed or obl_failed)
    code = (
        "PRODUCT_STRUCTURE_COVERAGE_PASSED"
        if ok
        else "PRODUCT_STRUCTURE_COVERAGE_FAILED"
    )
    detail = (
        "product structure refs covered by deliverable content"
        if ok
        else "; ".join(details) or "product structure coverage failed"
    )
    return ProductStructureCoverageResult(
        ok=ok,
        code=code,
        detail=detail,
        checked=True,
        missing_operation_refs=tuple(missing_ops),
        missing_entity_refs=tuple(missing_ents),
        ui_wiring_ok=ui_ok,
        required_operations=ops,
        required_entities=ents,
        evidence={
            "api_files": sorted(api_files.keys()),
            "db_files": sorted(db_files.keys()),
            "ui_files": sorted(ui_files.keys()),
            "mapping_refs": list(maps),
            "obligation_refs": list(obls),
        },
    )


__all__ = [
    "COVERAGE_HELPER_VERSION",
    "ProductStructureCoverageResult",
    "evaluate_product_structure_coverage",
    "step_has_continuity_refs",
]
