"""Maintained AIX event consumer (#119, programme 23).

WorkHub/OpsBus events in, exactly one durable Temporal action per event on
queue ``aix.execution.current``: event-ID dedupe (replay-safe), a durable
cursor plus a WorkHub-vs-Temporal recovery pass (restart-safe), an admission
seam, and an isolated integration runner for the #72 proofs.

Ownership: staging only. Files under this package never handle endpoints,
tokens, or lifecycle authority — that stays with WorkHub and the broker.
"""
from __future__ import annotations

__all__ = ["CONSUMER_VERSION"]

CONSUMER_VERSION = "aix.event-consumer.v1"
