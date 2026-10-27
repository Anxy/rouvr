"""AIX-PA-002: bounded planner-context packing before Integration ingress.

Priority order (keep higher ranks; compact/drop lower ranks first):

1. objective + obligations
2. relevant symbols
3. target file outlines / files_read
4. relevant excerpts
5. related tests/routes (constraints / extra files)
6. optional context (executor_profile, prior failure, observed_problem)

Does not invent an unbounded cap. The Integration planner ingress default is 8000
tokens with a hard max of 12000.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

DEFAULT_PLANNER_INPUT_TOKEN_CAP = 7500
HARD_MAX_PLANNER_INPUT_TOKEN_CAP = 12000
HARD_MIN_PLANNER_INPUT_TOKEN_CAP = 1500

OPTIONAL_KEYS = ("executor_profile", "prior_failure_evidence", "observed_problem")


def estimate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    byte_count = len(str(text or "").encode("utf-8"))
    return max(1, byte_count // 4) if byte_count else 0


def clamp_planner_input_cap(cap: int | None) -> int:
    value = int(cap if cap is not None else DEFAULT_PLANNER_INPUT_TOKEN_CAP)
    return min(max(value, HARD_MIN_PLANNER_INPUT_TOKEN_CAP), HARD_MAX_PLANNER_INPUT_TOKEN_CAP)


@dataclass
class PlannerContextBudgetReport:
    estimated_input_tokens: int
    configured_input_cap: int
    evidence_included: list[str] = field(default_factory=list)
    evidence_compacted: list[str] = field(default_factory=list)
    evidence_dropped: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_input_tokens": self.estimated_input_tokens,
            "configured_input_cap": self.configured_input_cap,
            "evidence_included": list(self.evidence_included),
            "evidence_compacted": list(self.evidence_compacted),
            "evidence_dropped": list(self.evidence_dropped),
        }


def _prompt_tokens(system_prompt: str, evidence: dict[str, Any]) -> int:
    body = system_prompt + "\n\nEvidence:\n" + json.dumps(evidence, sort_keys=True)
    return estimate_tokens(body)


def budget_planner_prompt(
    *,
    system_prompt: str,
    evidence: dict[str, Any],
    cap: int | None = None,
) -> PlannerContextBudgetReport:
    configured = clamp_planner_input_cap(cap)
    packed = dict(evidence)
    compacted: list[str] = []
    dropped: list[str] = []

    for key in OPTIONAL_KEYS:
        if _prompt_tokens(system_prompt, packed) <= configured:
            break
        current = packed.get(key)
        if current in ("", {}, None, []):
            continue
        dropped.append(key)
        compacted.append(key)
        packed[key] = {"compacted": True} if isinstance(current, dict) else ""

    excerpts = packed.get("file_excerpts")
    if isinstance(excerpts, dict) and excerpts:
        items = list(excerpts.items())
        guard = 0
        while _prompt_tokens(system_prompt, packed) > configured and items and guard < 32:
            guard += 1
            if len(items) > 2:
                dropped_path, _ = items.pop()
                dropped.append(f"file_excerpts:{dropped_path}")
                packed["file_excerpts"] = dict(items)
                continue
            new_excerpts: dict[str, str] = {}
            fully_min = True
            for path, text in items:
                raw = str(text or "")
                cut = max(120, len(raw) // 2)
                if cut < len(raw):
                    compacted.append(f"file_excerpts:{path}")
                    fully_min = False
                new_excerpts[path] = raw[:cut]
            packed["file_excerpts"] = new_excerpts
            items = list(new_excerpts.items())
            if fully_min and items:
                dropped_path, _ = items.pop()
                dropped.append(f"file_excerpts:{dropped_path}")
                packed["file_excerpts"] = dict(items)

    symbols = packed.get("symbols_found")
    if isinstance(symbols, list) and _prompt_tokens(system_prompt, packed) > configured and len(symbols) > 12:
        packed["symbols_found"] = symbols[:12]
        compacted.append("symbols_found")

    constraints = packed.get("constraints")
    if isinstance(constraints, list) and _prompt_tokens(system_prompt, packed) > configured and len(constraints) > 8:
        packed["constraints"] = constraints[:8]
        compacted.append("constraints")

    included: list[str] = []
    dropped_set = set(dropped)
    for key, value in packed.items():
        if key in dropped_set and not str(key).startswith("file_excerpts:"):
            continue
        if value in ("", {}, None, []) or value == {"compacted": True}:
            continue
        included.append(key)

    return PlannerContextBudgetReport(
        estimated_input_tokens=_prompt_tokens(system_prompt, packed),
        configured_input_cap=configured,
        evidence_included=included,
        evidence_compacted=list(dict.fromkeys(compacted)),
        evidence_dropped=list(dict.fromkeys(dropped)),
        evidence=packed,
    )


__all__ = [
    "DEFAULT_PLANNER_INPUT_TOKEN_CAP",
    "HARD_MAX_PLANNER_INPUT_TOKEN_CAP",
    "PlannerContextBudgetReport",
    "budget_planner_prompt",
    "clamp_planner_input_cap",
    "estimate_tokens",
]
