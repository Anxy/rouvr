"""
E1 — Artifact Normalization Service
====================================
Converts raw run_fire response.json artifacts into stable semantic fixtures
suitable for deterministic assertion in the E2/E3 replay harness.

Two passes:
  1. Structural pass  — replaces transient leaf values (UUIDs, SHA1s, timestamps,
     absolute paths, generated IDs) with stable type-qualified tokens.
  2. Content pass     — strips embedded transient data from prose string fields
     (reason_summary, message, etc.) while preserving semantic meaning.

Plus:
  - Evidence ref unification: converts old flat-string evidence_refs to typed
    objects (matched format to attestation.evidence_refs shape) before structural
    pass runs.
  - List ordering policy: sort-before-compare for non-order-sensitive lists;
    preserve order for order-sensitive lists (validation_commands, plan steps).
"""
from __future__ import annotations

import copy
import re
from typing import Any

# ---------------------------------------------------------------------------
# Transient value patterns (matched against complete string values)
# ---------------------------------------------------------------------------

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
_ABS_PATH_RE = re.compile(r"^(/[^\s]+|[A-Za-z]:\\[^\s]+)")
_CORR_ID_RE = re.compile(r"^corr-\S+$")
_RUNONCE_RE = re.compile(r"^runonce-[0-9a-f]+$")
_PKT_ID_RE = re.compile(r"^pkt-[0-9a-f]+$")
_BRANCH_PROPOSAL_RE = re.compile(r"^aix/proposals/.+$")

# Content-pass patterns (matched within prose string values)
_EMBEDDED_PATH_RE = re.compile(r"/[/a-zA-Z0-9_\-\.]+(?:/[a-zA-Z0-9_\-\.]+)+")
_EMBEDDED_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_EMBEDDED_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s]+")

# ---------------------------------------------------------------------------
# Field-name → token-type mapping (structural pass)
# Terminal field name drives token type; cross-referencing by value handles
# the same UUID appearing in multiple positions.
# ---------------------------------------------------------------------------

_FIELD_TOKEN_TYPES: dict[str, str] = {
    # invocation / run IDs
    "invocation_id": "id:invocation",
    "correlation_id": "id:correlation",
    "run_id": "id:run",
    "packet_id": "id:packet",
    "trace_packet_id": "id:trace_packet",
    # ticket / task IDs (all carry the same UUID per run)
    "ticket_id": "id:ticket",
    "execution_unit_id": "id:ticket",
    "task_id": "id:ticket",
    "scope_ref": "id:ticket",
    # proposal IDs
    "proposal_id": "id:proposal",
    "job_id": "id:job",
    # hash fields
    "attestation_id": "hash:attestation",
    "pack_attestation_id": "hash:pack_attestation",
    "transition_id": "hash:transition",
    "base_commit": "hash:commit",
    # branch fields
    "branch_name": "branch:proposal",
    "base_branch": "branch:base",
    # path fields (absolute filesystem paths)
    "workspace_root": "path:workspace",
    "source_root": "path:source_workspace",
    "effective_workspace_root": "path:effective_workspace",
    "source_workspace_root": "path:source_workspace",
    "checkout_root": "path:checkout_root",
    "proposal_workspace_root": "path:proposal_workspace",
    "ref_id": "path:evidence_ref",
    "trace_ref": "path:trace",
    "local_trace_ref": "path:trace_local",
    "transcript_ref": "path:transcript",
    "changed_files_ref": "path:changed_files_ref",
    "diff_ref": "path:diff_ref",
    "logs_ref": "path:logs_ref",
    "tests_ref": "path:tests_ref",
    "status_ref": "path:status_ref",
    "knowledge_ref": "path:knowledge_ref",
    "retry_event_ref": "path:retry_event_ref",
    # top-level artifact metadata
    "url": "path:url",
    "generated_at_utc": "ts:generated",
    # timestamps (pre-assigned by _prescan_timestamps)
    "timestamp_utc": "ts:record",
    "accepted_at_utc": "ts:accepted",
    "started_at_utc": "ts:started",
    "finished_at_utc": "ts:finished",
    "created_at_utc": "ts:created",
}

# Fields whose values contain embedded transient data within prose strings.
# These are run through the content pass instead of wholesale replacement.
_CONTENT_PASS_FIELDS: frozenset[str] = frozenset(
    {
        "reason_summary",
        "message",
        "repair_summary",
        "summary",
        "retention_reason",
    }
)

# ---------------------------------------------------------------------------
# List ordering policy — explicit declaration per field name.
#   UNORDERED: sorted before assertion (evidence refs, file lists, warnings)
#   ORDER_SENSITIVE: preserved as-is   (validation_commands, plan steps, events)
# ---------------------------------------------------------------------------

_UNORDERED_LIST_FIELDS: frozenset[str] = frozenset(
    {
        "evidence_refs",
        "changed_files",
        "failed_checks",
        "warnings",
        "refs",
        "missing_fields",
        "reasons",
        "clarification_questions",
        "context_keys",
        "fallback_reason_codes",
        "next_best_actions",
    }
)

# validation_commands, plan steps, temporal.state_machine.events, etc.
# are order-sensitive — not in the above set, so preserved.


# ---------------------------------------------------------------------------
# ArtifactNormalizer
# ---------------------------------------------------------------------------


class ArtifactNormalizer:
    """Normalize a raw run_fire response.json dict into a stable semantic fixture."""

    def __init__(self) -> None:
        self._token_registry: dict[str, str] = {}
        self._type_counters: dict[str, int] = {}

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Return a stable normalized copy of *raw*.  The input is not mutated."""
        self._token_registry = {}
        self._type_counters = {}

        artifact = copy.deepcopy(raw)

        # Step 1 — pre-assign timestamp tokens in chronological order
        self._prescan_timestamps(artifact)

        # Step 2 — unify evidence refs (flat strings → typed objects) before
        # structural pass so ref_id fields are present for path replacement
        _unify_evidence_refs_inplace(artifact)

        # Step 3 — structural pass (replace transient leaf values with tokens)
        result = self._walk(artifact, field_name=None)

        # Step 4 — sort non-order-sensitive lists
        _sort_unordered_lists(result)

        return result  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Timestamp pre-scan
    # ------------------------------------------------------------------

    def _prescan_timestamps(self, obj: Any) -> None:
        """Collect all ISO timestamps, sort chronologically, pre-assign tokens."""
        seen: set[str] = set()

        def _collect(o: Any) -> None:
            if isinstance(o, dict):
                for v in o.values():
                    _collect(v)
            elif isinstance(o, list):
                for item in o:
                    _collect(item)
            elif isinstance(o, str) and _TIMESTAMP_RE.match(o):
                seen.add(o)

        _collect(obj)

        for i, ts in enumerate(sorted(seen), 1):
            self._token_registry[ts] = f"<ts:{i}>"

    # ------------------------------------------------------------------
    # Structural walk
    # ------------------------------------------------------------------

    def _walk(self, obj: Any, field_name: str | None) -> Any:
        if isinstance(obj, dict):
            return {k: self._walk(v, k) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._walk(item, field_name) for item in obj]
        if isinstance(obj, str):
            return self._normalize_string(obj, field_name)
        return obj

    def _normalize_string(self, value: str, field_name: str | None) -> str:
        if not value:
            return value

        # Timestamps are pre-assigned — check registry first
        if value in self._token_registry:
            return self._token_registry[value]

        # Content-pass fields: strip embedded patterns, preserve prose
        if field_name in _CONTENT_PASS_FIELDS:
            return _content_pass(value)

        # Field has a known token type
        token_type = _FIELD_TOKEN_TYPES.get(field_name or "")
        if token_type:
            return self._assign(value, token_type)

        # Fallback: pattern detection
        return self._detect_and_replace(value, field_name)

    def _assign(self, value: str, token_type: str) -> str:
        if value in self._token_registry:
            return self._token_registry[value]
        n = self._type_counters.get(token_type, 0) + 1
        self._type_counters[token_type] = n
        token = f"<{token_type}:{n}>"
        self._token_registry[value] = token
        return token

    def _detect_and_replace(self, value: str, field_name: str | None) -> str:
        fname = field_name or ""

        if _SHA1_RE.match(value):
            if "attestation" in fname:
                ttype = "hash:attestation"
            elif "transition" in fname:
                ttype = "hash:transition"
            elif "commit" in fname:
                ttype = "hash:commit"
            else:
                ttype = "hash:sha1"
            return self._assign(value, ttype)

        if _UUID_RE.match(value):
            if any(k in fname for k in ("ticket", "task", "execution_unit")):
                ttype = "id:ticket"
            elif "invocation" in fname:
                ttype = "id:invocation"
            elif "proposal" in fname:
                ttype = "id:proposal"
            elif "correlation" in fname:
                ttype = "id:correlation"
            else:
                ttype = "id:uuid"
            return self._assign(value, ttype)

        if _RUNONCE_RE.match(value):
            return self._assign(value, "id:invocation")

        if _CORR_ID_RE.match(value):
            return self._assign(value, "id:correlation")

        if _PKT_ID_RE.match(value):
            return self._assign(value, "id:packet")

        if _BRANCH_PROPOSAL_RE.match(value):
            return self._assign(value, "branch:proposal")

        if _ABS_PATH_RE.match(value):
            if "workspace" in fname:
                ttype = "path:workspace"
            elif "trace" in fname or "log" in fname:
                ttype = "path:trace"
            elif "ref" in fname:
                ttype = "path:evidence_ref"
            else:
                ttype = "path:abs"
            return self._assign(value, ttype)

        return value


# ---------------------------------------------------------------------------
# Evidence ref unification (modifies artifact in-place, before structural pass)
# ---------------------------------------------------------------------------


def _unify_evidence_refs_inplace(obj: Any) -> None:
    """Walk the artifact and convert any flat-string evidence_refs to typed objects."""
    if isinstance(obj, dict):
        if "evidence_refs" in obj:
            obj["evidence_refs"] = _convert_ref_list(obj["evidence_refs"])
        for v in obj.values():
            _unify_evidence_refs_inplace(v)
    elif isinstance(obj, list):
        for item in obj:
            _unify_evidence_refs_inplace(item)


def _convert_ref_list(refs: list[Any]) -> list[dict[str, Any]]:
    """Convert mixed flat-string / typed evidence_refs to all-typed form."""
    result = []
    for ref in refs:
        if isinstance(ref, str):
            result.append(
                {
                    "ref_id": ref,
                    "ref_type": _infer_ref_type(ref),
                    "owned_by": _infer_owned_by(ref),
                    "pack_id": _infer_pack_id(ref),
                }
            )
        elif isinstance(ref, dict):
            result.append(ref)
        else:
            result.append(ref)
    return result


def _infer_ref_type(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".patch") or "/diff" in lower:
        return "diff"
    if "changed_files" in lower or "git-status" in lower or "status.txt" in lower:
        return "workspace_status"
    if "outcome-policy" in lower or "outcome_policy" in lower:
        return "execution_trace"
    if "planner" in lower and "trace" in lower:
        return "planner_trace"
    if "test" in lower and lower.endswith(".json"):
        return "test_result"
    if "log" in lower:
        return "run_log"
    if lower.endswith(".json"):
        return "artifact_ref"
    return "unknown"


def _infer_owned_by(path: str) -> str:
    if "_runlogs" in path or "/app/" in path:
        return "CONTROL_PLANE"
    return "PACK"


def _infer_pack_id(path: str) -> str | None:
    if "_runlogs" in path or "/app/" in path:
        return None
    return "coding"


# ---------------------------------------------------------------------------
# List ordering
# ---------------------------------------------------------------------------


def _sort_unordered_lists(obj: Any) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _UNORDERED_LIST_FIELDS and isinstance(value, list):
                obj[key] = _stable_sort(value)
            else:
                _sort_unordered_lists(value)
    elif isinstance(obj, list):
        for item in obj:
            _sort_unordered_lists(item)


def _stable_sort(lst: list[Any]) -> list[Any]:
    try:
        return sorted(
            lst,
            key=lambda x: (str(sorted(x.items())) if isinstance(x, dict) else str(x)),
        )
    except Exception:
        return lst


# ---------------------------------------------------------------------------
# Content pass (embedded transient data within prose strings)
# ---------------------------------------------------------------------------


def _content_pass(value: str) -> str:
    """Strip embedded absolute paths and UUIDs from prose string fields."""
    value = _EMBEDDED_WIN_PATH_RE.sub("<path:embedded>", value)
    value = _EMBEDDED_PATH_RE.sub("<path:embedded>", value)
    value = _EMBEDDED_UUID_RE.sub("<id:embedded>", value)
    return value.strip()
