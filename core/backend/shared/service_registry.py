"""Helpers for interacting with the Hyeve service registry."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

DEFAULT_MANIFEST_PATH = r"""services/manifests/hyeve-aix-service.yml"""
DEFAULT_REGISTRY_ROOT = r"""services"""


class ServiceTokenCache:
    """Simple TTL cache for service-to-service auth tokens."""

    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        self._store: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        record = self._store.get(key)
        if not record:
            return None
        expires_at, value = record
        if expires_at < time.time():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: str) -> None:
        self._store[key] = (time.time() + self.ttl, value)


class HyeveServiceRegistry:
    """Loads the local service manifest and resolves dependencies."""

    def __init__(self, manifest_path: str | None = None, cache_ttl_seconds: int = 300):
        resolved_manifest = manifest_path or os.getenv("HYEVE_SERVICE_MANIFEST") or DEFAULT_MANIFEST_PATH
        self.manifest_path = Path(resolved_manifest).resolve()
        registry_root = os.getenv("HYEVE_SERVICE_REGISTRY_ROOT") or DEFAULT_REGISTRY_ROOT
        self.registry_root = Path(registry_root).resolve()
        self._manifest_cache: dict[str, Any] | None = None
        self._manifest_mtime: float = 0.0
        self.token_cache = ServiceTokenCache(cache_ttl_seconds)

    def _load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Service manifest not found: {self.manifest_path}")
        current_mtime = self.manifest_path.stat().st_mtime
        if self._manifest_cache is None or current_mtime > self._manifest_mtime:
            self._manifest_cache = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8")) or {}
            self._manifest_mtime = current_mtime
        return self._manifest_cache

    def service_capabilities(self) -> list[str]:
        manifest = self._load_manifest()
        tiers = manifest.get("subscription_tiers", {})
        capabilities: set[str] = set()
        for caps in tiers.values():
            capabilities.update(caps or [])
        return sorted(capabilities)

    def ensure_capability(self, capability: str) -> None:
        if capability not in self.service_capabilities():
            raise PermissionError(f"Service is not allowed to perform capability '{capability}'")

    def dependency_manifest(self, service_name: str) -> dict[str, Any]:
        dep_manifest = self.registry_root / "manifests" / f"{service_name}.yml"
        if not dep_manifest.exists():
            raise FileNotFoundError(f"Dependency manifest not found: {dep_manifest}")
        return yaml.safe_load(dep_manifest.read_text(encoding="utf-8")) or {}

    def cache_service_token(self, service_name: str, token: str) -> None:
        self.token_cache.set(service_name, token)

    def get_cached_service_token(self, service_name: str) -> str | None:
        return self.token_cache.get(service_name)

    def allowed_dependencies(self) -> dict[str, list[str]]:
        manifest = self._load_manifest()
        deps = manifest.get("dependencies", {})
        deps.setdefault("required", [])
        deps.setdefault("optional", [])
        return deps


registry = HyeveServiceRegistry()
