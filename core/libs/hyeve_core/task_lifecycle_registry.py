from __future__ import annotations

from typing import Any

TERMINAL_TASK_STATUS = "done"
LEGACY_REVIEW_PENDING_STATUS = "submitted_for_review"

TASK_STATUS_DEFINITIONS: dict[str, dict[str, Any]] = {
    "queued": {
        "aliases": [],
        "allowed_transitions": ["running", "blocked", "archived"],
        "required_metadata": [],
        "review_pending": False,
        "open": True,
        "terminal": False,
    },
    "running": {
        "aliases": ["in_progress"],
        "allowed_transitions": [
            "blocked",
            "waiting_on_human",
            "needs_enrichment",
            "needs_human_intervention",
            "execution_failed",
            "needs_review",
            "queued",
            "archived",
        ],
        "required_metadata": [],
        "review_pending": False,
        "open": True,
        "terminal": False,
    },
    "waiting_on_human": {
        "aliases": [],
        "allowed_transitions": ["running", LEGACY_REVIEW_PENDING_STATUS, "blocked", "archived"],
        "required_metadata": ["reason_code", "reason_summary"],
        "review_pending": False,
        "open": True,
        "terminal": False,
    },
    "needs_enrichment": {
        "aliases": [],
        "allowed_transitions": ["queued", "running", "blocked", "archived"],
        "required_metadata": ["reason_code", "reason_summary"],
        "review_pending": False,
        "open": True,
        "terminal": False,
    },
    "needs_human_intervention": {
        "aliases": [],
        "allowed_transitions": ["queued", "running", "blocked", "archived"],
        "required_metadata": ["reason_code", "reason_summary"],
        "review_pending": False,
        "open": True,
        "terminal": False,
    },
    "execution_failed": {
        "aliases": [],
        "allowed_transitions": ["queued", "running", "needs_human_intervention", "blocked", "archived"],
        "required_metadata": ["reason_code", "reason_summary"],
        "review_pending": False,
        "open": True,
        "terminal": False,
    },
    LEGACY_REVIEW_PENDING_STATUS: {
        "aliases": [],
        "allowed_transitions": ["running", "waiting_on_human", "blocked", "done", "archived"],
        "required_metadata": ["reason_code", "reason_summary"],
        "review_pending": True,
        "open": True,
        "terminal": False,
    },
    "blocked": {
        "aliases": [],
        "allowed_transitions": ["queued", "running", "archived"],
        "required_metadata": ["blocked_reason", "reason_code", "reason_summary", "blocked_at"],
        "review_pending": False,
        "open": True,
        "terminal": False,
    },
    "needs_review": {
        "aliases": [],
        "allowed_transitions": ["running", "blocked", "done", "archived", "change_required"],
        "required_metadata": ["reason_code", "reason_summary"],
        "review_pending": True,
        "open": True,
        "terminal": False,
    },
    # Review outcome: parent stays in needs_review lineage; change_required is
    # the published child state before rework resumes at running/queued.
    "change_required": {
        "aliases": ["changes_required"],
        "allowed_transitions": ["running", "queued", "blocked", "archived", "needs_review"],
        "required_metadata": ["reason_code", "reason_summary"],
        "review_pending": False,
        "open": True,
        "terminal": False,
    },
    "done": {
        "aliases": [],
        "allowed_transitions": [],
        "required_metadata": [],
        "review_pending": False,
        "open": False,
        "terminal": True,
    },
    "archived": {
        "aliases": [],
        "allowed_transitions": [],
        "required_metadata": [],
        "review_pending": False,
        "open": False,
        "terminal": True,
    },
}

TASK_STATUS_ALIASES: dict[str, str] = {
    alias: status for status, definition in TASK_STATUS_DEFINITIONS.items() for alias in definition.get("aliases", [])
}

ALLOWED_TASK_TRANSITIONS: dict[str, set[str]] = {
    status: set(definition.get("allowed_transitions", [])) for status, definition in TASK_STATUS_DEFINITIONS.items()
}

TASK_STATE_METADATA_REQUIREMENTS: dict[str, set[str]] = {
    status: set(definition.get("required_metadata", []))
    for status, definition in TASK_STATUS_DEFINITIONS.items()
    if definition.get("required_metadata")
}

TASK_LOOKUP_STATUSES: tuple[str, ...] = (
    "running",
    "needs_enrichment",
    "needs_human_intervention",
    "execution_failed",
    "needs_review",
    "change_required",
    LEGACY_REVIEW_PENDING_STATUS,
    "waiting_on_human",
    "done",
    "blocked",
)

TARGETED_RERUN_TASK_STATUSES: set[str] = {
    "queued",
    "blocked",
    "needs_enrichment",
    "needs_human_intervention",
    "execution_failed",
    "needs_review",
    "change_required",
    "waiting_on_human",
    LEGACY_REVIEW_PENDING_STATUS,
}

REVIEW_PENDING_TASK_STATUSES: set[str] = {
    status for status, definition in TASK_STATUS_DEFINITIONS.items() if bool(definition.get("review_pending"))
}

OPEN_TASK_STATUSES: set[str] = {
    status for status, definition in TASK_STATUS_DEFINITIONS.items() if bool(definition.get("open"))
}


def normalize_task_status(status_value: str | None) -> str:
    normalized = str(status_value or "").strip().lower()
    return TASK_STATUS_ALIASES.get(normalized, normalized)


def task_status_definition(status_value: str | None) -> dict[str, Any]:
    normalized = normalize_task_status(status_value)
    definition = TASK_STATUS_DEFINITIONS.get(normalized)
    if definition is None:
        return {
            "status": normalized,
            "aliases": [],
            "allowed_transitions": [],
            "required_metadata": [],
            "review_pending": False,
            "open": False,
            "terminal": False,
        }
    return {
        "status": normalized,
        "aliases": list(definition.get("aliases", [])),
        "allowed_transitions": list(definition.get("allowed_transitions", [])),
        "required_metadata": list(definition.get("required_metadata", [])),
        "review_pending": bool(definition.get("review_pending", False)),
        "open": bool(definition.get("open", False)),
        "terminal": bool(definition.get("terminal", False)),
    }


def is_review_pending_status(status_value: str | None) -> bool:
    return bool(task_status_definition(status_value).get("review_pending"))


def is_open_task_status(status_value: str | None) -> bool:
    return bool(task_status_definition(status_value).get("open"))


def is_terminal_task_status(status_value: str | None) -> bool:
    return bool(task_status_definition(status_value).get("terminal"))


def task_transition_contract() -> dict[str, Any]:
    return {
        "aliases": dict(TASK_STATUS_ALIASES),
        "states": sorted(ALLOWED_TASK_TRANSITIONS.keys()),
        "allowed_transitions": {state: sorted(targets) for state, targets in ALLOWED_TASK_TRANSITIONS.items()},
        "required_metadata": {state: sorted(fields) for state, fields in TASK_STATE_METADATA_REQUIREMENTS.items()},
        "state_definitions": {state: task_status_definition(state) for state in sorted(TASK_STATUS_DEFINITIONS.keys())},
    }


def required_task_metadata_fields(status_value: str | None) -> set[str]:
    return set(TASK_STATE_METADATA_REQUIREMENTS.get(normalize_task_status(status_value), set()))
