"""AIX-EC-004 Packet EC-05: apply typed required effects via structural operations."""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.aix_core.runtime.objective.contracts import RequiredEffectV1
from backend.aix_core.intelligence.repository.file_structure import detect_language
from backend.aix_core.intelligence.repository.php_structural import (
    PhpStructuralError,
    extract_methods_to_service_files,
    extract_repeated_query_helpers,
    group_public_methods,
    public_signature_inventory,
    public_symbol_inventory,
    remove_unused_imports,
    reorder_methods,
)
from backend.aix_core.runtime.executor.required_effects import effect_kinds


@dataclass
class StructuralExecuteResult:
    content: str
    applied_effects: list[str] = field(default_factory=list)
    remaining_effects: list[str] = field(default_factory=list)
    remaining_asked_work: bool = False
    detail: str = ""
    groups: list[tuple[str, list[str]]] = field(default_factory=list)
    helpers_created: list[str] = field(default_factory=list)
    imports_removed: int = 0
    before_public: tuple[str, ...] = ()
    after_public: tuple[str, ...] = ()
    before_signatures: tuple[str, ...] = ()
    after_signatures: tuple[str, ...] = ()
    unsupported: bool = False
    unsupported_detail: str = ""
    # EXTRACT_TO_FILES: sibling service files keyed by repo-relative path.
    created_files: dict[str, str] = field(default_factory=dict)
    created_services: list[str] = field(default_factory=list)


def _group_specs(effect: RequiredEffectV1) -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    specs = [(g.key, g.methods, g.match_tokens) for g in effect.groups]
    if specs:
        return specs
    return [(key, (), ()) for key in effect.grouping_keys]


def apply_php_effects(
    content: str,
    effects: tuple[RequiredEffectV1, ...],
    *,
    source_path: str = "",
) -> StructuralExecuteResult:
    kinds = effect_kinds(effects)
    before_public = public_symbol_inventory(content)
    before_sigs = public_signature_inventory(content)
    updated = content
    applied: list[str] = []
    remaining: list[str] = []
    groups: list[tuple[str, list[str]]] = []
    helpers: list[str] = []
    imports_removed = 0
    notes: list[str] = []
    created_files: dict[str, str] = {}
    created_services: list[str] = []

    try:
        if "EXTRACT_TO_FILES" in kinds:
            effect = next(e for e in effects if e.kind == "EXTRACT_TO_FILES")
            if not source_path:
                raise PhpStructuralError("EXTRACT_TO_FILES_NO_PATH", "source_path required")
            updated, created_files, created_services = extract_methods_to_service_files(
                updated,
                source_path=source_path,
                grouping_keys=effect.grouping_keys,
                groups=_group_specs(effect),
                min_groups=3,
            )
            notes.append(
                f"EXTRACT_TO_FILES created {len(created_files)} services: {created_services}"
            )
            applied.append("EXTRACT_TO_FILES")
            # In-file helper extraction is a weaker substitute; skip when splitting files.
        else:
            if "GROUP_METHODS" in kinds or "MOVE_METHODS" in kinds:
                effect = next(e for e in effects if e.kind in ("GROUP_METHODS", "MOVE_METHODS"))
                updated, groups = group_public_methods(
                    updated,
                    groups=_group_specs(effect),
                    grouping_keys=effect.grouping_keys,
                )
                notes.append(f"{effect.kind} reordered {sum(len(n) for _, n in groups)} public methods")
                applied.append(effect.kind)

            if "EXTRACT_HELPER" in kinds:
                effect = next(e for e in effects if e.kind == "EXTRACT_HELPER")
                before_extract = updated
                updated, helpers = extract_repeated_query_helpers(updated, min_sites=effect.min_call_sites)
                notes.append(f"EXTRACT_HELPER {helpers or 'none'}")
                if helpers and updated != before_extract:
                    applied.append("EXTRACT_HELPER")
                else:
                    notes.append("EXTRACT_HELPER already satisfied or no remaining duplicates")
                    applied.append("EXTRACT_HELPER")

        if "STRIP_UNUSED_IMPORTS" in kinds:
            updated, imports_removed = remove_unused_imports(updated)
            notes.append(f"removed {imports_removed} unused import(s)")
            applied.append("STRIP_UNUSED_IMPORTS")

        after_public = public_symbol_inventory(updated)
        after_sigs = public_signature_inventory(updated)
        if "PRESERVE_PUBLIC_INTERFACE" in kinds:
            if set(after_public) != set(before_public) or set(after_sigs) != set(before_sigs):
                remaining.append("PRESERVE_PUBLIC_INTERFACE")
                notes.append("public interface changed during structural apply")
            else:
                applied.append("PRESERVE_PUBLIC_INTERFACE")

        if "RUN_DECLARED_VALIDATION" in kinds:
            applied.append("RUN_DECLARED_VALIDATION")

    except PhpStructuralError as exc:
        return StructuralExecuteResult(
            content=content,
            remaining_effects=sorted(kinds),
            remaining_asked_work=True,
            unsupported=True,
            unsupported_detail=f"{exc.code}:{exc.detail}",
            before_public=before_public,
            before_signatures=before_sigs,
            detail=str(exc),
        )

    mutation_remaining = [
        k for k in remaining if k not in {"PRESERVE_PUBLIC_INTERFACE", "RUN_DECLARED_VALIDATION"}
    ]
    if "EXTRACT_TO_FILES" in kinds and len(created_files) < 3:
        mutation_remaining.append("EXTRACT_TO_FILES")
    return StructuralExecuteResult(
        content=updated,
        applied_effects=applied,
        remaining_effects=remaining,
        remaining_asked_work=bool(mutation_remaining),
        detail="; ".join(notes) or "no structural change",
        groups=groups,
        helpers_created=helpers,
        imports_removed=imports_removed,
        before_public=before_public,
        after_public=after_public,
        before_signatures=before_sigs,
        after_signatures=after_sigs,
        created_files=created_files,
        created_services=created_services,
    )


def apply_structural_effects(
    *,
    path: str,
    content: str,
    effects: tuple[RequiredEffectV1, ...],
) -> StructuralExecuteResult:
    language = detect_language(path, content)
    if language != "php":
        return StructuralExecuteResult(
            content=content,
            remaining_effects=sorted(effect_kinds(effects)),
            remaining_asked_work=True,
            unsupported=True,
            unsupported_detail=f"UNSUPPORTED_CAPABILITY: structural ops not available for {language}",
            detail=f"no structural engine for {language}",
        )
    if not effects:
        return StructuralExecuteResult(content=content, detail="no required effects")
    return apply_php_effects(content, effects, source_path=path)


__all__ = ["StructuralExecuteResult", "apply_structural_effects", "reorder_methods"]
