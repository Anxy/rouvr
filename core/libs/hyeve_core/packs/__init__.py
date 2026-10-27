"""
Pack resolver implementations for the Hyeve ecosystem.

The primary export is :class:`LayeredPackResolver`, which materialises pack
bundles by merging layered JSON artefacts (global → product → industry → client
→ tenant → environment → locale).  The resolver is configurable so that teams
can point it to filesystem directories, object stores, or custom provider
functions.
"""

from .resolver import (
    LayeredPackResolver,
    LayeredPackResolverConfig,
    LayerSourceConfig,
)

__all__ = [
    "LayeredPackResolver",
    "LayeredPackResolverConfig",
    "LayerSourceConfig",
]
