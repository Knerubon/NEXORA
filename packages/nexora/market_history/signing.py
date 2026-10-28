"""Ed25519 signatures for custody evidence (V4, round 3).

A hash chain proves that entries were not edited after the fact; it does not prove WHO appended
them. Every custody statement that can grant trust (operator attestation, custodian approval, head
pin) is therefore signed, and the verifier checks the signature against public keys in the
principal registry it supplies itself (`custody.PrincipalRegistry`). A store appender without a
registered private key cannot produce an accepted statement.

Signatures cover `DOMAIN || purpose || NUL || canonical_json(body)`, so a signature made for one
purpose (say an operator attestation) can never be replayed as another (an approval).

No private key is ever stored in the repository, fixtures, manifests or the evidence store. Key
files live with their holder, outside the dataset; `load_signer` refuses a key file that is a
symlink or readable by group/other.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from nexora.market_history.models import HistoryError, canonical_json, sha256_hex

DOMAIN = b"nexora.market-history/signature/1\x00"
PURPOSES = frozenset({"operator-attestation", "custody-approval", "custody-revocation", "head-pin"})
_HEX = re.compile(r"[0-9a-f]+")
_MAX_KEY_FILE = 4096


def _message(purpose: str, body: Mapping[str, Any]) -> bytes:
    if purpose not in PURPOSES:
        raise HistoryError("signature_purpose_unknown", purpose[:40])
    return DOMAIN + purpose.encode() + b"\x00" + canonical_json(dict(body))


def key_id_of(public_hex: str) -> str:
    """Key identifier = SHA-256 of the raw 32-byte public key. Derived, never declared."""
    return sha256_hex(bytes.fromhex(public_hex))


def is_public_key_hex(text: Any) -> bool:
    return type(text) is str and len(text) == 64 and _HEX.fullmatch(text) is not None


def is_signature_hex(text: Any) -> bool:
    return type(text) is str and len(text) == 128 and _HEX.fullmatch(text) is not None


def verify(public_hex: str, purpose: str, body: Mapping[str, Any], signature_hex: Any) -> bool:
    """True only for a well-formed signature by `public_hex` over exactly `body` for `purpose`.
    Never raises on hostile input."""
    if not is_public_key_hex(public_hex) or not is_signature_hex(signature_hex):
        return False
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
        key.verify(bytes.fromhex(signature_hex), _message(purpose, body))
    except (InvalidSignature, ValueError, HistoryError):
        return False
    return True


@dataclass(frozen=True, slots=True)
class Signer:
    """A private signing key held by one principal. `repr` never shows key material."""

    _key: Ed25519PrivateKey

    @property
    def public_hex(self) -> str:
        raw = self._key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return raw.hex()

    @property
    def key_id(self) -> str:
        return key_id_of(self.public_hex)

    def sign(self, purpose: str, body: Mapping[str, Any]) -> str:
        return self._key.sign(_message(purpose, body)).hex()

    def __repr__(self) -> str:
        return f"Signer(key_id={self.key_id[:16]}...)"

    def private_pem(self) -> bytes:
        return self._key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )


def generate_signer() -> Signer:
    return Signer(Ed25519PrivateKey.generate())


def load_signer(path: Path) -> Signer:
    """Read an unencrypted PKCS#8 PEM Ed25519 private key. The final component must not be a
    symlink and (POSIX) the file must not be readable by group or other."""
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
    except OSError as exc:
        raise HistoryError("signing_key_unreadable", type(exc).__name__) from None
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise HistoryError("signing_key_unreadable", "not a regular file")
        if os.name == "posix" and st.st_mode & 0o077:
            raise HistoryError("signing_key_permissions", "must not be group/other accessible")
        data = fh.read(_MAX_KEY_FILE)
    try:
        key = serialization.load_pem_private_key(data, password=None)
    except (ValueError, TypeError):
        raise HistoryError("signing_key_unreadable", "not a PEM private key") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise HistoryError("signing_key_unreadable", "not an Ed25519 key")
    return Signer(key)


def write_signer(signer: Signer, path: Path) -> None:
    """Create `path` (0600, never overwrite) holding the private key."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(signer.private_pem())
        fh.flush()
        os.fsync(fh.fileno())
