from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.services.aix_target_file_resolution_service import (
    TargetFileResolutionResult,
    resolve_target_file,
)
from backend.services.devexecutor_search_replace_blocks_service import (
    SearchReplaceBlock,
    find_line_aligned_matches,
)


class V2LiveFileGroundingError(RuntimeError):
    def __init__(self, code: str, detail: dict[str, Any] | None = None) -> None:
        self.code = str(code or "").strip() or "EDIT_FILE_LIVE_GROUNDING_FAILED"
        self.detail = dict(detail or {})
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class V2LiveFileGroundingResult:
    relative_path: str
    file_content: str
    blocks: list[SearchReplaceBlock]
    mode: str
    target_resolution: TargetFileResolutionResult
    grounding_source: str | None = None
    search_range: dict[str, int] | None = None
    edit_range_within_read: dict[str, int] | None = None
    derived_search_text: str | None = None

    def contract_validation(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "passed",
            "mode": f"live_file_{self.mode}",
            "relative_path": self.relative_path,
            "block_count": len(self.blocks),
            "target_resolution": self.target_resolution.as_dict(),
        }
        if self.grounding_source is not None:
            payload["grounding_source"] = self.grounding_source
        if self.search_range is not None:
            payload["search_range"] = dict(self.search_range)
        if self.edit_range_within_read is not None:
            payload["edit_range_within_read"] = dict(self.edit_range_within_read)
        if self.derived_search_text is not None:
            payload["derived_search_line_count"] = len(self.derived_search_text.splitlines())
            payload["derived_search_bytes"] = len(self.derived_search_text.encode("utf-8"))
        return payload


def is_v2_planner_packet(packet: dict[str, Any] | None) -> bool:
    return str((packet or {}).get("schema_name") or "").strip() == "planner_slice.current"


def validate_v2_edit_against_live_file(
    *,
    workspace_root: str | Path,
    args: dict[str, Any],
    live_file_content: str | None = None,
    metadata: dict[str, Any] | None = None,
    instruction_contract: dict[str, Any] | None = None,
    planner_packet: dict[str, Any] | None = None,
    read_buffers: dict[str, Any] | None = None,
) -> V2LiveFileGroundingResult:
    relative_path = _normalize_relative_path(args.get("relative_path") or args.get("file") or args.get("path"))
    if not relative_path:
        raise V2LiveFileGroundingError("EDIT_FILE_WRONG_TARGET", {"reason": "relative_path_required"})
    declared_grounding_source = _declared_grounding_source(args)
    declared_read_step_idx = _declared_read_step_idx(args)
    forbidden = [
        key
        for key in ("read_source", "step_idx", "buffer_ref", "output_content")
        if args.get(key) not in (None, "", {}, [])
    ]
    if forbidden:
        raise V2LiveFileGroundingError(
            "BUILDER_Current_CONTAMINATION",
            {"file": relative_path, "forbidden_fields": forbidden},
        )
    target_resolution = resolve_target_file(
        workspace_root=workspace_root,
        input_path=relative_path,
        metadata=metadata,
        instruction_contract=instruction_contract,
    )
    if not target_resolution.exists or not target_resolution.resolved_absolute_path:
        raise V2LiveFileGroundingError(
            "EDIT_FILE_TARGET_NOT_FOUND",
            {"file": relative_path, "target_resolution": target_resolution.as_dict()},
        )
    relative_path = target_resolution.normalized_repo_relative_path
    file_content = (
        str(live_file_content)
        if isinstance(live_file_content, str)
        else _read_live_file(
            target_resolution=target_resolution,
        )
    )
    search_range = _coerce_search_range(args.get("search_range"))
    edit_range_within_read = _coerce_search_range(args.get("edit_range_within_read"))
    replace_text = str(args.get("replacement_text") or args.get("replace_text") or args.get("replace") or "")
    if declared_grounding_source == "read_range":
        if search_range is not None:
            raise V2LiveFileGroundingError(
                "EDIT_FILE_READ_RANGE_SEARCH_RANGE_FORBIDDEN",
                {
                    "file": relative_path,
                    "grounding_source": declared_grounding_source,
                    "reason": "read-grounded edit_file must use edit_range_within_read, not search_range",
                },
            )
        if str(args.get("search") or "") or str(args.get("search_replace_blocks") or ""):
            raise V2LiveFileGroundingError(
                "EDIT_FILE_READ_RANGE_SEARCH_FORBIDDEN",
                {
                    "file": relative_path,
                    "grounding_source": declared_grounding_source,
                    "reason": "read-grounded edit_file must not provide planner-authored search text",
                },
            )
        if edit_range_within_read is None:
            raise V2LiveFileGroundingError(
                "EDIT_FILE_MISSING_EDIT_RANGE_WITHIN_READ",
                {
                    "file": relative_path,
                    "grounding_source": declared_grounding_source,
                    "reason": "read-grounded edit_file must declare edit_range_within_read",
                },
            )
        resolved = _resolve_read_grounded_edit(
            relative_path=relative_path,
            file_content=file_content,
            read_buffers=read_buffers,
            declared_read_step_idx=declared_read_step_idx,
            edit_range_within_read=edit_range_within_read,
            replace_text=replace_text,
        )
        return V2LiveFileGroundingResult(
            relative_path=relative_path,
            file_content=file_content,
            blocks=[resolved["block"]],
            mode="read_range_within_read",
            target_resolution=target_resolution,
            grounding_source="explicit_read_output",
            search_range=resolved["absolute_search_range"],
            edit_range_within_read=edit_range_within_read,
            derived_search_text=resolved["derived_search_text"],
        )
    blocks = _extract_blocks(args=args, search_range=search_range, replace_text=replace_text, file_content=file_content)
    if search_range is not None:
        if declared_grounding_source != "anchor":
            raise V2LiveFileGroundingError(
                "EDIT_FILE_INVALID_GROUNDING_SOURCE",
                {
                    "file": relative_path,
                    "grounding_source": declared_grounding_source,
                    "reason": "search_range edits must declare grounding_source=anchor",
                },
            )
        derived = _slice_file_lines(file_content=file_content, search_range=search_range)
        expected = blocks[0].search if blocks else ""
        if expected != derived:
            raise V2LiveFileGroundingError(
                "EDIT_FILE_RANGE_MISMATCH",
                {
                    "file": relative_path,
                    "search_range": dict(search_range),
                    "reason": "search_range did not resolve to the supplied live-file preimage",
                },
            )
        return V2LiveFileGroundingResult(
            relative_path=relative_path,
            file_content=file_content,
            blocks=blocks,
            mode="search_range",
            target_resolution=target_resolution,
            grounding_source="anchor",
            search_range=search_range,
            derived_search_text=derived,
        )
    grounding_source = "anchor"
    for block in blocks:
        matches = find_line_aligned_matches(file_content, block.search)
        if not matches:
            raise V2LiveFileGroundingError(
                "EDIT_FILE_SEARCH_NOT_FOUND",
                {
                    "file": relative_path,
                    "search": str(block.search or "")[:200],
                    "reason": "SEARCH not found in live file",
                },
            )
        if len(matches) > 1:
            raise V2LiveFileGroundingError(
                "EDIT_FILE_SEARCH_AMBIGUOUS",
                {
                    "file": relative_path,
                    "search": str(block.search or "")[:200],
                    "match_count": len(matches),
                    "reason": "SEARCH matched multiple live-file locations",
                },
            )
    return V2LiveFileGroundingResult(
        relative_path=relative_path,
        file_content=file_content,
        blocks=blocks,
        mode="search",
        target_resolution=target_resolution,
        grounding_source=grounding_source,
    )


def _normalize_relative_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").strip("/")


def _declared_grounding_source(args: dict[str, Any]) -> str:
    value = str(args.get("grounding_source") or "").strip().lower()
    if value not in {"anchor", "read_range"}:
        raise V2LiveFileGroundingError(
            "EDIT_FILE_MISSING_GROUNDING_SOURCE",
            {
                "file": _normalize_relative_path(args.get("relative_path") or args.get("file") or args.get("path")),
                "reason": "edit_file must declare grounding_source=anchor or grounding_source=read_range",
            },
        )
    return value


def _declared_read_step_idx(args: dict[str, Any]) -> int | None:
    value = args.get("read_step_idx")
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception as exc:
        raise V2LiveFileGroundingError(
            "EDIT_FILE_INVALID_READ_PROVENANCE",
            {"read_step_idx": value, "reason": "read_step_idx must be an integer"},
        ) from exc


def _read_live_file(*, target_resolution: TargetFileResolutionResult) -> str:
    target_path = str(target_resolution.resolved_absolute_path or "").strip()
    if not target_path:
        raise V2LiveFileGroundingError(
            "EDIT_FILE_TARGET_NOT_FOUND",
            {"target_resolution": target_resolution.as_dict()},
        )
    target = Path(target_path)
    if not target.exists():
        raise V2LiveFileGroundingError(
            "EDIT_FILE_TARGET_NOT_FOUND",
            {"target_resolution": target_resolution.as_dict()},
        )
    if not target.is_file():
        raise V2LiveFileGroundingError(
            "EDIT_FILE_WRONG_TARGET",
            {
                "file": target_resolution.normalized_repo_relative_path,
                "reason": "not_a_file",
                "target_resolution": target_resolution.as_dict(),
            },
        )
    return target.read_text(encoding="utf-8", errors="replace")


def _coerce_search_range(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    try:
        start_line = int(str(value.get("start_line")))
        end_line = int(str(value.get("end_line")))
    except Exception as exc:
        raise V2LiveFileGroundingError(
            "EDIT_FILE_RANGE_INVALID",
            {"search_range": dict(value), "reason": "search_range lines must be integers"},
        ) from exc
    if start_line < 1 or end_line < start_line:
        raise V2LiveFileGroundingError(
            "EDIT_FILE_RANGE_INVALID",
            {
                "search_range": {"start_line": start_line, "end_line": end_line},
                "reason": "search_range must be 1-based and end_line >= start_line",
            },
        )
    return {"start_line": start_line, "end_line": end_line}


def _slice_file_lines(*, file_content: str, search_range: dict[str, int]) -> str:
    lines = str(file_content or "").splitlines(keepends=True)
    start_idx = int(search_range["start_line"]) - 1
    end_idx = int(search_range["end_line"])
    if start_idx >= len(lines) or start_idx < 0:
        raise V2LiveFileGroundingError(
            "EDIT_FILE_RANGE_INVALID",
            {"search_range": dict(search_range), "reason": "search_range start_line exceeds live file"},
        )
    slice_text = "".join(lines[start_idx:end_idx])
    if not slice_text:
        raise V2LiveFileGroundingError(
            "EDIT_FILE_RANGE_EMPTY",
            {"search_range": dict(search_range), "reason": "search_range resolved to empty live-file slice"},
        )
    return slice_text


def _extract_blocks(
    *,
    args: dict[str, Any],
    search_range: dict[str, int] | None,
    replace_text: str,
    file_content: str,
) -> list[SearchReplaceBlock]:
    if str(args.get("search_replace_blocks") or ""):
        raise V2LiveFileGroundingError(
            "EDIT_FILE_TEXTUAL_PATCH_BLOCKS_FORBIDDEN",
            {"reason": "edit_file must use structured ranges plus replacement_text only"},
        )
    if str(args.get("search") or ""):
        raise V2LiveFileGroundingError(
            "EDIT_FILE_TEXTUAL_SEARCH_FORBIDDEN",
            {"reason": "edit_file must use structured ranges plus replacement_text only"},
        )
    if search_range is not None:
        derived = _slice_file_lines(file_content=file_content, search_range=search_range)
        return [SearchReplaceBlock(index=1, search=derived, replace=replace_text)]
    raise V2LiveFileGroundingError(
        "EDIT_FILE_EMPTY",
        {"reason": "edit_file missing structured search_range or edit_range_within_read"},
    )


def _resolve_read_grounded_edit(
    *,
    relative_path: str,
    file_content: str,
    read_buffers: dict[str, Any] | None,
    declared_read_step_idx: int | None,
    edit_range_within_read: dict[str, int],
    replace_text: str,
) -> dict[str, Any]:
    normalized_buffers = _normalize_read_buffers(read_buffers)
    read_buffer_entry = normalized_buffers.get(relative_path) or {}
    read_buffer_text = str(read_buffer_entry.get("text") or "")
    read_buffer_step_idx = read_buffer_entry.get("step_idx")
    read_start_line = read_buffer_entry.get("start_line")
    if declared_read_step_idx is None:
        raise V2LiveFileGroundingError(
            "EDIT_FILE_MISSING_READ_PROVENANCE",
            {
                "file": relative_path,
                "grounding_source": "read_range",
                "reason": "read-grounded edits must declare read_step_idx for the same-file read_range source",
            },
        )
    if not read_buffer_text or not isinstance(read_buffer_step_idx, int):
        raise V2LiveFileGroundingError(
            "EDIT_FILE_READ_PROVENANCE_UNAVAILABLE",
            {
                "file": relative_path,
                "grounding_source": "read_range",
                "read_step_idx": declared_read_step_idx,
                "reason": "same-file read_range output is unavailable for the declared read_step_idx",
            },
        )
    if read_buffer_step_idx != declared_read_step_idx:
        raise V2LiveFileGroundingError(
            "EDIT_FILE_READ_PROVENANCE_MISMATCH",
            {
                "file": relative_path,
                "grounding_source": "read_range",
                "read_step_idx": declared_read_step_idx,
                "available_read_step_idx": read_buffer_step_idx,
                "reason": "edit_file must cite the same-file read_range step that produced the observed edit span",
            },
        )
    if not isinstance(read_start_line, int):
        raise V2LiveFileGroundingError(
            "EDIT_FILE_READ_PROVENANCE_UNAVAILABLE",
            {
                "file": relative_path,
                "grounding_source": "read_range",
                "read_step_idx": declared_read_step_idx,
                "reason": "same-file read_range bounds are unavailable for edit_range_within_read",
            },
        )
    derived_search_text = _slice_file_lines(file_content=read_buffer_text, search_range=edit_range_within_read)
    absolute_search_range = {
        "start_line": read_start_line + int(edit_range_within_read["start_line"]) - 1,
        "end_line": read_start_line + int(edit_range_within_read["end_line"]) - 1,
    }
    live_slice = _slice_file_lines(file_content=file_content, search_range=absolute_search_range)
    if live_slice != derived_search_text:
        raise V2LiveFileGroundingError(
            "EDIT_FILE_READ_RANGE_DIVERGED",
            {
                "file": relative_path,
                "grounding_source": "read_range",
                "read_step_idx": declared_read_step_idx,
                "edit_range_within_read": dict(edit_range_within_read),
                "search_range": dict(absolute_search_range),
                "reason": "edit_range_within_read no longer matches the live file slice",
            },
        )
    return {
        "block": SearchReplaceBlock(index=1, search=derived_search_text, replace=replace_text),
        "absolute_search_range": absolute_search_range,
        "derived_search_text": derived_search_text,
    }


def _validate_grounded_search_source(
    *,
    relative_path: str,
    blocks: list[SearchReplaceBlock],
    planner_packet: dict[str, Any] | None,
    read_buffers: dict[str, Any] | None,
    declared_grounding_source: str,
    declared_read_step_idx: int | None,
) -> str:
    compact_anchor = _planner_packet_compact_anchor_snippet(planner_packet)
    normalized_buffers = _normalize_read_buffers(read_buffers)
    read_buffer_entry = normalized_buffers.get(relative_path) or {}
    read_buffer_text = str(read_buffer_entry.get("text") or "")
    read_buffer_step_idx = read_buffer_entry.get("step_idx")
    if declared_grounding_source == "anchor":
        if declared_read_step_idx is not None:
            raise V2LiveFileGroundingError(
                "EDIT_FILE_INVALID_READ_PROVENANCE",
                {
                    "file": relative_path,
                    "grounding_source": "anchor",
                    "read_step_idx": declared_read_step_idx,
                    "reason": "anchor-grounded edits may not declare read_step_idx",
                },
            )
        for block in blocks:
            search_text = str(block.search or "")
            if compact_anchor and _search_present_in_observed_source(
                search_text=search_text, observed_text=compact_anchor
            ):
                continue
            raise V2LiveFileGroundingError(
                "EDIT_FILE_UNGROUNDED_SEARCH",
                {
                    "file": relative_path,
                    "search": search_text[:200],
                    "grounding_source": "anchor",
                    "reason": "SEARCH must be copied verbatim from compact anchor when grounding_source=anchor",
                    "anchor_available": bool(compact_anchor),
                    "read_output_available": bool(read_buffer_text),
                },
            )
        return "compact_anchor"
    if declared_read_step_idx is None:
        raise V2LiveFileGroundingError(
            "EDIT_FILE_MISSING_READ_PROVENANCE",
            {
                "file": relative_path,
                "grounding_source": "read_range",
                "reason": "read-grounded edits must declare read_step_idx for the same-file read_range source",
            },
        )
    if not read_buffer_text or not isinstance(read_buffer_step_idx, int):
        raise V2LiveFileGroundingError(
            "EDIT_FILE_READ_PROVENANCE_UNAVAILABLE",
            {
                "file": relative_path,
                "grounding_source": "read_range",
                "read_step_idx": declared_read_step_idx,
                "reason": "same-file read_range output is unavailable for the declared read_step_idx",
            },
        )
    if read_buffer_step_idx != declared_read_step_idx:
        raise V2LiveFileGroundingError(
            "EDIT_FILE_READ_PROVENANCE_MISMATCH",
            {
                "file": relative_path,
                "grounding_source": "read_range",
                "read_step_idx": declared_read_step_idx,
                "available_read_step_idx": read_buffer_step_idx,
                "reason": "edit_file must cite the same-file read_range step that produced the observed search text",
            },
        )
    for block in blocks:
        search_text = str(block.search or "")
        if read_buffer_text and _search_present_in_observed_source(
            search_text=search_text, observed_text=read_buffer_text
        ):
            continue
        raise V2LiveFileGroundingError(
            "EDIT_FILE_UNGROUNDED_SEARCH",
            {
                "file": relative_path,
                "search": search_text[:200],
                "grounding_source": "read_range",
                "reason": "SEARCH must be copied verbatim from explicit same-file read output when grounding_source=read_range",
                "anchor_available": bool(compact_anchor),
                "read_output_available": bool(read_buffer_text),
            },
        )
    return "explicit_read_output"


def _search_present_in_observed_source(*, search_text: str, observed_text: str) -> bool:
    candidate = str(search_text or "").replace("\r\n", "\n").replace("\r", "\n")
    observed = str(observed_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not candidate or not observed:
        return False
    if candidate in observed:
        return True
    stripped_candidate = candidate.rstrip("\n")
    return bool(stripped_candidate) and stripped_candidate in observed


def _planner_packet_compact_anchor_snippet(packet: dict[str, Any] | None) -> str:
    allowed_surface = dict((packet or {}).get("allowed_surface") or {})
    anchor = dict(allowed_surface.get("anchor") or {})
    return str(anchor.get("compact_snippet") or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def _normalize_read_buffers(read_buffers: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for raw_path, raw_value in dict(read_buffers or {}).items():
        path = _normalize_relative_path(raw_path)
        if not path:
            continue
        text = ""
        step_idx: int | None = None
        if isinstance(raw_value, dict):
            text = str(raw_value.get("text") or raw_value.get("content") or "")
            try:
                step_idx = int(str(raw_value.get("step_idx"))) if raw_value.get("step_idx") is not None else None
            except Exception:
                step_idx = None
        elif isinstance(raw_value, str):
            text = raw_value
        if text:
            entry: dict[str, Any] = {"text": str(text)}
            if step_idx is not None:
                entry["step_idx"] = step_idx
            try:
                start_line = (
                    int(str(raw_value.get("start_line")))
                    if isinstance(raw_value, dict) and raw_value.get("start_line") is not None
                    else None
                )
            except Exception:
                start_line = None
            try:
                end_line = (
                    int(str(raw_value.get("end_line")))
                    if isinstance(raw_value, dict) and raw_value.get("end_line") is not None
                    else None
                )
            except Exception:
                end_line = None
            if start_line is not None:
                entry["start_line"] = start_line
            if end_line is not None:
                entry["end_line"] = end_line
            normalized[path] = entry
    return normalized
