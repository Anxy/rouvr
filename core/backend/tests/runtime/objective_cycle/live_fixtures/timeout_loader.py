"""Real timeout source used by the live-fixture remote-call helper."""

from __future__ import annotations

import os


def get_request_timeout_seconds() -> int:
    """Return the active request timeout.

    Runtime truth is the AIX_LIVE_REQUEST_TIMEOUT_SECONDS environment variable.
    When unset, the fallback is a computed default (not the decoy config module).
    """
    raw = os.environ.get("AIX_LIVE_REQUEST_TIMEOUT_SECONDS")
    if raw is None or str(raw).strip() == "":
        # Computed so a naive 10→45 literal replace on decoy config does nothing here.
        return 45
    return int(raw)
