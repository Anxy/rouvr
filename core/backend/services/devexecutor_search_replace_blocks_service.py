from __future__ import annotations

import textwrap
from dataclasses import dataclass
from difflib import SequenceMatcher

SEARCH_MARKER = "<<<<<<< SEARCH"
DIVIDER_MARKER = "======="
REPLACE_MARKER = ">>>>>>> REPLACE"
_FUZZY_MATCH_THRESHOLD = 0.92
_FUZZY_AMBIGUITY_DELTA = 0.01


class SearchReplaceBlockError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code or "").strip() or "SEARCH_REPLACE_INVALID"
        self.message = str(message or "").strip() or self.code


@dataclass(frozen=True, slots=True)
class SearchReplaceBlock:
    index: int
    search: str
    replace: str


@dataclass(frozen=True, slots=True)
class SearchReplaceMatch:
    start: int
    end: int
    mode: str = "exact"
    similarity: float = 1.0


def parse_search_replace_blocks(block_text: str) -> list[SearchReplaceBlock]:
    text = str(block_text or "")
    lines = text.splitlines(keepends=True)
    index = 0
    blocks: list[SearchReplaceBlock] = []
    outside_chunks: list[str] = []

    while index < len(lines):
        line = lines[index]
        if line.strip() == SEARCH_MARKER:
            index += 1
            search_lines: list[str] = []
            while index < len(lines) and lines[index].strip() != DIVIDER_MARKER:
                search_lines.append(lines[index])
                index += 1
            if index >= len(lines):
                raise SearchReplaceBlockError(
                    "SEARCH_REPLACE_MISSING_DIVIDER",
                    "SEARCH/REPLACE block is missing ======= divider",
                )
            index += 1
            replace_lines: list[str] = []
            while index < len(lines) and lines[index].strip() != REPLACE_MARKER:
                replace_lines.append(lines[index])
                index += 1
            if index >= len(lines):
                raise SearchReplaceBlockError(
                    "SEARCH_REPLACE_MISSING_END",
                    "SEARCH/REPLACE block is missing >>>>>>> REPLACE terminator",
                )
            index += 1
            block_index = len(blocks) + 1
            search = "".join(search_lines)
            replace = "".join(replace_lines)
            if not search:
                raise SearchReplaceBlockError(
                    "SEARCH_REPLACE_EMPTY_SEARCH",
                    f"SEARCH/REPLACE block #{block_index} has empty SEARCH text",
                )
            blocks.append(SearchReplaceBlock(index=block_index, search=search, replace=replace))
            continue
        outside_chunks.append(line)
        index += 1

    if not blocks:
        raise SearchReplaceBlockError(
            "SEARCH_REPLACE_NO_BLOCKS",
            "No SEARCH/REPLACE blocks were found in edit_file input",
        )

    outside_text = "".join(outside_chunks).strip()
    if outside_text:
        raise SearchReplaceBlockError(
            "SEARCH_REPLACE_UNEXPECTED_TEXT",
            "SEARCH/REPLACE input contains unexpected text outside blocks",
        )
    return blocks


def apply_search_replace_blocks(
    *, original_text: str, blocks: list[SearchReplaceBlock]
) -> tuple[str, list[dict[str, int | bool | str | float]]]:
    current = str(original_text or "")
    outcomes: list[dict[str, int | bool | str | float]] = []
    for block in blocks:
        matches = find_line_aligned_matches(current, block.search)
        if not matches:
            raise SearchReplaceBlockError(
                "SEARCH_REPLACE_NOT_FOUND",
                f"SEARCH text for block #{block.index} was not matchable in the target file",
            )
        if len(matches) > 1:
            raise SearchReplaceBlockError(
                "SEARCH_REPLACE_AMBIGUOUS",
                f"SEARCH text for block #{block.index} matched multiple locations in the target file",
            )
        no_op = block.search == block.replace
        match = matches[0]
        start, end = match.start, match.end
        replace_text = block.replace
        if match.mode == "normalized_indentation":
            replace_text = _reindent_replace_text(
                replace=block.replace,
                search=block.search,
                matched_text=current[start:end],
            )
        current = current[:start] + replace_text + current[end:]
        outcome: dict[str, int | bool | str | float] = {
            "index": block.index,
            "match_count": len(matches),
            "no_op": no_op,
        }
        if match.mode != "exact":
            outcome["match_mode"] = match.mode
            outcome["similarity"] = round(match.similarity, 4)
        outcomes.append(outcome)
    return current, outcomes


def find_line_aligned_matches(text: str, search: str) -> list[SearchReplaceMatch]:
    exact_matches = _find_candidate_matches(text=text, candidate=search)
    if exact_matches:
        return [SearchReplaceMatch(start=start, end=end) for start, end in exact_matches]
    if search.endswith("\n") and not search.endswith("\r\n"):
        trimmed_matches = _find_candidate_matches(text=text, candidate=search[:-1])
        if trimmed_matches:
            return [SearchReplaceMatch(start=start, end=end) for start, end in trimmed_matches]
    indentation_matches = _find_indentation_normalized_matches(text=text, search=search)
    if indentation_matches:
        return indentation_matches
    return _find_fuzzy_line_aligned_matches(text=text, search=search)


def _find_fuzzy_line_aligned_matches(*, text: str, search: str) -> list[SearchReplaceMatch]:
    search_lines = str(search or "").splitlines(keepends=True)
    if len(search_lines) < 2:
        return []
    if search.endswith("\n") and not search.endswith("\r\n") and search_lines and not search_lines[-1].endswith("\n"):
        search_lines[-1] = search_lines[-1] + "\n"
    text_lines = text.splitlines(keepends=True)
    if len(search_lines) > len(text_lines):
        return []
    offsets = _line_offsets(text_lines)
    normalized_search = _normalize_match_text("".join(search_lines))
    candidates: list[SearchReplaceMatch] = []
    for start_index in range(0, len(text_lines) - len(search_lines) + 1):
        window = text_lines[start_index : start_index + len(search_lines)]
        similarity = SequenceMatcher(None, normalized_search, _normalize_match_text("".join(window))).ratio()
        if similarity < _FUZZY_MATCH_THRESHOLD:
            continue
        start = offsets[start_index]
        end = offsets[start_index + len(search_lines)]
        candidates.append(
            SearchReplaceMatch(
                start=start,
                end=end,
                mode="fuzzy_whitespace",
                similarity=similarity,
            )
        )
    if not candidates:
        return []
    candidates.sort(key=lambda item: item.similarity, reverse=True)
    best = candidates[0]
    ambiguous = [
        candidate for candidate in candidates[1:] if (best.similarity - candidate.similarity) <= _FUZZY_AMBIGUITY_DELTA
    ]
    if ambiguous:
        return [best, *ambiguous]
    return [best]


def _find_indentation_normalized_matches(*, text: str, search: str) -> list[SearchReplaceMatch]:
    search_lines = str(search or "").splitlines(keepends=True)
    if not search_lines:
        return []
    text_lines = text.splitlines(keepends=True)
    if len(search_lines) > len(text_lines):
        return []
    if len(search_lines) == 1 and not _allow_single_line_indentation_normalized_match(search_lines[0]):
        return []
    normalized_search = _normalize_indentation_match_text("".join(search_lines))
    if not normalized_search:
        return []
    offsets = _line_offsets(text_lines)
    matches: list[SearchReplaceMatch] = []
    for start_index in range(0, len(text_lines) - len(search_lines) + 1):
        window = text_lines[start_index : start_index + len(search_lines)]
        if _normalize_indentation_match_text("".join(window)) != normalized_search:
            continue
        start = offsets[start_index]
        end = offsets[start_index + len(search_lines)]
        matches.append(SearchReplaceMatch(start=start, end=end, mode="normalized_indentation"))
    return matches


def _allow_single_line_indentation_normalized_match(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return False
    if stripped.startswith("@"):
        return True
    if stripped.startswith(("def ", "class ", "async def ")):
        return stripped.endswith(":")
    return False


def _line_offsets(lines: list[str]) -> list[int]:
    offsets = [0]
    total = 0
    for line in lines:
        total += len(line)
        offsets.append(total)
    return offsets


def _normalize_match_text(text: str) -> str:
    return "\n".join(line.strip() for line in str(text or "").replace("\r\n", "\n").split("\n"))


def _normalize_indentation_match_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n")
    trailing_newline = normalized.endswith("\n")
    body = textwrap.dedent(normalized)
    if trailing_newline and not body.endswith("\n"):
        body += "\n"
    return body


def _common_indentation_width(text: str) -> int:
    widths = [
        len(line) - len(line.lstrip(" ")) for line in str(text or "").replace("\r\n", "\n").split("\n") if line.strip()
    ]
    return min(widths) if widths else 0


def _shift_indentation(line: str, delta: int) -> str:
    if not line.strip() or delta == 0:
        return line
    if delta > 0:
        return (" " * delta) + line
    remove = min(len(line) - len(line.lstrip(" ")), abs(delta))
    return line[remove:]


def _reindent_replace_text(*, replace: str, search: str, matched_text: str) -> str:
    delta = _common_indentation_width(matched_text) - _common_indentation_width(search)
    if delta == 0:
        return replace
    lines = str(replace or "").splitlines(keepends=True)
    return "".join(_shift_indentation(line, delta) for line in lines)


def find_anchor_matches(*, text: str, anchor_text: str) -> list[SearchReplaceMatch]:
    return find_line_aligned_matches(text, anchor_text)


def _find_candidate_matches(*, text: str, candidate: str) -> list[tuple[int, int]]:
    if not candidate:
        return []
    matches: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(candidate, start)
        if index < 0:
            break
        end = index + len(candidate)
        if _is_line_aligned(text=text, start=index, end=end):
            matches.append((index, end))
        start = index + 1
    return matches


def _is_line_aligned(*, text: str, start: int, end: int) -> bool:
    if start > 0 and text[start - 1] != "\n":
        return False
    if end >= len(text):
        return True
    if end > start and text[end - 1] == "\n":
        return True
    if text[end] != "\n":
        return False
    return True


__all__ = [
    "DIVIDER_MARKER",
    "REPLACE_MARKER",
    "SEARCH_MARKER",
    "SearchReplaceBlock",
    "SearchReplaceBlockError",
    "SearchReplaceMatch",
    "apply_search_replace_blocks",
    "find_anchor_matches",
    "find_line_aligned_matches",
    "parse_search_replace_blocks",
]
