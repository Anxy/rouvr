from __future__ import annotations

from typing import Literal, TypedDict


class ReadSource(TypedDict, total=False):
    step_idx: int
    file: str
    line_start: int
    line_end: int


class EditFileOp(TypedDict):
    type: Literal["edit_file"]
    file: str
    search: str
    replace: str
    read_source: ReadSource


class FailureAnalysisOp(TypedDict):
    type: Literal["failure_analysis"]
    reason_code: Literal["INSUFFICIENT_CONTEXT_FOR_MUTATION"]
    detail: dict
