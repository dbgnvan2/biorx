"""
Tests for src/crypto.py — encryption of a user's API key at rest.

Spec: docs/implementation_plan_2026-09-15.md#1.2, W3.c, W3.d
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from src import crypto
from src.crypto import (
    KeyEncryptionUnavailable, decrypt_key, encrypt_key, is_enabled, last4, mask,
    unavailable_reason,
)

SECRET = "a-secret-long-enough-to-pass"
KEY = "sk-ant-api03-EXAMPLEEXAMPLEEXAMPLE1234"


@pytest.fixture
def enc(monkeypatch):
    monkeypatch.setenv(crypto.ENV_VAR, SECRET)


# ── Roundtrip ─────────────────────────────────────────────────────────────────

def test_key_roundtrips_through_fernet(enc):
    assert decrypt_key(encrypt_key(KEY)) == KEY


def test_stored_ciphertext_does_not_contain_plaintext(enc):
    blob = encrypt_key(KEY)
    assert KEY.encode() not in blob
    assert b"sk-ant" not in blob


def test_ciphertext_differs_between_encryptions_of_the_same_key(enc):
    """Fernet includes a timestamp and IV; identical keys must not look identical."""
    assert encrypt_key(KEY) != encrypt_key(KEY)


def test_any_secret_string_is_accepted(monkeypatch):
    monkeypatch.setenv(crypto.ENV_VAR, "not-base64-but-long-enough-!!")
    assert decrypt_key(encrypt_key(KEY)) == KEY


# ── The secret is required, never generated (decision §1.2) ───────────────────

def test_missing_enc_secret_disables_byo_storage_without_generating_one(monkeypatch):
    monkeypatch.delenv(crypto.ENV_VAR, raising=False)
    assert is_enabled() is False
    assert crypto.ENV_VAR in unavailable_reason()
    with pytest.raises(KeyEncryptionUnavailable):
        encrypt_key(KEY)
    # And nothing was written anywhere to make it work next time.
    assert monkeypatch.delenv(crypto.ENV_VAR, raising=False) is None
    assert is_enabled() is False


def test_a_too_short_secret_is_refused(monkeypatch):
    monkeypatch.setenv(crypto.ENV_VAR, "short")
    assert is_enabled() is False
    with pytest.raises(KeyEncryptionUnavailable):
        encrypt_key(KEY)


def test_is_enabled_is_read_at_call_time(monkeypatch):
    monkeypatch.delenv(crypto.ENV_VAR, raising=False)
    assert is_enabled() is False
    monkeypatch.setenv(crypto.ENV_VAR, SECRET)
    assert is_enabled() is True


# ── Failures are typed, never quietly empty ───────────────────────────────────

def test_a_changed_secret_raises_rather_than_yielding_an_empty_key(enc, monkeypatch):
    """
    A key that will not decrypt must not look like "this user has no key", which
    would silently fall back to spending the owner's credential (P14/P2).
    """
    blob = encrypt_key(KEY)
    monkeypatch.setenv(crypto.ENV_VAR, "a-completely-different-secret")
    with pytest.raises(KeyEncryptionUnavailable):
        decrypt_key(blob)


def test_tampered_ciphertext_raises(enc):
    blob = bytearray(encrypt_key(KEY))
    blob[-1] = blob[-1] ^ 0xFF
    with pytest.raises(KeyEncryptionUnavailable):
        decrypt_key(bytes(blob))


def test_empty_inputs_are_refused(enc):
    with pytest.raises(ValueError):
        encrypt_key("")
    with pytest.raises(KeyEncryptionUnavailable):
        decrypt_key(b"")


# ── Masking (W3.c) ────────────────────────────────────────────────────────────

def test_mask_shows_only_the_last_four_characters():
    assert mask(KEY) == "…1234"
    assert KEY[:-4] not in mask(KEY)


def test_last4_has_no_decoration():
    assert last4(KEY) == "1234"


@pytest.mark.parametrize("short", ["", "a", "abc"])
def test_mask_never_reveals_a_short_key(short):
    out = mask(short)
    assert len(out.replace("…", "")) <= 1
