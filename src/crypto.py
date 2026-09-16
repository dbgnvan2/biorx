"""
Purpose: Encrypt a user's LLM API key at rest, and mask it for display.
Spec:    docs/implementation_plan_2026-09-15.md#1.2, W3.c, W3.d
Tests:   tests/web/test_crypto.py

KEY_ENC_SECRET is REQUIRED from the environment and is never generated. A secret
generated on first run would be written to the same disk as the ciphertext it
protects, which makes the encryption decorative. When it is absent, storing a
user key is refused and the UI says so — the app still runs on the owner key.

What this does and does not buy: it protects a copied volume, a backup, or a
`SELECT *` during support. It does not protect a host on which an attacker can
already read the process environment.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

ENV_VAR = "KEY_ENC_SECRET"
MIN_SECRET_LENGTH = 16
MASK_VISIBLE_CHARS = 4


class KeyEncryptionUnavailable(RuntimeError):
    """Raised when a key cannot be encrypted or decrypted.

    A typed error, not a sentinel string: a caller must not be able to store the
    words "encryption unavailable" into the key column by accident (P14).
    """


def _fernet():
    """Build a Fernet from KEY_ENC_SECRET, or raise KeyEncryptionUnavailable."""
    secret = os.environ.get(ENV_VAR, "")
    if not secret:
        raise KeyEncryptionUnavailable(
            f"{ENV_VAR} is not set — per-user API keys cannot be stored. "
            "Set it in the environment; it is never generated automatically."
        )
    if len(secret) < MIN_SECRET_LENGTH:
        raise KeyEncryptionUnavailable(
            f"{ENV_VAR} must be at least {MIN_SECRET_LENGTH} characters"
        )
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:      # pragma: no cover - depends on the install
        raise KeyEncryptionUnavailable(
            "the `cryptography` package is not installed"
        ) from e

    # Any secret string is accepted and stretched to Fernet's required 32-byte
    # urlsafe-base64 key, so the operator does not have to generate one in a
    # particular format.
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def is_enabled() -> bool:
    """True when a user key could be stored right now.

    Read at call time so a process can be started, inspected, and corrected
    without a restart changing the answer behind the caller's back.
    """
    try:
        _fernet()
        return True
    except KeyEncryptionUnavailable:
        return False


def unavailable_reason() -> Optional[str]:
    """Why key storage is unavailable, or None when it is available.

    Safe to show a user: it names the missing configuration, never a key.
    """
    try:
        _fernet()
        return None
    except KeyEncryptionUnavailable as e:
        return str(e)


def encrypt_key(plaintext: str) -> bytes:
    """Encrypt an API key for storage. Raises KeyEncryptionUnavailable."""
    if not plaintext:
        raise ValueError("refusing to encrypt an empty key")
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_key(ciphertext: bytes) -> str:
    """Decrypt a stored API key. Raises KeyEncryptionUnavailable.

    A ciphertext that will not decrypt — because KEY_ENC_SECRET changed, or the
    row was tampered with — is an error, never a silently empty key that would
    look like "this user has no key" and quietly fall back to the owner's.
    """
    if not ciphertext:
        raise KeyEncryptionUnavailable("no stored key")
    try:
        from cryptography.fernet import InvalidToken
    except ImportError as e:      # pragma: no cover
        raise KeyEncryptionUnavailable("the `cryptography` package is not installed") from e
    try:
        return _fernet().decrypt(bytes(ciphertext)).decode("utf-8")
    except InvalidToken as e:
        raise KeyEncryptionUnavailable(
            f"stored key could not be decrypted — has {ENV_VAR} changed?"
        ) from e


def mask(key: str) -> str:
    """Render a key for display: the last four characters only.

    Used everywhere a key would otherwise reach a response body, a log line, or
    a template. Short or empty input never leaks more than it should.
    """
    if not key:
        return ""
    tail = key[-MASK_VISIBLE_CHARS:] if len(key) > MASK_VISIBLE_CHARS else key[-1:]
    return f"…{tail}"


def last4(key: str) -> str:
    """The stored display fragment: at most the last four characters, no ellipsis."""
    if not key:
        return ""
    return key[-MASK_VISIBLE_CHARS:] if len(key) > MASK_VISIBLE_CHARS else key[-1:]
