"""Attach codeintel citations to recon evidence (Wave 4 CI6, AIX lane).

:func:`attach_citations_to_evidence` is a pure function with a stable
signature: the recon layer calls it with its payload dict and a
:class:`ContextPack`, and gets back a NEW dict with exactly three keys
added. It performs no I/O, mutates neither its inputs nor any pre-existing
payload key, and never picks, ranks, or resolves anything.
"""

from __future__ import annotations

from typing import Any, Mapping

from backend.codeintel_v2.citations import citation_to_dict

#: Keys added to the recon payload copy. Exactly these, no more.
ADDED_KEYS = ("codeintel_citations", "pack_freshness", "advisory_degraded")

_STALE_OR_UNKNOWN = ("stale", "unknown")


def _pack_label(pack: Any) -> str:
    """Read the pack freshness label from any pack-like object."""
    freshness = getattr(pack, "freshness", None)
    if freshness is None and isinstance(pack, Mapping):
        freshness = pack.get("freshness")
    if isinstance(freshness, str):
        label = freshness.strip().lower()
    elif isinstance(freshness, Mapping):
        label = str(freshness.get("freshness") or "").strip().lower()
    else:
        label = str(getattr(freshness, "freshness", "") or "").strip().lower()
    return label if label in ("current", "stale", "unknown") else "unknown"


def _pack_citations(pack: Any) -> list[Any]:
    items = getattr(pack, "citations", None)
    if items is None and isinstance(pack, Mapping):
        items = pack.get("citations")
    if items is None:
        raise ValueError("attach expects a pack carrying 'citations'")
    return list(items)


def attach_citations_to_evidence(
    recon_payload: dict[str, Any], pack: Any
) -> dict[str, Any]:
    """Return a NEW dict with codeintel citations attached.

    Added keys: ``codeintel_citations`` (list of citation dicts),
    ``pack_freshness`` (``"current"`` / ``"stale"`` / ``"unknown"`` label),
    ``advisory_degraded`` (``True`` exactly when the pack is ``stale`` or
    ``unknown``). Every other key of ``recon_payload`` is carried over
    untouched.
    """
    if not isinstance(recon_payload, dict):
        raise TypeError(
            "attach_citations_to_evidence expects a dict recon_payload, "
            f"got {type(recon_payload).__name__}"
        )
    if pack is None:
        raise ValueError("attach_citations_to_evidence expects a pack, got None")
    label = _pack_label(pack)
    out: dict[str, Any] = dict(recon_payload)
    out["codeintel_citations"] = [
        citation_to_dict(item) for item in _pack_citations(pack)
    ]
    out["pack_freshness"] = label
    out["advisory_degraded"] = label in _STALE_OR_UNKNOWN
    return out


__all__ = ["ADDED_KEYS", "attach_citations_to_evidence"]
