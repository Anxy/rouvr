"""C017: environment discovery + immutable fingerprint.

Forbidden: arbitrary install from objective text.
DoD: tests run in an identified, reproducible environment.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class EnvironmentError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


FORBIDDEN_OBJECTIVE_INSTALL_PATTERNS = (
    "pip install",
    "npm install -g",
    "curl | sh",
    "wget | bash",
    "apt-get install",
    "brew install",
)


@dataclass(frozen=True)
class EnvironmentFingerprint:
    fingerprint_id: str
    languages: tuple[str, ...]
    manifests: tuple[str, ...]
    runtime: dict[str, str]
    tools: dict[str, str]
    schema_version: str = "aix.env_fingerprint.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fingerprint_id": self.fingerprint_id,
            "languages": list(self.languages),
            "manifests": list(self.manifests),
            "runtime": dict(self.runtime),
            "tools": dict(self.tools),
        }


def reject_objective_install_text(objective_text: str) -> None:
    lowered = (objective_text or "").lower()
    for pattern in FORBIDDEN_OBJECTIVE_INSTALL_PATTERNS:
        if pattern in lowered:
            raise EnvironmentError("ARBITRARY_INSTALL_FROM_OBJECTIVE_FORBIDDEN", pattern)


def discover_environment(root: str | Path | None = None) -> EnvironmentFingerprint:
    base = Path(root).resolve() if root else Path.cwd().resolve()
    manifests: list[str] = []
    languages: set[str] = set()
    tools: dict[str, str] = {}

    checks = {
        "pyproject.toml": "python",
        "requirements.txt": "python",
        "package.json": "javascript",
        "go.mod": "go",
        "Cargo.toml": "rust",
        "pom.xml": "java",
        "Gemfile": "ruby",
    }
    for name, lang in checks.items():
        if (base / name).is_file():
            manifests.append(name)
            languages.add(lang)

    runtime = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": sys.platform,
    }
    # Presence-only tool probes (no install).
    for tool in ("git", "node", "npm", "pytest"):
        tools[tool] = "present" if shutil_which(tool) else "absent"

    payload = {
        "languages": sorted(languages),
        "manifests": sorted(manifests),
        "runtime": runtime,
        "tools": tools,
        "root": str(base),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return EnvironmentFingerprint(
        fingerprint_id=f"env_{digest[:16]}",
        languages=tuple(sorted(languages)),
        manifests=tuple(sorted(manifests)),
        runtime=runtime,
        tools=tools,
    )


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


__all__ = [
    "EnvironmentError",
    "EnvironmentFingerprint",
    "FORBIDDEN_OBJECTIVE_INSTALL_PATTERNS",
    "reject_objective_install_text",
    "discover_environment",
]
