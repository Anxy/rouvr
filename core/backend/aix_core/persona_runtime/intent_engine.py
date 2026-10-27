"""IntentEngine: classifies a real planner packet into a lane -> persona role.

This is deliberately NOT a greenfield intent taxonomy. It formalizes the one real
intent signal that exists anywhere in the AIX codebase today: the inline boolean
condition chain that previously lived directly inside
devexecutor_tool_contract_registry_service.build_planner_contract_text, deciding
between the "strict_replace_function" and "generic" lanes. The seven conditions
below are reproduced in the same order, unchanged, from that real code -- not
reinvented -- so classify_intent()'s output is byte-identical to what the inline
logic already produced. See test_intent_engine.py's parity tests for proof.

Every other "intent taxonomy" example one might reach for (Debug Code, Code Review,
Architecture, Planning, Documentation, Research, Sprint Planning, Security Audit...)
has zero grounding anywhere in this codebase. Building an engine for those categories
would be exactly the kind of fabrication already ruled out for the Capability
Registry (see capability_registry.py). This engine classifies only what is real.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.services.aix_v2_executable_plan_protocol_service import (
    is_codeintel_325_replace_function_lane,
    is_packet_bound_single_symbol_replace_function_lane,
)

LaneIntent = Literal["strict_replace_function", "generic"]

# Role this lane currently routes to. Both lanes route to executor_coder because
# that is the only role with any real serving prompt-builder path today (see
# manifest_adapter.MISSING_REAL_CONTENT for the other four roles' status). This map
# is a real seam for future routing, not a claim that other roles are reachable yet.
LANE_TO_PERSONA_ROLE: dict[LaneIntent, str] = {
    "strict_replace_function": "executor_coder",
    "generic": "executor_coder",
}


@dataclass(frozen=True)
class IntentV1:
    lane: LaneIntent
    matched_trigger: str  # which real condition fired -- evidence, not decoration
    persona_role: str


def classify_intent(packet: dict[str, object] | None = None) -> IntentV1:
    packet_dict: dict[str, object] = packet if isinstance(packet, dict) else {}

    if str(packet_dict.get("planner_mode") or "").strip() == "packet_bound_replace_function":
        trigger = "planner_mode==packet_bound_replace_function"
    elif str(packet_dict.get("mode") or "").strip() == "packet_bound_replace_function":
        trigger = "mode==packet_bound_replace_function"
    elif str(packet_dict.get("lane") or "").strip() == "packet_bound_replace_function":
        trigger = "lane==packet_bound_replace_function"
    elif str(packet_dict.get("lane") or "").strip() == "strict_replace_function":
        trigger = "lane==strict_replace_function"
    elif str(packet_dict.get("planner_lane") or "").strip() == "strict_replace_function":
        trigger = "planner_lane==strict_replace_function"
    elif is_codeintel_325_replace_function_lane(packet_dict):
        trigger = "is_codeintel_325_replace_function_lane"
    elif is_packet_bound_single_symbol_replace_function_lane(packet_dict):
        trigger = "is_packet_bound_single_symbol_replace_function_lane"
    else:
        return IntentV1(
            lane="generic",
            matched_trigger="no_strict_trigger_matched",
            persona_role=LANE_TO_PERSONA_ROLE["generic"],
        )

    return IntentV1(
        lane="strict_replace_function",
        matched_trigger=trigger,
        persona_role=LANE_TO_PERSONA_ROLE["strict_replace_function"],
    )
