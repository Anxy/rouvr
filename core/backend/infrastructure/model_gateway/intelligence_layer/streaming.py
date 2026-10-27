"""W03: SSE helpers — Integration chat is non-streaming today; emit chunked events."""

from __future__ import annotations

import json
from collections.abc import Iterator


def chunk_text(text: str, *, size: int = 48) -> Iterator[str]:
    value = str(text or "")
    step = max(8, int(size))
    for idx in range(0, len(value), step):
        yield value[idx : idx + step]


def sse_events_for_reply(reply: str, *, meta: dict | None = None) -> Iterator[str]:
    """Yield SSE lines for a completed reply (pseudo-stream for FE progress UX)."""
    yield f"event: meta\ndata: {json.dumps(meta or {})}\n\n"
    for chunk in chunk_text(reply):
        yield f"event: token\ndata: {json.dumps({'text': chunk})}\n\n"
    yield f"event: done\ndata: {json.dumps({'ok': True})}\n\n"
