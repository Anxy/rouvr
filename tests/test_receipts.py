"""S010 P2: Ed25519 receipt signing — AC-1/2/3 + independent verifier (10.2).

Pure logic + tmp keys, no network. The independent-verifier test deliberately
avoids rouvr.receipts.verify_* and checks the signature with cryptography +
hashlib + json only, exactly as an external auditor would.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from rouvr.errors import Code, RouvrError
from rouvr.receipts import (
    SignedReceipt,
    build_links,
    load_private_key,
    load_public_key,
    receipt_digest,
    sign_links,
    verify_receipt,
)

LINKS = dict(
    task_id="task1",
    contract_rev="3",
    attempt_id="att1",
    muse_session_id="sess1",
    pr_url="https://pr/1",
    head_sha="sha1",
    test_run_id="tr1",
    test_evidence_id="ev-t",
    verdict="approve",
    verifier="svc-v",
    verify_evidence_id="ev-v",
)


def _write_private(path: Path, key: Ed25519PrivateKey, mode: int = 0o600) -> Path:
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path.write_bytes(pem)
    os.chmod(path, mode)
    return path


def _write_public(path: Path, key: Ed25519PrivateKey) -> Path:
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    path.write_bytes(pem)
    return path


# --- AC-2: digest coverage unchanged (11 links, legacy encoding) ---

def test_digest_covers_exactly_11_links_legacy_value():
    links = build_links(**LINKS)
    assert set(links) == {
        "task_id", "contract_rev", "attempt_id", "muse_session_id", "pr_url",
        "head_sha", "test_run_id", "test_evidence_id", "verdict", "verifier",
        "verify_evidence_id",
    }
    assert len(links) == 11
    legacy = hashlib.sha256(json.dumps(links, sort_keys=True).encode()).hexdigest()
    assert receipt_digest(links) == legacy
    assert len(legacy) == 64


@pytest.mark.parametrize("field", sorted(LINKS))
def test_digest_changes_when_any_link_changes(field):
    links = build_links(**LINKS)
    d0 = receipt_digest(links)
    mutated = dict(links, **{field: links[field] + "-x"})
    assert receipt_digest(mutated) != d0


# --- AC-1: verifies against published public key; wrong key fails ---

def test_sign_verify_roundtrip_and_wrong_key_fails(tmp_path: Path):
    key = Ed25519PrivateKey.generate()
    other = Ed25519PrivateKey.generate()
    links = build_links(**LINKS)
    signed = sign_links(links, key, "rouvr-receipt-1")
    assert signed.key_id == "rouvr-receipt-1"
    assert signed.digest == receipt_digest(links)
    assert verify_receipt(signed, key.public_key()) is True
    assert verify_receipt(signed, other.public_key()) is False
    # rotation-aware mapping: correct id passes, unknown id fails closed
    assert verify_receipt(signed, {"rouvr-receipt-1": key.public_key()}) is True
    assert verify_receipt(signed, {"rouvr-receipt-2": key.public_key()}) is False
    assert verify_receipt(signed, {"rouvr-receipt-1": other.public_key()}) is False


def test_tampered_link_or_signature_fails():
    key = Ed25519PrivateKey.generate()
    signed = sign_links(build_links(**LINKS), key, "k1")
    tampered_links = SignedReceipt(
        version=signed.version, key_id=signed.key_id, digest=signed.digest,
        signature=signed.signature, links=dict(signed.links, verdict="reject"),
    )
    assert verify_receipt(tampered_links, key.public_key()) is False
    raw = bytearray(base64.b64decode(signed.signature))
    raw[0] ^= 1
    bad_sig = SignedReceipt(
        version=signed.version, key_id=signed.key_id, digest=signed.digest,
        signature=base64.b64encode(bytes(raw)).decode(), links=dict(signed.links),
    )
    assert verify_receipt(bad_sig, key.public_key()) is False


def test_key_rotation_old_receipts_still_verify(tmp_path: Path):
    k1, k2 = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    links = build_links(**LINKS)
    r1 = sign_links(links, k1, "rouvr-receipt-1")
    r2 = sign_links(links, k2, "rouvr-receipt-2")
    pool = {"rouvr-receipt-1": k1.public_key(), "rouvr-receipt-2": k2.public_key()}
    assert verify_receipt(r1, pool) is True
    assert verify_receipt(r2, pool) is True
    # after retiring k1, its receipts no longer verify (explicit, not silent)
    assert verify_receipt(r1, {"rouvr-receipt-2": k2.public_key()}) is False


# --- 10.2 independent verifier (no rouvr verify helper) ---

def test_independent_verifier_with_cryptography_only(tmp_path: Path):
    key = Ed25519PrivateKey.generate()
    priv = _write_private(tmp_path / "receipt_ed25519.pem", key)
    loaded = load_private_key(priv)
    signed = sign_links(build_links(**LINKS), loaded, "rouvr-receipt-1")
    # External auditor path: recompute digest, decode sig, verify with pubkey.
    digest = hashlib.sha256(json.dumps(signed.links, sort_keys=True).encode()).hexdigest()
    assert digest == signed.digest
    key.public_key().verify(base64.b64decode(signed.signature), bytes.fromhex(digest))
    wrong = Ed25519PrivateKey.generate()
    with pytest.raises(Exception):
        wrong.public_key().verify(base64.b64decode(signed.signature), bytes.fromhex(digest))
    # envelope round-trips as JSON (what Activities.sign_receipt returns)
    again = SignedReceipt.from_json(signed.to_json())
    assert again == signed


def test_published_public_key_file_verifies(tmp_path: Path):
    key = Ed25519PrivateKey.generate()
    pub_path = _write_public(tmp_path / "receipt_ed25519.pub.pem", key)
    signed = sign_links(build_links(**LINKS), key, "rouvr-receipt-1")
    assert verify_receipt(signed, load_public_key(pub_path)) is True


# --- AC-3: 0600 enforcement; no key material in envelope/errors ---

def test_private_key_requires_0600(tmp_path: Path):
    key = Ed25519PrivateKey.generate()
    loose = _write_private(tmp_path / "loose.pem", key, mode=0o644)
    with pytest.raises(RouvrError) as e:
        load_private_key(loose)
    assert e.value.code is Code.EVIDENCE
    strict = _write_private(tmp_path / "strict.pem", key, mode=0o600)
    assert isinstance(load_private_key(strict), Ed25519PrivateKey)
    assert stat.S_IMODE(strict.stat().st_mode) == 0o600


def test_missing_and_wrong_type_key_fail_closed(tmp_path: Path):
    with pytest.raises(RouvrError) as e:
        load_private_key(tmp_path / "nope.pem")
    assert e.value.code is Code.EVIDENCE
    # RSA key in an Ed25519 slot must be refused, never coerced.
    from cryptography.hazmat.primitives.asymmetric import rsa

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    p = tmp_path / "rsa.pem"
    p.write_bytes(rsa_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    os.chmod(p, 0o600)
    with pytest.raises(RouvrError) as e2:
        load_private_key(p)
    assert e2.value.code is Code.EVIDENCE


def test_envelope_and_errors_carry_no_key_material(tmp_path: Path):
    key = Ed25519PrivateKey.generate()
    priv = _write_private(tmp_path / "k.pem", key)
    pem_text = priv.read_text()
    signed = sign_links(build_links(**LINKS), load_private_key(priv), "k1")
    blob = signed.to_json()
    assert "PRIVATE KEY" not in blob
    # raw private scalar bytes must not appear (hex or b64) in the envelope
    raw = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption())
    assert raw.hex() not in blob
    assert base64.b64encode(raw).decode() not in blob
    assert pem_text.strip() not in blob
