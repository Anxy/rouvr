"""Deterministic conversation -> objective message binding.

Implements `CURSOR-CLASS-EXTENSION.md` Sprint 002 S1 (highest priority):
every objective must cite exactly one originating message
(`objective_source_message_id`), and binding must never fall back to an
earlier greeting when a later objective-bearing message exists in the same
turn window.

No model dependency: this is a deterministic containment gate, matching the
same "never guess" discipline as `plan_validation.py`. Model-assisted intent
classification (for genuinely ambiguous objective-bearing text) is a later,
separate concern -- this module only decides *which message* the objective
text comes from, never what the text means.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# Pure greeting/ack tokens. Anything else -- including a greeting *plus*
# extra content ("hey, can you check X") -- is objective-bearing, since the
# extra content itself may be the objective; only a message that is *only*
# a greeting/ack is excluded.
_GREETING_RE = re.compile(
    r"^(hey+|hi+|hello+|hiya|yo+|sup|howdy|greetings|"
    r"good\s+(morning|afternoon|evening|day)|"
    r"thanks|thank\s+you|thx|ty|ok|okay|k|cool|got\s+it|sure)"
    r"[\s!.,]*$",
    re.IGNORECASE,
)

# Strong create/edit verbs that mark a message as a primary task statement.
_STRONG_TASK_VERB_RE = re.compile(
    r"\b(create|build|implement|develop|generate|write|fix|refactor|"
    r"scaffold|design|code)\b",
    re.IGNORECASE,
)
# Weaker verbs only count with an explicit deliverable noun.
_WEAK_TASK_VERB_RE = re.compile(r"\b(add|make)\b", re.IGNORECASE)

# Follow-ups that constrain style/scope without restating the whole task.
_REFINEMENT_RE = re.compile(
    r"\b(too long|shorter|short(er)?|concise|simpler|simple|only|"
    r"instead|without|keep it|make it|can be short|less verbose|"
    r"trim|reduce|brief)\b",
    re.IGNORECASE,
)

# Tech / deliverable anchors that a real create objective usually carries.
_DELIVERABLE_RE = re.compile(
    r"\b(php|html|css|backend|frontend|api|page|form|attendance|"
    r"login|regist\w*|system|app)\b",
    re.IGNORECASE,
)


class MessageBindingError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}:{detail}")


@dataclass(frozen=True)
class MessageBindingResult:
    conversation_id: str | None
    message_id: str
    parent_message_id: str | None
    selected_message_id: str
    objective_source_message_id: str
    objective_text: str


def is_pure_greeting(text: str) -> bool:
    """True only for a message that carries no objective content of its own."""
    return bool(_GREETING_RE.match((text or "").strip()))


def looks_like_task_statement(text: str) -> bool:
    """True when the message itself looks like a primary create/edit ask."""
    t = (text or "").strip()
    if not t or is_pure_greeting(t):
        return False
    # Style-only follow-ups often contain weak verbs ("only teacher will add") —
    # do not treat them as a new primary objective.
    if _REFINEMENT_RE.search(t) and not _STRONG_TASK_VERB_RE.search(t):
        return False
    has_deliverable = bool(_DELIVERABLE_RE.search(t))
    if _STRONG_TASK_VERB_RE.search(t) and (has_deliverable or len(t) >= 40):
        return True
    if _WEAK_TASK_VERB_RE.search(t) and has_deliverable:
        return True
    return False


def looks_like_refinement(text: str, *, prior_objective: str) -> bool:
    """True when `text` is a short constraint on a prior concrete objective."""
    t = (text or "").strip()
    prior = (prior_objective or "").strip()
    if not t or not prior or is_pure_greeting(t):
        return False
    # A new full task statement always replaces, even if it says "instead".
    if looks_like_task_statement(t):
        return False
    if len(t) >= len(prior):
        return False
    if _REFINEMENT_RE.search(t):
        return True
    # Short follow-up without strong task verbs after a longer create ask.
    return (
        not _STRONG_TASK_VERB_RE.search(t)
        and looks_like_task_statement(prior)
        and len(t) < 100
        and len(prior) > len(t) * 1.2
    )


def compose_objective_text(*, primary: str, refinements: Sequence[str]) -> str:
    """Keep the primary task and append later constraints without replacing it."""
    base = (primary or "").strip()
    extras = [str(r).strip() for r in refinements if str(r).strip() and str(r).strip() != base]
    if not extras:
        return base
    return f"{base}\n\nAdditional constraints from user: " + " ".join(extras)


def bind_objective_source_message(
    messages: Sequence[Mapping[str, Any]],
    *,
    conversation_id: str | None = None,
    selected_message_id: str | None = None,
) -> MessageBindingResult:
    """Deterministically select which user message's text becomes the objective.

    `messages` is chronological, oldest first, each item at least
    `{"message_id": str, "role": "user"|"assistant", "content": str,
    "parent_message_id": str | None}`.

    Rule order (never guesses, never falls back to greeting text):
      1. An explicit `selected_message_id` wins when it names a real user
         message in `messages` (still merges later refinements after it).
      2. Otherwise pick the latest user message that is not a pure
         greeting/ack. If that message is only a refinement of an earlier
         task statement, keep the earlier task as primary and append the
         refinement as a constraint — never replace attendance/PHP asks
         with "make it shorter" chat.
      3. If every user message is a pure greeting/ack, or there are no user
         messages at all, binding fails closed -- the caller must surface
         `INTENT_UNCLEAR`, never silently bind the greeting.
    """
    user_messages = [
        m
        for m in messages
        if str(m.get("role") or "").strip().lower() == "user" and str(m.get("message_id") or "").strip()
    ]
    if not user_messages:
        raise MessageBindingError("INTENT_UNCLEAR", "no user message supplied")

    by_id = {str(m["message_id"]): m for m in user_messages}

    if selected_message_id:
        chosen = by_id.get(str(selected_message_id))
        if chosen is None:
            raise MessageBindingError(
                "WRONG_SOURCE_MESSAGE",
                f"selected_message_id {selected_message_id!r} is not a known user message",
            )
        primary = chosen
        primary_text = str(chosen.get("content") or "").strip()
        if not primary_text:
            raise MessageBindingError("INTENT_UNCLEAR", "selected message has no text")
        refinements: list[str] = []
        after = False
        for candidate in user_messages:
            mid = str(candidate["message_id"])
            if mid == str(selected_message_id):
                after = True
                continue
            if not after:
                continue
            text = str(candidate.get("content") or "").strip()
            if text and not is_pure_greeting(text) and looks_like_refinement(
                text, prior_objective=primary_text
            ):
                refinements.append(text)
        parent = primary.get("parent_message_id")
        return MessageBindingResult(
            conversation_id=conversation_id,
            message_id=str(primary["message_id"]),
            parent_message_id=str(parent) if parent else None,
            selected_message_id=str(primary["message_id"]),
            objective_source_message_id=str(primary["message_id"]),
            objective_text=compose_objective_text(primary=primary_text, refinements=refinements),
        )

    chosen = None
    for candidate in reversed(user_messages):
        text = str(candidate.get("content") or "").strip()
        if text and not is_pure_greeting(text):
            chosen = candidate
            break
    if chosen is None:
        raise MessageBindingError(
            "INTENT_UNCLEAR", "every user message in this turn window is a pure greeting/ack"
        )

    latest_text = str(chosen.get("content") or "").strip()
    if not latest_text:
        raise MessageBindingError("INTENT_UNCLEAR", "selected message has no text")

    primary = chosen
    primary_text = latest_text
    refinements: list[str] = []

    # Walk earlier user messages for a stronger task statement when latest is
    # only a style/scope refinement ("thats too long...").
    for candidate in reversed(user_messages):
        if candidate is chosen:
            continue
        text = str(candidate.get("content") or "").strip()
        if not text or is_pure_greeting(text):
            continue
        if looks_like_task_statement(text) and looks_like_refinement(
            latest_text, prior_objective=text
        ):
            primary = candidate
            primary_text = text
            between: list[str] = []
            seen_primary = False
            for m in user_messages:
                mid = str(m["message_id"])
                if mid == str(primary["message_id"]):
                    seen_primary = True
                    continue
                if not seen_primary:
                    continue
                if mid == str(chosen["message_id"]):
                    break
                mid_text = str(m.get("content") or "").strip()
                if mid_text and looks_like_refinement(mid_text, prior_objective=primary_text):
                    between.append(mid_text)
            refinements = between + [latest_text]
            break

    parent = primary.get("parent_message_id")
    return MessageBindingResult(
        conversation_id=conversation_id,
        message_id=str(primary["message_id"]),
        parent_message_id=str(parent) if parent else None,
        selected_message_id=str(primary["message_id"]),
        objective_source_message_id=str(primary["message_id"]),
        objective_text=compose_objective_text(primary=primary_text, refinements=refinements),
    )


__all__ = [
    "MessageBindingError",
    "MessageBindingResult",
    "bind_objective_source_message",
    "compose_objective_text",
    "is_pure_greeting",
    "looks_like_refinement",
    "looks_like_task_statement",
]
