"""C032: data/config/migration/environment inventory (secret-safe)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


class InventoryError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


EffectClass = Literal["code", "config", "migration", "deployment", "secret_metadata"]


@dataclass(frozen=True)
class InventoryItem:
    path: str
    effect_class: EffectClass
    owner: str | None = None
    secret_safe: bool = True
    notes: str = ""


_SECRET_NAME = re.compile(r"(?i)(secret|password|token|api[_-]?key|credential)")


def classify_path(path: str) -> EffectClass:
    p = path.replace("\\", "/").lower()
    name = Path(p).name
    if _SECRET_NAME.search(name) or p.endswith(".pem") or "/secrets/" in p:
        return "secret_metadata"
    if any(x in p for x in ("/migrations/", "/alembic/", "/flyway/")) or (
        name.startswith("v") and name.endswith(".sql")
    ):
        return "migration"
    if name.endswith((".yml", ".yaml", ".toml", ".ini", ".env", ".json", ".conf", ".config")):
        if "docker-compose" in name or name in {"dockerfile", "procfile"}:
            return "deployment"
        return "config"
    if name in {"dockerfile", "makefile"} or p.endswith((".tf", ".tfvars")):
        return "deployment"
    return "code"


def build_inventory(paths: list[str], *, owners: dict[str, str] | None = None) -> list[InventoryItem]:
    owners = owners or {}
    items: list[InventoryItem] = []
    for path in paths:
        effect = classify_path(path)
        secret_safe = effect != "secret_metadata"
        items.append(
            InventoryItem(
                path=path,
                effect_class=effect,
                owner=owners.get(path),
                secret_safe=secret_safe,
                notes="metadata only; values never loaded" if not secret_safe else "",
            )
        )
    return items


def distinguish_effects(items: list[InventoryItem]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {
        "code": [],
        "config": [],
        "migration": [],
        "deployment": [],
        "secret_metadata": [],
    }
    for item in items:
        out[item.effect_class].append(item.path)
    return out


def assert_no_secret_values(payload: dict[str, Any]) -> None:
    """Inventory payloads must not carry secret values."""
    banned = ("password", "secret", "token", "api_key")
    for key, value in payload.items():
        if any(b in key.lower() for b in banned) and value not in (None, "", "[REDACTED]", "[METADATA]"):
            raise InventoryError("SECRET_VALUE_IN_INVENTORY", key)


__all__ = [
    "InventoryError",
    "EffectClass",
    "InventoryItem",
    "classify_path",
    "build_inventory",
    "distinguish_effects",
    "assert_no_secret_values",
]
