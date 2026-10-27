"""Durable cursor for the maintained AIX event consumer.

The cursor file is the restart-safe dedupe record: every processed event id
maps to the Temporal workflow it produced. Writes are atomic (tmp + rename)
so a crash never leaves a half-written cursor; a fresh process reloads it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

CURSOR_VERSION = 1


class DurableCursor:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._processed: dict[str, dict[str, Any]] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._path.is_file():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        processed = payload.get("processed")
        if isinstance(processed, dict):
            for key, value in processed.items():
                if isinstance(value, dict):
                    self._processed[str(key)] = dict(value)

    def is_processed(self, event_id: str) -> bool:
        self._ensure_loaded()
        return event_id in self._processed

    def record(self, event_id: str, *, workflow_id: str, event: Mapping[str, Any] | None = None) -> None:
        self._ensure_loaded()
        entry: dict[str, Any] = {"workflow_id": workflow_id}
        if event is not None:
            entry["event"] = dict(event)
        self._processed[event_id] = entry
        self._save()

    def processed_ids(self) -> list[str]:
        self._ensure_loaded()
        return sorted(self._processed)

    def entry(self, event_id: str) -> dict[str, Any] | None:
        self._ensure_loaded()
        found = self._processed.get(event_id)
        return dict(found) if found is not None else None

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(
            json.dumps({"version": CURSOR_VERSION, "processed": self._processed}, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)
