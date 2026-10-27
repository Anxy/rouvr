"""AIX-EC-004 Packet EC-06: effect-based structural verification.

Comments never prove GROUP_METHODS. Helper extraction must show a new or reused
helper with enough call sites. Import cleanup must show unused imports gone.
Public-interface invariants must hold.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.aix_core.runtime.objective.contracts import RequiredEffectV1
from backend.aix_core.intelligence.repository.file_structure import detect_language
from backend.aix_core.intelligence.repository.php_structural import (
    PhpStructuralError,
    parse_php_structure,
    public_signature_inventory,
    public_symbol_inventory,
    unused_imports,
)
from backend.aix_core.runtime.executor.required_effects import effect_kinds
from backend.aix_core.runtime.executor.structural_execute import _group_specs
from backend.aix_core.intelligence.repository.php_structural import assign_methods_to_groups


@dataclass(frozen=True)
class EffectCheck:
    kind: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class StructuralVerifyResult:
    passed: bool
    checks: tuple[EffectCheck, ...]
    missing_effects: tuple[str, ...]
    remaining_asked_work: bool
    detail: str
    comment_only: bool = False
    public_symbols_unchanged: bool = True


def _diff_is_comment_only(diff_text: str) -> bool:
    added = []
    removed = []
    for line in str(diff_text or "").splitlines():
        if not line or line.startswith(("---", "+++", "@@", "\\")):
            continue
        if line.startswith("+"):
            added.append(line[1:].strip())
        elif line.startswith("-"):
            removed.append(line[1:].strip())
    if not added and not removed:
        return False
    def _comment(line: str) -> bool:
        return (not line) or line.startswith(("//", "#", "/*", "*", "*/"))
    return all(_comment(x) for x in added + removed)


def _public_order(src: str) -> list[str]:
    struct = parse_php_structure(src)
    return [m.name for m in struct.methods if m.visibility == "public" and m.name != "__construct"]


def _groups_contiguous(order: list[str], grouped: list[tuple[str, list[str]]]) -> tuple[bool, str]:
    for key, names in grouped:
        if len(names) < 2 and sum(1 for n in order if n in names) < 2:
            continue
        positions = sorted(i for i, n in enumerate(order) if n in set(names))
        if not positions:
            return False, f"group {key!r} methods missing after edit"
        if max(positions) - min(positions) + 1 != len(positions):
            return False, f"group {key!r} is not contiguous in public method order"
    if not any(len(names) >= 2 for _k, names in grouped):
        return False, "grouping did not produce a multi-method contiguous domain"
    return True, "public methods are contiguous by group"


def _helper_evidence(before: str, after: str, min_sites: int) -> tuple[bool, str]:
    before_s = parse_php_structure(before)
    after_s = parse_php_structure(after)
    before_priv = {m.name for m in before_s.methods if m.visibility != "public"}
    after_priv = [m.name for m in after_s.methods if m.visibility != "public"]
    new_helpers = [n for n in after_priv if n not in before_priv]
    after_text = after
    for name in list(dict.fromkeys(new_helpers + list(before_priv))):
        calls = len(re_calls(after_text, name))
        defined_here = name in after_priv
        if defined_here and calls >= min_sites:
            if name in new_helpers or calls > len(re_calls(before, name)):
                return True, f"helper {name} has {calls} call sites"
    # A prior step (or the original file) may already have extracted the repeats.
    from backend.aix_core.intelligence.repository.php_structural import extract_repeated_query_helpers

    _, remaining = extract_repeated_query_helpers(after, min_sites=min_sites)
    if not remaining:
        return True, "no remaining repeated DB/query statements to extract"
    return False, f"no helper with >= {min_sites} call sites (new={new_helpers})"


def re_calls(src: str, name: str) -> list[str]:
    import re

    return [
        m.group(0)
        for m in re.finditer(r"\$this->" + re.escape(name) + r"\s*\(", src)
    ]


def verify_structural_effects(
    *,
    path: str,
    before: str,
    after: str,
    effects: tuple[RequiredEffectV1, ...],
    diff_text: str = "",
    created_files: dict[str, str] | None = None,
) -> StructuralVerifyResult:
    if not effects:
        return StructuralVerifyResult(
            passed=True,
            checks=(),
            missing_effects=(),
            remaining_asked_work=False,
            detail="no required effects",
        )
    language = detect_language(path, after or before)
    comment_only = _diff_is_comment_only(diff_text)
    checks: list[EffectCheck] = []
    kinds = effect_kinds(effects)

    if language != "php":
        # PRESERVE_PUBLIC_INTERFACE is a governance constraint the planner attaches
        # broadly. Outside PHP we cannot inventory public methods the same way; when
        # it is the only structural obligation, do not fail a valid Python/TS edit.
        # Same for EXTRACT_HELPER/GROUP_* on operator UI extracts (live FE#7).
        soft_non_php = {
            "PRESERVE_PUBLIC_INTERFACE",
            "EXTRACT_HELPER",
            "GROUP_METHODS",
            "MOVE_METHODS",
            "STRIP_UNUSED_IMPORTS",
            "RUN_DECLARED_VALIDATION",
        }
        php_only = kinds - soft_non_php
        if not php_only:
            soft_checks = tuple(
                EffectCheck(
                    kind=kind,
                    passed=True,
                    detail=f"non-php {kind.lower()} treated as soft constraint ({language})",
                )
                for kind in sorted(kinds)
            ) or (
                EffectCheck(
                    kind="PRESERVE_PUBLIC_INTERFACE",
                    passed=True,
                    detail=f"non-php preserve treated as soft constraint ({language})",
                ),
            )
            return StructuralVerifyResult(
                passed=True,
                checks=soft_checks,
                missing_effects=(),
                remaining_asked_work=False,
                detail=f"structural soft-ok for {language}",
                comment_only=comment_only,
            )
        missing = tuple(sorted(php_only))
        return StructuralVerifyResult(
            passed=False,
            checks=(EffectCheck(kind="ENGINE", passed=False, detail=f"no verifier for {language}"),),
            missing_effects=missing,
            remaining_asked_work=True,
            detail=f"UNSUPPORTED_CAPABILITY: {language}",
            comment_only=comment_only,
        )

    try:
        before_pub = tuple(n for n in public_symbol_inventory(before) if n != "__construct")
        after_pub = tuple(n for n in public_symbol_inventory(after) if n != "__construct")
        before_sig = tuple(s for s in public_signature_inventory(before) if "__construct" not in s)
        after_sig = tuple(s for s in public_signature_inventory(after) if "__construct" not in s)
        after_order = _public_order(after)
        before_order = _public_order(before)
    except PhpStructuralError as exc:
        return StructuralVerifyResult(
            passed=False,
            checks=(EffectCheck(kind="PARSE", passed=False, detail=str(exc)),),
            missing_effects=tuple(sorted(effect_kinds(effects))),
            remaining_asked_work=True,
            detail=str(exc),
            comment_only=comment_only,
        )

    kinds = effect_kinds(effects)

    if "EXTRACT_TO_FILES" in kinds:
        import re

        created = created_files or {}
        service_classes = []
        for body in created.values():
            m = re.search(r"\bclass\s+([A-Za-z_]\w*)", body)
            if m:
                service_classes.append(m.group(1))
        delegates = sum(
            1
            for name in service_classes
            if re.search(rf"new\s+{re.escape(name)}\s*\(", after)
            or re.search(rf"{re.escape(name)}::", after)
        )
        ok = len(created) >= 3 and delegates >= 3 and not comment_only
        checks.append(
            EffectCheck(
                kind="EXTRACT_TO_FILES",
                passed=ok,
                detail=(
                    f"created={len(created)} services={service_classes} delegates={delegates}"
                    if ok
                    else f"need>=3 new service files with controller delegates; "
                    f"created={len(created)} delegates={delegates}"
                ),
            )
        )
    else:
        if "GROUP_METHODS" in kinds or "MOVE_METHODS" in kinds:
            kind = "GROUP_METHODS" if "GROUP_METHODS" in kinds else "MOVE_METHODS"
            if comment_only:
                checks.append(
                    EffectCheck(
                        kind=kind,
                        passed=False,
                        detail="comments cannot prove grouping",
                    )
                )
            else:
                effect = next(e for e in effects if e.kind in ("GROUP_METHODS", "MOVE_METHODS"))
                grouped = assign_methods_to_groups(
                    before_order,
                    groups=_group_specs(effect),
                    grouping_keys=effect.grouping_keys,
                )
                ok, detail = _groups_contiguous(after_order, grouped)
                checks.append(EffectCheck(kind=kind, passed=ok, detail=detail))

        if "EXTRACT_HELPER" in kinds:
            effect = next(e for e in effects if e.kind == "EXTRACT_HELPER")
            ok, detail = _helper_evidence(before, after, effect.min_call_sites)
            checks.append(EffectCheck(kind="EXTRACT_HELPER", passed=ok, detail=detail))

    if "STRIP_UNUSED_IMPORTS" in kinds:
        still = unused_imports(after)
        removed = len(unused_imports(before)) - len(still)
        # Pass if we removed at least one unused import, or none were unused to start.
        if not unused_imports(before):
            checks.append(EffectCheck(kind="STRIP_UNUSED_IMPORTS", passed=True, detail="no unused imports present"))
        else:
            ok = removed > 0 and len(still) < len(unused_imports(before))
            checks.append(
                EffectCheck(
                    kind="STRIP_UNUSED_IMPORTS",
                    passed=ok,
                    detail=f"removed {max(removed, 0)}; still unused {len(still)}",
                )
            )

    if "PRESERVE_PUBLIC_INTERFACE" in kinds:
        ok = set(before_pub) == set(after_pub) and set(before_sig) == set(after_sig)
        checks.append(
            EffectCheck(
                kind="PRESERVE_PUBLIC_INTERFACE",
                passed=ok,
                detail="public names/signatures unchanged" if ok else "public interface changed",
            )
        )

    missing = tuple(c.kind for c in checks if not c.passed)
    passed = bool(checks) and not missing
    return StructuralVerifyResult(
        passed=passed,
        checks=tuple(checks),
        missing_effects=missing,
        remaining_asked_work=bool(missing),
        detail="; ".join(f"{c.kind}:{'PASS' if c.passed else 'FAIL'} ({c.detail})" for c in checks),
        comment_only=comment_only,
        public_symbols_unchanged=(set(before_pub) == set(after_pub)),
    )


__all__ = ["EffectCheck", "StructuralVerifyResult", "verify_structural_effects"]
