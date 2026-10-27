"""AIX-SEM-001-W1 — Prompt continuity for amnesiac step execution.

Pure helper: serialize StepContract semantic refs into actionable prompt tokens.
No I/O. No discovery. No gates.

Continuity Law (A): nonempty operation_refs / entity_refs / mapping_refs /
obligation_refs must yield human-readable tokens in derived_execution_prompt —
never opaque obl_* / map-* IDs alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CONTINUITY_HELPER_VERSION = "sem001.w1.v1"


@dataclass(frozen=True)
class ContinuityPromptSection:
    """Result of building the continuity block for an executor prompt."""

    section_text: str
    actionable_tokens: tuple[str, ...]
    operation_tokens: tuple[str, ...]
    entity_tokens: tuple[str, ...]
    mapping_ids: tuple[str, ...]
    derived_from_obligations: tuple[str, ...]
    helper_version: str = CONTINUITY_HELPER_VERSION

    @property
    def has_content(self) -> bool:
        return bool(self.section_text.strip()) and bool(self.actionable_tokens)


def _norm_token(value: str) -> str:
    return str(value or "").strip()


def _unique(seq: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        key = item.casefold()
        if not item or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return tuple(out)


def derive_tokens_from_obligation_ref(obligation_ref: str) -> tuple[str, ...]:
    """Map obl_* ids to actionable tokens. Opaque leftovers yield empty."""

    raw = _norm_token(obligation_ref)
    if not raw:
        return ()
    lower = raw.casefold()
    if lower.startswith("obl_map_") or lower.startswith("obl_map-"):
        return ()
    if lower.startswith("obl_api_"):
        rest = raw[8:]
        if not rest:
            return ()
        # auth_login -> auth.login ; products_list -> products.list
        if "_" in rest:
            head, tail = rest.split("_", 1)
            return (f"{head}.{tail.replace('_', '.')}",)
        return (rest,)
    if lower.startswith("obl_db_"):
        rest = raw[7:]
        return (rest,) if rest else ()
    if lower.startswith("obl_ui_"):
        rest = raw[7:]
        if not rest:
            return ()
        if "_" in rest:
            # list_products -> products.list when verb_noun; login -> login
            parts = rest.split("_")
            if len(parts) >= 2 and parts[0] in {
                "list",
                "create",
                "update",
                "delete",
                "view",
                "adjust",
            }:
                verb, noun = parts[0], "_".join(parts[1:])
                return (f"{noun}.{verb}",)
            return (".".join(parts),)
        return (rest,)
    return ()


def actionable_tokens_from_refs(
    *,
    operation_refs: tuple[str, ...] = (),
    entity_refs: tuple[str, ...] = (),
    mapping_refs: tuple[str, ...] = (),
    obligation_refs: tuple[str, ...] = (),
) -> ContinuityPromptSection:
    """Build continuity section from raw ref tuples (testable without StepContract)."""

    ops = [_norm_token(x) for x in operation_refs]
    entities = [_norm_token(x) for x in entity_refs]
    mappings = [_norm_token(x) for x in mapping_refs]

    derived: list[str] = []
    for obl in obligation_refs:
        derived.extend(derive_tokens_from_obligation_ref(obl))

    # If ops/entities missing, promote derived obligation tokens into those buckets
    # using simple heuristics (dotted => operation-like; else entity-like).
    if not any(ops) and not any(entities) and derived:
        for tok in derived:
            if "." in tok:
                ops.append(tok)
            else:
                entities.append(tok)

    op_tokens = _unique([x for x in ops if x])
    entity_tokens = _unique([x for x in entities if x])
    mapping_ids = _unique([x for x in mappings if x])
    derived_tokens = _unique(derived)

    actionable = _unique([*op_tokens, *entity_tokens])
    # Mappings alone are opaque — never actionable by themselves.
    if not actionable:
        return ContinuityPromptSection(
            section_text="",
            actionable_tokens=(),
            operation_tokens=(),
            entity_tokens=(),
            mapping_ids=mapping_ids,
            derived_from_obligations=derived_tokens,
        )

    lines: list[str] = ["Continuity contract (must honor):"]
    if op_tokens:
        lines.append(
            "Must implement operations: " + ", ".join(op_tokens)
        )
    if entity_tokens:
        lines.append(
            "Must persist / align entities: " + ", ".join(entity_tokens)
        )
    if mapping_ids and actionable:
        # Context only — never the sole content
        lines.append(
            "Related mapping ids (context): " + ", ".join(mapping_ids)
        )
    lines.append(
        "Do not invent a disjoint interface; honor the tokens above."
    )

    return ContinuityPromptSection(
        section_text="\n".join(lines),
        actionable_tokens=actionable,
        operation_tokens=op_tokens,
        entity_tokens=entity_tokens,
        mapping_ids=mapping_ids,
        derived_from_obligations=derived_tokens,
    )


def build_continuity_prompt_section(step_contract: Any | None) -> ContinuityPromptSection:
    """Extract refs from a StepContract-like object and build the prompt section."""

    if step_contract is None:
        return ContinuityPromptSection(
            section_text="",
            actionable_tokens=(),
            operation_tokens=(),
            entity_tokens=(),
            mapping_ids=(),
            derived_from_obligations=(),
        )

    def _as_tuple(name: str) -> tuple[str, ...]:
        raw = getattr(step_contract, name, ()) or ()
        return tuple(str(x) for x in raw if str(x).strip())

    return actionable_tokens_from_refs(
        operation_refs=_as_tuple("operation_refs"),
        entity_refs=_as_tuple("entity_refs"),
        mapping_refs=_as_tuple("mapping_refs"),
        obligation_refs=_as_tuple("obligation_refs"),
    )


__all__ = [
    "CONTINUITY_HELPER_VERSION",
    "ContinuityPromptSection",
    "actionable_tokens_from_refs",
    "build_continuity_prompt_section",
    "derive_tokens_from_obligation_ref",
]
