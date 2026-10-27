from __future__ import annotations

from typing import Any

from hyeve_task_contract_engine.facade import build_task_contract_artifacts

_AIX_PACKET_FACTORY_CLASS: Any | None = None


def _import_aix_packet_factory_class() -> type[Any]:
    from aix.core.packet_factory import AIXPacketFactory as packet_factory_class

    return packet_factory_class


def _resolve_aix_packet_factory_class() -> type[Any]:
    global _AIX_PACKET_FACTORY_CLASS
    if _AIX_PACKET_FACTORY_CLASS is not None:
        return _AIX_PACKET_FACTORY_CLASS
    try:
        packet_factory_class = _import_aix_packet_factory_class()
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "AIX_PACKET_FACTORY_UNAVAILABLE: aix.core.packet_factory is not importable in this runtime"
        ) from exc
    _AIX_PACKET_FACTORY_CLASS = packet_factory_class
    return packet_factory_class


class _AIXPacketFactoryProxy:
    def create_strict_planner_packet(self, *, task_number: int) -> dict[str, Any]:
        packet_factory_class = _resolve_aix_packet_factory_class()
        return packet_factory_class.create_strict_planner_packet(task_number=task_number)


AIXPacketFactory = _AIXPacketFactoryProxy()


def attach_task_ticket_contract(
    *,
    title: str,
    description: str | None,
    metadata: dict[str, Any] | None,
    require_execution_slice: bool = False,
) -> dict[str, Any]:
    artifacts = build_task_contract_artifacts(
        title=title,
        description=description,
        metadata=metadata,
        require_execution_slice=require_execution_slice,
    )
    return {
        "metadata": dict((artifacts.get("normalized_contract") or {}).get("metadata") or {}),
        "validation": dict((artifacts.get("diagnostics") or {}).get("validation") or {}),
    }


def _emit_ascii_safe_debug_line(message: str) -> None:
    text = str(message or "")
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        safe_text = text.encode("ascii", "backslashreplace").decode("ascii")
        print(safe_text, flush=True)


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except Exception:
        return None
