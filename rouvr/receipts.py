"""rouvr/receipts.py — Ed25519-signed completion receipts (S010 P2).

A receipt binds the same 11 links the unsigned S006 digest covered; the
digest formula is unchanged (sha256 of sort-keys JSON) and the Ed25519
signature covers the digest bytes. Key rotation: the envelope carries
`key_id`; verifiers keep a {key_id: public key} pool so receipts signed
under a retired id stay verifiable until that id is dropped from the pool.
Private keys live only in 0600 files (receipt_key_path); key material is
never logged, returned, or placed in evidence.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key

from rouvr.errors import Code, RouvrError

RECEIPT_VERSION = "rouvr-receipt-v1"


def build_links(*, task_id: str, contract_rev: str, attempt_id: str, muse_session_id: str,
                pr_url: str, head_sha: str, test_run_id: str, test_evidence_id: str,
                verdict: str, verifier: str, verify_evidence_id: str) -> dict[str, str]:
    """The 11 receipt links, exactly as the unsigned digest covered them."""
    return {"task_id": task_id, "contract_rev": contract_rev, "attempt_id": attempt_id,
            "muse_session_id": muse_session_id, "pr_url": pr_url, "head_sha": head_sha,
            "test_run_id": test_run_id, "test_evidence_id": test_evidence_id,
            "verdict": verdict, "verifier": verifier, "verify_evidence_id": verify_evidence_id}


def receipt_digest(links: Mapping[str, str]) -> str:
    return hashlib.sha256(json.dumps(links, sort_keys=True).encode()).hexdigest()


def load_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError as e:
        raise RouvrError(Code.EVIDENCE, "receipt signing key missing") from e
    if stat.S_IMODE(mode) & 0o077:
        raise RouvrError(Code.EVIDENCE, "receipt signing key must be 0600")
    try:
        key = load_pem_private_key(path.read_bytes(), password=None)
    except Exception as e:
        raise RouvrError(Code.EVIDENCE, "receipt signing key unreadable") from e
    if not isinstance(key, Ed25519PrivateKey):
        raise RouvrError(Code.EVIDENCE, "receipt key must be Ed25519")
    return key


def load_public_key(path: Path) -> Ed25519PublicKey:
    try:
        key = load_pem_public_key(path.read_bytes())
    except Exception as e:
        raise RouvrError(Code.EVIDENCE, "receipt public key unreadable") from e
    if not isinstance(key, Ed25519PublicKey):
        raise RouvrError(Code.EVIDENCE, "receipt public key must be Ed25519")
    return key


@dataclass(frozen=True)
class SignedReceipt:
    version: str
    key_id: str
    digest: str
    signature: str  # base64 Ed25519 signature over bytes.fromhex(digest)
    links: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"version": self.version, "key_id": self.key_id, "digest": self.digest,
                           "signature": self.signature, "links": self.links}, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "SignedReceipt":
        d = json.loads(raw)
        return cls(version=d["version"], key_id=d["key_id"], digest=d["digest"],
                   signature=d["signature"], links=d["links"])


def sign_links(links: Mapping[str, str], key: Ed25519PrivateKey, key_id: str) -> SignedReceipt:
    digest = receipt_digest(links)
    sig = key.sign(bytes.fromhex(digest))
    return SignedReceipt(version=RECEIPT_VERSION, key_id=key_id, digest=digest,
                         signature=base64.b64encode(sig).decode(), links=dict(links))


def verify_receipt(receipt: SignedReceipt,
                   public_keys: Ed25519PublicKey | Mapping[str, Ed25519PublicKey]) -> bool:
    """True only if the digest matches the links and the signature verifies.

    Accepts one published key or a {key_id: key} pool (rotation). Any
    mismatch, unknown key_id, or malformed envelope fails closed (False).
    """
    try:
        digest = receipt_digest(receipt.links)
        if not hmac.compare_digest(digest, receipt.digest):
            return False
        pub = (public_keys.get(receipt.key_id) if isinstance(public_keys, Mapping)
               else public_keys)
        if pub is None:
            return False
        pub.verify(base64.b64decode(receipt.signature, validate=True), bytes.fromhex(digest))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
