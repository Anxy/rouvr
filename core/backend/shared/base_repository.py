"""Compatibility shim for generated repository modules.

These generated repositories are not on the active runtime path, but mypy still
imports them when checking the backend package. Keep a minimal generic base so
the generated modules resolve cleanly without pulling in legacy DAL code.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Generic, TypeVar

ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    """Minimal repository base used only by generated compatibility modules."""

    def __init__(self, model_class: type[ModelT]) -> None:
        self._model_class = model_class

    def _apply_filters(
        self,
        query: Any,
        filters: Mapping[str, Any] | Any,
        context: Mapping[str, Any] | None = None,
    ) -> Any:
        return query
