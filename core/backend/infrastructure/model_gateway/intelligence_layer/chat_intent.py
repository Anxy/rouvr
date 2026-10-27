"""W04: Chat intent classifier (conversation / objective / clarification / continue)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ChatIntent = Literal["conversation", "objective", "clarification", "continue_objective"]

_GREETING = re.compile(
    r"^\s*(hi|hello|hey|yo|sup|thanks|thank you|good (morning|afternoon|evening)|how are you)\b[\s!?.]*$",
    re.I,
)
_CLARIFY = re.compile(
    r"\b(what do you mean|can you clarify|not sure|more detail|which (one|file|option)|explain)\b",
    re.I,
)
_CONTINUE = re.compile(
    r"\b(continue|resume|keep going|same (objective|task)|that objective|existing (task|objective))\b",
    re.I,
)
_OBJECTIVE = re.compile(
    r"\b("
    r"build|create|implement|fix|add|update|refactor|migrate|generate|write|make|"
    r"login page|landing page|registration|register|sign[- ]?up|html|css|bug|feature|"
    r"docs?|test|api|endpoint|page"
    r")\b",
    re.I,
)


@dataclass(frozen=True)
class ChatIntentResultV1:
    intent: ChatIntent
    confidence: float
    objective_summary: str | None
    reason: str


def classify_chat_intent(
    *,
    latest_user_text: str,
    history: list[dict[str, str]] | None = None,
    active_objective_id: str | None = None,
) -> ChatIntentResultV1:
    text = str(latest_user_text or "").strip()
    if not text:
        return ChatIntentResultV1(
            intent="clarification",
            confidence=1.0,
            objective_summary=None,
            reason="empty_user_text",
        )

    if active_objective_id and _CONTINUE.search(text):
        return ChatIntentResultV1(
            intent="continue_objective",
            confidence=0.9,
            objective_summary=f"continue:{active_objective_id}",
            reason="continue_with_active_objective",
        )

    if _GREETING.match(text) or len(text) <= 3:
        return ChatIntentResultV1(
            intent="conversation",
            confidence=0.95,
            objective_summary=None,
            reason="greeting_or_trivial",
        )

    if _CONTINUE.search(text):
        return ChatIntentResultV1(
            intent="continue_objective",
            confidence=0.75,
            objective_summary=text[:240],
            reason="continue_keywords",
        )

    if _CLARIFY.search(text) and not _OBJECTIVE.search(text):
        return ChatIntentResultV1(
            intent="clarification",
            confidence=0.8,
            objective_summary=None,
            reason="clarification_keywords",
        )

    if _OBJECTIVE.search(text) or len(text) >= 40:
        return ChatIntentResultV1(
            intent="objective",
            confidence=0.85 if _OBJECTIVE.search(text) else 0.6,
            objective_summary=text[:480],
            reason="objective_keywords_or_long_utterance",
        )

    # Short non-greeting chatter
    prior_user = 0
    for item in history or []:
        if str(item.get("role") or "") == "user":
            prior_user += 1
    if prior_user <= 1 and len(text.split()) <= 6:
        return ChatIntentResultV1(
            intent="conversation",
            confidence=0.7,
            objective_summary=None,
            reason="short_chitchat",
        )

    return ChatIntentResultV1(
        intent="clarification",
        confidence=0.55,
        objective_summary=text[:240],
        reason="underspecified",
    )
