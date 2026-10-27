from __future__ import annotations

import hashlib
import json
import shlex
from collections.abc import Iterable
from typing import Any


def command_from_argv(argv: Iterable[Any]) -> dict[str, Any]:
    tokens = [str(item) for item in list(argv or [])]
    canonical = json.dumps(tokens, ensure_ascii=True, separators=(",", ":"))
    return {
        "argv": tokens,
        "shell": shlex.join(tokens),
        "hash": f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}",
    }


def command_from_shell(command: Any) -> dict[str, Any] | None:
    text = str(command or "").strip()
    if not text:
        return None
    try:
        argv = shlex.split(text, posix=True)
    except ValueError:
        return None
    if not argv:
        return None
    return command_from_argv(argv)


def coerce_command(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        raw_argv = value.get("argv")
        if isinstance(raw_argv, list):
            return command_from_argv(raw_argv)
        raw_command = value.get("command")
        if isinstance(raw_command, list):
            return command_from_argv(raw_command)
        return command_from_shell(value.get("shell") or value.get("command_text") or value.get("command"))
    if isinstance(value, (list, tuple)):
        return command_from_argv(value)
    return command_from_shell(value)


def coerce_command_list(values: Any) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in list(values or []):
        command = coerce_command(value)
        if not command:
            continue
        command_hash = str(command.get("hash") or "")
        if not command_hash or command_hash in seen:
            continue
        seen.add(command_hash)
        commands.append(command)
    return commands


def command_hashes(commands: Iterable[dict[str, Any]]) -> set[str]:
    return {str(command.get("hash") or "") for command in commands if str(command.get("hash") or "")}


__all__ = [
    "coerce_command",
    "coerce_command_list",
    "command_from_argv",
    "command_from_shell",
    "command_hashes",
]
