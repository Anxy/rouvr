"""Machine-readable reachability schema for C001."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

RUNTIME_STATUSES = frozenset(
    {
        "LIVE",
        "PARTIALLY_LIVE",
        "FEATURE_FLAGGED",
        "IMPLEMENTED_NOT_WIRED",
        "TEST_ONLY",
        "DUPLICATE",
        "DEAD",
        "SCAFFOLD",
        "BROKEN",
        "MISSING",
    }
)

DISPOSITIONS = frozenset(
    {
        "KEEP",
        "EXPAND",
        "MERGE",
        "ADAPTER",
        "REPLACE",
        "ARCHIVE",
        "REMOVE",
        "INVESTIGATE",
    }
)

ComponentKind = Literal[
    "router",
    "package",
    "workflow",
    "worker",
    "client",
    "script",
    "service_module",
]

Disposition = Literal[
    "KEEP",
    "EXPAND",
    "MERGE",
    "ADAPTER",
    "REPLACE",
    "ARCHIVE",
    "REMOVE",
    "INVESTIGATE",
]

RuntimeStatus = Literal[
    "LIVE",
    "PARTIALLY_LIVE",
    "FEATURE_FLAGGED",
    "IMPLEMENTED_NOT_WIRED",
    "TEST_ONLY",
    "DUPLICATE",
    "DEAD",
    "SCAFFOLD",
    "BROKEN",
    "MISSING",
]


@dataclass(frozen=True)
class ReachabilityComponent:
    """One inventory unit: route family, package, worker, client, or script."""

    component: str
    kind: ComponentKind
    entrypoint: str
    production_caller: str
    state_owned: tuple[str, ...] = ()
    effects_owned: tuple[str, ...] = ()
    completion_authority: str = "none"
    feature_flags: tuple[str, ...] = ()
    runtime_status: RuntimeStatus = "DEAD"
    disposition: Disposition = "INVESTIGATE"
    notes: str = ""
    owners: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ReachabilityComponent:
        status = str(payload.get("runtime_status") or "DEAD")
        disposition = str(payload.get("disposition") or "INVESTIGATE")
        if status not in RUNTIME_STATUSES:
            raise ValueError(f"invalid runtime_status:{status}")
        if disposition not in DISPOSITIONS:
            raise ValueError(f"invalid disposition:{disposition}")
        return cls(
            component=str(payload["component"]),
            kind=payload["kind"],  # type: ignore[arg-type]
            entrypoint=str(payload.get("entrypoint") or ""),
            production_caller=str(payload.get("production_caller") or "none"),
            state_owned=tuple(payload.get("state_owned") or ()),
            effects_owned=tuple(payload.get("effects_owned") or ()),
            completion_authority=str(payload.get("completion_authority") or "none"),
            feature_flags=tuple(payload.get("feature_flags") or ()),
            runtime_status=status,  # type: ignore[arg-type]
            disposition=disposition,  # type: ignore[arg-type]
            notes=str(payload.get("notes") or ""),
            owners=tuple(payload.get("owners") or ()),
        )


@dataclass
class ReachabilityManifest:
    schema_version: str
    repository_revision: str
    generated_from: str
    components: list[ReachabilityComponent] = field(default_factory=list)
    unclassified_production_routes: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "repository_revision": self.repository_revision,
            "generated_from": self.generated_from,
            "component_count": len(self.components),
            "components": [c.to_dict() for c in self.components],
            "unclassified_production_routes": list(self.unclassified_production_routes),
            "validation_errors": list(self.validation_errors),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ReachabilityManifest:
        return cls(
            schema_version=str(payload.get("schema_version") or ""),
            repository_revision=str(payload.get("repository_revision") or ""),
            generated_from=str(payload.get("generated_from") or ""),
            components=[ReachabilityComponent.from_dict(item) for item in payload.get("components") or []],
            unclassified_production_routes=list(payload.get("unclassified_production_routes") or []),
            validation_errors=list(payload.get("validation_errors") or []),
        )
