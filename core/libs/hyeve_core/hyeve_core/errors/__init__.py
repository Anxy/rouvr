"""
Error catalog implementations for Hyeve services.

The main export is :class:`LayeredErrorCatalog`, which resolves error codes to
tenant/client-specific messages using pack bundles.
"""

from .catalog import ErrorCatalogConfig, LayeredErrorCatalog

__all__ = [
    "LayeredErrorCatalog",
    "ErrorCatalogConfig",
]
