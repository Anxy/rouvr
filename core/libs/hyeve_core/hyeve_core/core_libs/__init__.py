"""
Core library interface definitions shared across Hyeve services.

This package exposes common dataclasses and abstract contracts that the concrete
implementations (packs, validation, errors, dal, etc.) must satisfy.  BIVR and
other generators depend on these thin contracts rather than the concrete
implementations, so the wiring is isolated from service-specific details.
"""

from . import interfaces, types

__all__ = [
    "types",
    "interfaces",
]
