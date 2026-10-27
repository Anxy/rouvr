"""
Hyeve Core Library

Core components and utilities for the Hyeve multi-tenant SaaS ERP platform.
"""

__version__ = "0.1.0"

# Import main modules for easy access
from . import (
    core_libs,
    dal,
    error_handler,
    errors,
    managers,
    metadata,
    packs,
    security,
    service_registry,
    telemetry,
    validation,
)

__all__ = [
    "core_libs",
    "dal",
    "error_handler",
    "errors",
    "managers",
    "metadata",
    "packs",
    "security",
    "service_registry",
    "telemetry",
    "validation",
]
