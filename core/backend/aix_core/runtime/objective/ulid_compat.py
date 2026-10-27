"""ULID generation compatible with ulid-py 1.x (`ulid.new()` when `ULID()` is unavailable)."""

from __future__ import annotations


def new_ulid() -> str:
    try:
        from ulid import ULID

        return str(ULID())
    except TypeError:
        import ulid as ulid_api

        return str(ulid_api.new())
