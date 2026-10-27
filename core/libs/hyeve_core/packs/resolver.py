"""
Filesystem-backed implementation of :class:`PackResolver`.

The resolver expects a directory structure per layer::

    <base>/<layer>/<service>/<entity>/<version>/<override>/pack.json

Where ``<override>`` is optional (used for tenant/client/environment/locale
layers).  Each ``pack.json`` must contain a dictionary with the keys described
in :class:`hyeve_core.core_libs.types.PackBundle`.  Later layers override and
deep-merge the content of earlier layers.

The implementation is purposely conservative: it only depends on the Python
standard library, keeps caching in-process, and exposes configuration hooks for
future persistence backends (e.g., Redis, S3).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hyeve_core.core_libs.interfaces import PackResolutionError, PackResolver
from hyeve_core.core_libs.types import PackBundle, PackContext, PackLayer, PackSource

DEFAULT_FILENAME = "pack.json"
DEFAULT_REGISTRY_TIMEOUT = 5
DEFAULT_REGISTRY_LIMIT = 500

logger = logging.getLogger(__name__)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge two dictionaries without modifying inputs."""

    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


LayerValueGetter = Callable[[PackContext], str | None]


def _default_value_getter(layer: PackLayer) -> LayerValueGetter:
    return {
        PackLayer.GLOBAL: lambda ctx: None,
        PackLayer.PRODUCT: lambda ctx: None,
        PackLayer.INDUSTRY: lambda ctx: ctx.industry,
        PackLayer.CLIENT: lambda ctx: ctx.client_id,
        PackLayer.TENANT: lambda ctx: ctx.tenant_id,
        PackLayer.ENVIRONMENT: lambda ctx: ctx.environment,
        PackLayer.LOCALE: lambda ctx: ctx.locale,
    }[layer]


@dataclass(frozen=True)
class LayerSourceConfig:
    """Configuration describing how to locate a single layer."""

    layer: PackLayer
    base_path: Path
    filename: str = DEFAULT_FILENAME
    include_service: bool = True
    include_entity: bool = True
    include_version: bool = True
    value_getter: LayerValueGetter | None = None
    required: bool = False

    def with_defaults(self) -> LayerSourceConfig:
        getter = self.value_getter if self.value_getter is not None else _default_value_getter(self.layer)
        return LayerSourceConfig(
            layer=self.layer,
            base_path=self.base_path,
            filename=self.filename or DEFAULT_FILENAME,
            include_service=self.include_service,
            include_entity=self.include_entity,
            include_version=self.include_version,
            value_getter=getter,
            required=self.required,
        )


@dataclass
class LayeredPackResolverConfig:
    """Configuration for :class:`LayeredPackResolver`."""

    sources: Sequence[LayerSourceConfig]
    cache_size: int = 256

    def normalised_sources(self) -> Sequence[LayerSourceConfig]:
        seen = set()
        ordered = []
        for source in self.sources:
            source = source.with_defaults()
            if source.layer in seen:
                raise ValueError(f"Duplicate layer specified: {source.layer}")
            seen.add(source.layer)
            ordered.append(source)
        ordered.sort(key=lambda cfg: list(PackLayer).index(cfg.layer))
        return ordered


class LayeredPackResolver(PackResolver):
    """Filesystem-backed layered resolver."""

    def __init__(self, config: LayeredPackResolverConfig):
        self._config = config
        self._sources = config.normalised_sources()
        self._lock = threading.RLock()
        self._cache: dict[PackContext, PackBundle] = {}
        self._cache_order: list[PackContext] = []

    def get(self, context: PackContext) -> PackBundle:
        with self._lock:
            cached = self._cache.get(context)
            if cached:
                return cached

        bundle = self._resolve_bundle(context)

        with self._lock:
            self._cache[context] = bundle
            self._cache_order.append(context)
            if len(self._cache_order) > self._config.cache_size:
                evict = self._cache_order.pop(0)
                self._cache.pop(evict, None)

        return bundle

    def warm_cache(self, contexts: Iterable[PackContext]) -> None:
        for ctx in contexts:
            self.get(ctx)

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #
    def _resolve_bundle(self, context: PackContext) -> PackBundle:
        registry_bundle = _fetch_registry_bundle(context)
        if registry_bundle is not None:
            return registry_bundle

        merged: dict[str, Any] = {}
        sources: list[PackSource] = []

        for cfg in self._sources:
            payload, source_meta = self._load_layer(cfg, context)
            if payload is None:
                if cfg.required:
                    raise PackResolutionError(f"Required layer {cfg.layer} not found for {context}")
                continue
            merged = _deep_merge(merged, payload)
            if source_meta:
                sources.append(source_meta)

        if not merged:
            allow_empty = os.getenv("HYEVE_PACKS_ALLOW_EMPTY", "").lower() in {"1", "true", "yes"}
            if allow_empty:
                return PackBundle(
                    schema={},
                    validation_rules={},
                    messages={},
                    workflows={},
                    http_hints={},
                    metadata={
                        "source": "empty",
                        "context": {
                            "service": context.service,
                            "entity": context.entity,
                            "version": context.version,
                        },
                    },
                    version=context.version,
                    sources=tuple(sources),
                )
            raise PackResolutionError(
                f"No pack data found for context {context.service}/{context.entity}/{context.version}"
            )

        schema = merged.get("schema") or {}
        validation_rules = merged.get("validation_rules") or {}
        messages = merged.get("messages") or {}
        workflows = merged.get("workflows") or {}
        http_hints = merged.get("http_hints") or {}
        metadata = merged.get("metadata") or {}

        return PackBundle(
            schema=schema,
            validation_rules=validation_rules,
            messages=messages,
            workflows=workflows,
            http_hints=http_hints,
            metadata=metadata,
            version=merged.get("version"),
            sources=tuple(sources),
        )

    def _load_layer(
        self,
        cfg: LayerSourceConfig,
        context: PackContext,
    ) -> tuple[dict[str, Any] | None, PackSource | None]:
        value_getter = cfg.value_getter or _default_value_getter(cfg.layer)
        value = value_getter(context)
        if value is None and cfg.layer not in {PackLayer.GLOBAL, PackLayer.PRODUCT}:
            return None, None

        path = Path(cfg.base_path)
        if cfg.include_service:
            path /= context.service
        if cfg.include_entity:
            path /= context.entity
        if cfg.include_version:
            path /= context.version
        if value:
            path /= value

        file_path = path / cfg.filename
        if not file_path.exists():
            return None, None

        try:
            with open(file_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise PackResolutionError(f"Invalid JSON in {file_path}: {exc}") from exc
        except OSError as exc:
            raise PackResolutionError(f"Failed to read pack data from {file_path}: {exc}") from exc

        source = PackSource(
            layer=cfg.layer,
            source=str(file_path),
            version=data.get("version"),
            hash=data.get("metadata", {}).get("hash"),
        )
        return data, source


def _registry_enabled() -> bool:
    value = os.getenv("HYEVE_PACK_REGISTRY_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no"}


def _registry_base_url() -> str | None:
    return os.getenv("HYEVE_PACK_REGISTRY_URL")


def _registry_headers(context: PackContext) -> dict[str, str]:
    headers: dict[str, str] = {}
    if context.tenant_id:
        headers["X-Tenant-ID"] = context.tenant_id
    token = os.getenv("HYEVE_PACK_REGISTRY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    actor_id = os.getenv("HYEVE_PACK_REGISTRY_ACTOR_ID", "system")
    headers["X-Actor-ID"] = actor_id
    return headers


def _pack_key(context: PackContext) -> str:
    return f"{context.service}:{context.entity}"


def _pick_pack_payload(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not assets:
        return None
    for asset in assets:
        if asset.get("asset_path") == DEFAULT_FILENAME:
            payload = asset.get("content_json")
            if isinstance(payload, dict):
                return payload
    for asset in assets:
        payload = asset.get("content_json")
        if isinstance(payload, dict) and payload.get("schema") is not None:
            return payload
    return None


def _fetch_registry_bundle(context: PackContext) -> PackBundle | None:
    if not _registry_enabled():
        return None
    base_url = _registry_base_url()
    if not base_url:
        return None

    try:
        import httpx
    except Exception:
        return None

    timeout = float(os.getenv("HYEVE_PACK_REGISTRY_TIMEOUT", str(DEFAULT_REGISTRY_TIMEOUT)))
    headers = _registry_headers(context)
    pack_key = _pack_key(context)

    try:
        with httpx.Client(timeout=timeout) as client:
            catalog_resp = client.get(
                f"{base_url.rstrip('/')}/api/pack-catalog",
                headers=headers,
                params={"pack_key": pack_key, "limit": 1, "offset": 0},
            )
            if catalog_resp.status_code != 200:
                return None
            catalog_payload = catalog_resp.json()
            catalog_items = catalog_payload.get("data") or []
            if not catalog_items:
                return None
            pack_id = catalog_items[0].get("id")
            if not pack_id:
                return None

            active_version = None
            active_resp = client.get(
                f"{base_url.rstrip('/')}/api/packs/{pack_id}/active",
                headers=headers,
            )
            if active_resp.status_code == 200:
                active_payload = active_resp.json()
                binding = active_payload.get("data") or {}
                active_version = binding.get("active_version")

            requested_version = active_version or context.version

            versions_resp = client.get(
                f"{base_url.rstrip('/')}/api/pack-versions",
                headers=headers,
                params={"pack_id": pack_id, "version": requested_version, "limit": 1, "offset": 0},
            )
            if versions_resp.status_code != 200:
                return None
            versions_payload = versions_resp.json()
            versions = versions_payload.get("data") or []
            if not versions:
                return None
            version_record = versions[0]
            pack_version_id = version_record.get("id")
            if not pack_version_id:
                return None

            assets_resp = client.get(
                f"{base_url.rstrip('/')}/api/pack-assets",
                headers=headers,
                params={"pack_version_id": pack_version_id, "limit": DEFAULT_REGISTRY_LIMIT, "offset": 0},
            )
            if assets_resp.status_code != 200:
                return None
            assets_payload = assets_resp.json()
            assets = assets_payload.get("data") or []
            pack_payload = _pick_pack_payload(assets)
            if not isinstance(pack_payload, dict):
                return None

            source = PackSource(
                layer=PackLayer.TENANT,
                source=f"registry:{pack_key}@{requested_version}",
                version=pack_payload.get("version") or requested_version,
                hash=version_record.get("checksum"),
            )

            return PackBundle(
                schema=pack_payload.get("schema", {}),
                validation_rules=pack_payload.get("validation_rules", {}),
                messages=pack_payload.get("messages", {}),
                workflows=pack_payload.get("workflows", {}),
                http_hints=pack_payload.get("http_hints", {}),
                metadata=pack_payload.get("metadata", {}),
                version=pack_payload.get("version") or requested_version,
                sources=(source,),
            )
    except Exception as exc:
        logger.debug("Pack registry lookup failed: %s", exc)
        return None
