"""AIX-E2E-CYCLE-001H-W03: evidence bundle.

A signed/hashed immutable manifest tied to one candidate SHA and one attempt. "Signed"
here means a pluggable signer callable is applied to the bundle hash — no real signing
key/HSM/KMS is integrated in this codebase yet, so the default signer is an explicit,
clearly-labelled unsigned marker rather than a fake signature that could be mistaken for
a real one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field

UNSIGNED_MARKER = "UNSIGNED:no-signer-configured"

Signer = Callable[[str], str]


class EvidenceBundleError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceItemV1:
    ref: str
    content_hash: str

    def __post_init__(self) -> None:
        if not self.ref or not self.ref.strip():
            raise EvidenceBundleError("evidence item ref must not be empty")
        if not self.content_hash or not self.content_hash.strip():
            raise EvidenceBundleError("evidence item content_hash must not be empty")


@dataclass(frozen=True)
class EvidenceBundleV1:
    attempt_id: str
    candidate_sha: str
    items: tuple[EvidenceItemV1, ...]
    bundle_hash: str
    signature: str = UNSIGNED_MARKER

    @property
    def is_signed(self) -> bool:
        return self.signature != UNSIGNED_MARKER


def _compute_bundle_hash(attempt_id: str, candidate_sha: str, items: tuple[EvidenceItemV1, ...]) -> str:
    canonical = json.dumps(
        {
            "attempt_id": attempt_id,
            "candidate_sha": candidate_sha,
            "items": sorted(
                [{"ref": item.ref, "content_hash": item.content_hash} for item in items],
                key=lambda entry: entry["ref"],
            ),
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_bundle(
    *,
    attempt_id: str,
    candidate_sha: str,
    items: list[EvidenceItemV1],
    signer: Signer | None = None,
) -> EvidenceBundleV1:
    """Build an immutable evidence bundle. `items` must be non-empty — an attempt with
    no evidence cannot produce a bundle (mirrors `verification.py`'s "no independent
    evidence" -> `BLOCKED` rule at the evidence layer). The bundle hash is computed over
    a canonical (sorted, deterministic) JSON representation so re-ordering `items` never
    changes the hash. If `signer` is provided it is applied to the hash to produce
    `signature`; otherwise `signature` is the explicit `UNSIGNED_MARKER`, never a value
    that looks like a real signature."""
    if not attempt_id or not attempt_id.strip():
        raise EvidenceBundleError("attempt_id is required")
    if not candidate_sha or not candidate_sha.strip():
        raise EvidenceBundleError("candidate_sha is required")
    if not items:
        raise EvidenceBundleError("evidence bundle requires at least one item")

    items_tuple = tuple(items)
    bundle_hash = _compute_bundle_hash(attempt_id, candidate_sha, items_tuple)
    signature = signer(bundle_hash) if signer is not None else UNSIGNED_MARKER
    return EvidenceBundleV1(
        attempt_id=attempt_id,
        candidate_sha=candidate_sha,
        items=items_tuple,
        bundle_hash=bundle_hash,
        signature=signature,
    )


def verify_bundle_integrity(bundle: EvidenceBundleV1) -> bool:
    """Recompute the hash from `bundle`'s own fields and compare — detects any bundle
    whose `items`/`attempt_id`/`candidate_sha` were mutated after `bundle_hash` was set
    (the dataclass is frozen, so this only catches a caller reconstructing one by hand)."""
    return _compute_bundle_hash(bundle.attempt_id, bundle.candidate_sha, bundle.items) == bundle.bundle_hash


__all__ = [
    "UNSIGNED_MARKER",
    "Signer",
    "EvidenceBundleError",
    "EvidenceItemV1",
    "EvidenceBundleV1",
    "build_bundle",
    "verify_bundle_integrity",
]
