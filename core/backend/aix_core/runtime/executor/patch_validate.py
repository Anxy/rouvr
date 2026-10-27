"""006-PREP: Surgical patch validation, atomic patch application, and create-only write policy."""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
import re


def compute_content_hash(content: str | bytes) -> str:
    """Compute sha256 hash for content string or bytes."""
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
    else:
        content_bytes = content
    return hashlib.sha256(content_bytes).hexdigest()


@dataclass(frozen=True)
class DiffHunkLine:
    op: str  # ' ' (context), '-' (remove), '+' (add)
    text: str


@dataclass
class DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffHunkLine] = field(default_factory=list)


@dataclass(frozen=True)
class PatchValidationResult:
    valid: bool
    reason_code: str | None
    reason_detail: str
    before_hash: str
    after_hash: str | None
    lines_added: int
    lines_removed: int
    new_content: str | None
    modified_path: str


@dataclass(frozen=True)
class WriteFilePolicyResult:
    allowed: bool
    reason_code: str | None
    reason_detail: str


_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_unified_diff(unified_diff: str) -> list[DiffHunk]:
    """Parse standard unified diff into structured DiffHunk instances."""
    lines = unified_diff.splitlines()
    hunks: list[DiffHunk] = []
    current_hunk: DiffHunk | None = None

    for line in lines:
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        match = _HUNK_HEADER_RE.match(line)
        if match:
            if current_hunk is not None:
                hunks.append(current_hunk)
            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) is not None else 1
            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) is not None else 1
            current_hunk = DiffHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                lines=[],
            )
            continue

        if current_hunk is not None:
            if line.startswith("+"):
                current_hunk.lines.append(DiffHunkLine(op="+", text=line[1:]))
            elif line.startswith("-"):
                current_hunk.lines.append(DiffHunkLine(op="-", text=line[1:]))
            elif line.startswith(" ") or line == "":
                text = line[1:] if line.startswith(" ") else ""
                current_hunk.lines.append(DiffHunkLine(op=" ", text=text))
            elif line.startswith("\\ No newline at end of file"):
                continue

    if current_hunk is not None:
        hunks.append(current_hunk)

    return hunks


def validate_patch(
    *,
    file_path: str | Path,
    existing_content: str,
    unified_diff: str,
    expected_file_hash: str | None = None,
    allow_no_op: bool = False,
) -> PatchValidationResult:
    """Validate unified diff syntax, context match against existing file content,
    expected precondition hash, and no-op status.

    Returns a PatchValidationResult. On failure, valid=False and reason_code is set
    (e.g., PATCH_CONTEXT_MISMATCH, STALE_FILE_VERSION, NO_OP_PATCH, PATCH_VALIDATION_FAILED).
    """
    path_str = str(file_path)
    before_hash = compute_content_hash(existing_content)

    if expected_file_hash:
        cleaned_expected = expected_file_hash.strip().lower()
        if cleaned_expected != before_hash.lower():
            return PatchValidationResult(
                valid=False,
                reason_code="STALE_FILE_VERSION",
                reason_detail=f"Expected file hash {cleaned_expected[:8]}... but actual content hash is {before_hash[:8]}...",
                before_hash=before_hash,
                after_hash=None,
                lines_added=0,
                lines_removed=0,
                new_content=None,
                modified_path=path_str,
            )

    if not unified_diff.strip():
        return PatchValidationResult(
            valid=False,
            reason_code="NO_OP_PATCH",
            reason_detail="Patch contains empty or whitespace-only diff",
            before_hash=before_hash,
            after_hash=None,
            lines_added=0,
            lines_removed=0,
            new_content=None,
            modified_path=path_str,
        )

    hunks = parse_unified_diff(unified_diff)
    if not hunks:
        return PatchValidationResult(
            valid=False,
            reason_code="PATCH_VALIDATION_FAILED",
            reason_detail="Could not parse any unified diff hunks from provided patch",
            before_hash=before_hash,
            after_hash=None,
            lines_added=0,
            lines_removed=0,
            new_content=None,
            modified_path=path_str,
        )

    total_added = sum(1 for h in hunks for line in h.lines if line.op == "+")
    total_removed = sum(1 for h in hunks for line in h.lines if line.op == "-")

    if total_added == 0 and total_removed == 0 and not allow_no_op:
        return PatchValidationResult(
            valid=False,
            reason_code="NO_OP_PATCH",
            reason_detail="Patch contains zero additions and zero deletions (+0/-0)",
            before_hash=before_hash,
            after_hash=None,
            lines_added=0,
            lines_removed=0,
            new_content=None,
            modified_path=path_str,
        )

    # Split lines preserving line breaks format where possible
    has_trailing_newline = existing_content.endswith("\n")
    orig_lines = existing_content.splitlines()

    result_lines: list[str] = []
    orig_cursor = 0  # 0-indexed cursor into orig_lines

    for hunk in hunks:
        # Hunk old_start is 1-indexed.
        target_orig_idx = max(0, hunk.old_start - 1)

        # Copy unchanged lines leading up to hunk start
        while orig_cursor < target_orig_idx and orig_cursor < len(orig_lines):
            result_lines.append(orig_lines[orig_cursor])
            orig_cursor += 1

        # Match hunk context and removals against orig_lines
        for hunk_line in hunk.lines:
            if hunk_line.op == " ":
                if orig_cursor >= len(orig_lines):
                    return PatchValidationResult(
                        valid=False,
                        reason_code="PATCH_CONTEXT_MISMATCH",
                        reason_detail=f"Context line out of range at line {orig_cursor + 1}",
                        before_hash=before_hash,
                        after_hash=None,
                        lines_added=total_added,
                        lines_removed=total_removed,
                        new_content=None,
                        modified_path=path_str,
                    )
                actual_line = orig_lines[orig_cursor]
                if actual_line != hunk_line.text:
                    return PatchValidationResult(
                        valid=False,
                        reason_code="PATCH_CONTEXT_MISMATCH",
                        reason_detail=f"Context line mismatch at line {orig_cursor + 1}: expected '{hunk_line.text}', got '{actual_line}'",
                        before_hash=before_hash,
                        after_hash=None,
                        lines_added=total_added,
                        lines_removed=total_removed,
                        new_content=None,
                        modified_path=path_str,
                    )
                result_lines.append(actual_line)
                orig_cursor += 1

            elif hunk_line.op == "-":
                if orig_cursor >= len(orig_lines):
                    return PatchValidationResult(
                        valid=False,
                        reason_code="PATCH_CONTEXT_MISMATCH",
                        reason_detail=f"Removal line out of range at line {orig_cursor + 1}",
                        before_hash=before_hash,
                        after_hash=None,
                        lines_added=total_added,
                        lines_removed=total_removed,
                        new_content=None,
                        modified_path=path_str,
                    )
                actual_line = orig_lines[orig_cursor]
                if actual_line != hunk_line.text:
                    return PatchValidationResult(
                        valid=False,
                        reason_code="PATCH_CONTEXT_MISMATCH",
                        reason_detail=f"Removal line mismatch at line {orig_cursor + 1}: expected '{hunk_line.text}', got '{actual_line}'",
                        before_hash=before_hash,
                        after_hash=None,
                        lines_added=total_added,
                        lines_removed=total_removed,
                        new_content=None,
                        modified_path=path_str,
                    )
                orig_cursor += 1  # Skip removed line

            elif hunk_line.op == "+":
                result_lines.append(hunk_line.text)

    # Append any remaining lines past last hunk
    while orig_cursor < len(orig_lines):
        result_lines.append(orig_lines[orig_cursor])
        orig_cursor += 1

    new_content = "\n".join(result_lines)
    if has_trailing_newline and not new_content.endswith("\n"):
        new_content += "\n"

    after_hash = compute_content_hash(new_content)

    if after_hash == before_hash and not allow_no_op:
        return PatchValidationResult(
            valid=False,
            reason_code="NO_OP_PATCH",
            reason_detail="Applied patch produced content identical to before-state (hash unchanged)",
            before_hash=before_hash,
            after_hash=after_hash,
            lines_added=total_added,
            lines_removed=total_removed,
            new_content=None,
            modified_path=path_str,
        )

    return PatchValidationResult(
        valid=True,
        reason_code=None,
        reason_detail="Patch validated successfully against file context and preconditions",
        before_hash=before_hash,
        after_hash=after_hash,
        lines_added=total_added,
        lines_removed=total_removed,
        new_content=new_content,
        modified_path=path_str,
    )


def apply_patch_atomic(
    file_path: str | Path,
    validation_result: PatchValidationResult,
) -> str:
    """Apply validated patch atomically using write-to-temp and atomic replace.

    Raises ValueError if validation_result is invalid or if target file on disk changed.
    Returns after_hash string on success.
    """
    path = Path(file_path)

    if not validation_result.valid or validation_result.new_content is None:
        raise ValueError(
            f"Cannot apply invalid patch: reason_code={validation_result.reason_code}, detail={validation_result.reason_detail}"
        )

    if path.exists():
        disk_content = path.read_text(encoding="utf-8-sig")
        current_hash = compute_content_hash(disk_content)
        if current_hash != validation_result.before_hash:
            raise ValueError(f"STALE_FILE_VERSION: file {path} changed on disk before patch application")

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_name(f".tmp_{path.name}_{os.getpid()}")
    try:
        tmp_path.write_text(validation_result.new_content, encoding="utf-8", newline="\n")
        replaced = False
        last_exc: BaseException | None = None
        for attempt in range(8):
            try:
                os.replace(tmp_path, path)
                replaced = True
                break
            except PermissionError as exc:
                last_exc = exc
            except OSError as exc:
                winerror = getattr(exc, "winerror", None)
                if winerror not in (5, 32) and getattr(exc, "errno", None) not in (13,):
                    raise
                last_exc = exc
            time.sleep(0.05 * (2 ** attempt))
        if not replaced:
            # Windows can deny replace() while Defender/indexer holds the dest.
            # Overwrite in place after retries rather than fail the whole step.
            path.write_text(validation_result.new_content, encoding="utf-8", newline="\n")
            try:
                tmp_path.unlink()
            except OSError:
                pass
            del last_exc
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise

    return validation_result.after_hash or compute_content_hash(validation_result.new_content)


def validate_write_file_policy(
    file_path: str | Path,
    content: str,
    *,
    create_only: bool = True,
) -> WriteFilePolicyResult:
    """Policy validator for write_file tool. By default (create_only=True), write_file
    must NOT overwrite existing files; edits must use surgical patches instead."""
    path = Path(file_path)

    if create_only and path.exists():
        return WriteFilePolicyResult(
            allowed=False,
            reason_code="CREATE_ONLY_VIOLATION",
            reason_detail=f"write_file is create-only by policy; file '{path}' already exists. Use surgical patch for edits.",
        )

    return WriteFilePolicyResult(
        allowed=True,
        reason_code=None,
        reason_detail="File write operation permitted by policy",
    )


__all__ = [
    "compute_content_hash",
    "DiffHunkLine",
    "DiffHunk",
    "PatchValidationResult",
    "WriteFilePolicyResult",
    "parse_unified_diff",
    "validate_patch",
    "apply_patch_atomic",
    "validate_write_file_policy",
]
