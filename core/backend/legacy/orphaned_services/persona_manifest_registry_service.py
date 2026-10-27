from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.models.persona_manifest_models import PersonaManifestCurrent, validate_persona_manifest


class PersonaManifestNotFoundError(LookupError):
    pass


def load_persona_manifests(*, manifests_dir: str | Path) -> dict[str, PersonaManifestCurrent]:
    root = Path(manifests_dir)
    if not root.exists():
        raise FileNotFoundError(str(root))

    items: dict[str, PersonaManifestCurrent] = {}
    for file_path in sorted(root.glob("*.json"), key=lambda p: p.name.lower()):
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        manifest = validate_persona_manifest(payload)
        items[manifest.persona_id] = manifest
    return items


def resolve_persona_manifest(
    *,
    persona_id: str,
    manifests: dict[str, PersonaManifestCurrent] | None = None,
    manifests_dir: str | Path | None = None,
) -> tuple[PersonaManifestCurrent, dict[str, Any]]:
    available = dict(manifests or {})
    if not available:
        if manifests_dir is None:
            raise ValueError("manifests or manifests_dir is required")
        available = load_persona_manifests(manifests_dir=manifests_dir)

    manifest = available.get(persona_id)
    if manifest is None:
        raise PersonaManifestNotFoundError(f"PERSONA_MANIFEST_NOT_FOUND persona_id={persona_id}")

    return manifest, {
        "source": "explicit_persona_id",
        "persona_id": persona_id,
        "loaded_persona_ids": sorted(available.keys()),
    }
