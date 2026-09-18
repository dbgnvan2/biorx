"""
Tests for the bring-your-own-key routes.

Spec: docs/implementation_plan_2026-09-15.md#1.2, W3.b, W3.c, D3
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

KEY = "sk-ant-api03-SUPERSECRETVALUE7788"


def test_put_key_then_resolver_uses_it(ctx, signed_in, enc_secret):
    r = signed_in.put("/api/me/llm-key",
                      json={"provider": "deepseek", "api_key": KEY})
    assert r.status_code == 200
    assert r.json()["key_source"] == "user"

    from src import user_store
    provider, stored = user_store.get_llm_key(ctx.db, r.json()["user_id"])
    assert (provider, stored) == ("deepseek", KEY)


def test_key_is_never_returned_in_full(signed_in, enc_secret):
    body = signed_in.put("/api/me/llm-key",
                         json={"provider": "deepseek", "api_key": KEY}).json()
    serialized = str(body)
    assert KEY not in serialized
    assert "SUPERSECRET" not in serialized
    assert body["key_last4"] == "7788"

    me = str(signed_in.get("/api/me").json())
    assert KEY not in me and "SUPERSECRET" not in me


def test_the_ciphertext_in_the_database_is_not_the_key(ctx, signed_in, enc_secret):
    signed_in.put("/api/me/llm-key", json={"provider": "deepseek", "api_key": KEY})
    row = ctx.db.conn.execute("SELECT llm_key_ciphertext FROM users").fetchone()
    assert KEY.encode() not in bytes(row["llm_key_ciphertext"])


def test_key_never_appears_in_logs(signed_in, enc_secret, caplog):
    with caplog.at_level("DEBUG"):
        signed_in.put("/api/me/llm-key", json={"provider": "deepseek", "api_key": KEY})
        signed_in.get("/api/me")
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert KEY not in logged
    assert "SUPERSECRET" not in logged


def test_removing_a_key_falls_back_to_the_owner(signed_in, enc_secret, monkeypatch, ctx):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-owner")
    from src.llm_config import load_llm_config
    ctx.llm_config = load_llm_config()

    signed_in.put("/api/me/llm-key", json={"provider": "deepseek", "api_key": KEY})
    assert signed_in.get("/api/me").json()["key_source"] == "user"

    body = signed_in.delete("/api/me/llm-key").json()
    assert body["key_source"] == "owner"
    assert body["key_last4"] == ""


def test_storing_a_key_is_refused_when_encryption_is_unavailable(signed_in):
    """KEY_ENC_SECRET absent: refuse, never store plaintext (decision §1.2)."""
    r = signed_in.put("/api/me/llm-key", json={"provider": "deepseek", "api_key": KEY})
    assert r.status_code == 503
    assert "KEY_ENC_SECRET" in r.json()["detail"]


def test_the_ui_is_told_why_byo_is_unavailable(signed_in):
    me = signed_in.get("/api/me").json()
    assert me["byo_enabled"] is False
    assert "KEY_ENC_SECRET" in me["byo_disabled_reason"]


def test_an_unknown_provider_is_rejected(signed_in, enc_secret):
    r = signed_in.put("/api/me/llm-key",
                      json={"provider": "not-a-provider", "api_key": KEY})
    assert r.status_code == 400


def test_a_short_key_is_rejected(signed_in, enc_secret):
    assert signed_in.put("/api/me/llm-key",
                         json={"provider": "deepseek", "api_key": "abc"}).status_code == 422


def test_an_undecryptable_stored_key_does_not_silently_spend_the_owners(
    ctx, signed_in, enc_secret, monkeypatch
):
    """
    If KEY_ENC_SECRET changes, a stored key stops decrypting. That must surface,
    not fall through to the owner's credential (learnings P2/P14).
    """
    signed_in.put("/api/me/llm-key", json={"provider": "deepseek", "api_key": KEY})
    monkeypatch.setenv("KEY_ENC_SECRET", "a-completely-different-long-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-owner")

    r = signed_in.post("/api/summaries", json={"paper": {"title": "t", "abstract": "a"}})
    assert r.status_code == 503
    assert "could not be read" in r.json()["detail"]


# ── The profile payload is an exact, enumerated shape ─────────────────────────

PROFILE_FIELDS = {
    "user_id", "display_name", "provider", "model", "default_model",
    "preferred_model", "key_source", "key_last4",
    "byo_enabled", "byo_disabled_reason", "owner_summaries_used_today",
    "owner_summaries_cap", "owner_summaries_remaining", "available_providers",
}


def test_the_profile_payload_has_exactly_these_fields(signed_in, enc_secret):
    """
    An exact enumeration, not a floor (learnings P29): a field added to the
    profile — a debug dump, the ciphertext, a token — fails here rather than
    quietly shipping to the browser.
    """
    signed_in.put("/api/me/llm-key", json={"provider": "deepseek", "api_key": KEY})
    body = signed_in.get("/api/me").json()
    assert set(body) == PROFILE_FIELDS


def test_the_profile_never_carries_the_stored_ciphertext(ctx, signed_in, enc_secret):
    """
    The encrypted blob must not leave the server either. It is not the key, but
    it is one leaked KEY_ENC_SECRET away from being the key, and nothing in the
    browser needs it.
    """
    signed_in.put("/api/me/llm-key", json={"provider": "deepseek", "api_key": KEY})
    ciphertext = bytes(
        ctx.db.conn.execute("SELECT llm_key_ciphertext FROM users").fetchone()[0]
    )

    for response in (signed_in.get("/api/me"), signed_in.delete("/api/me/llm-key")):
        serialized = response.text
        assert "gAAAAA" not in serialized, "a Fernet token reached the response"
        assert ciphertext.decode("utf-8", "ignore")[:24] not in serialized


# ── ME1: the profile reports the model that will actually run ─────────────────

def test_me1_preferred_model_without_own_key_reports_the_default(signed_in, monkeypatch, ctx):
    """resolve_client ignores the preferred model on the owner key, so /api/me
    must not claim it will be used."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-owner")
    from src.llm_config import load_llm_config
    ctx.llm_config = load_llm_config()
    body = signed_in.put("/api/me/llm-model", json={"model": "claude-haiku-4-5"}).json()
    assert body["key_source"] == "owner"
    assert body["preferred_model"] == "claude-haiku-4-5"
    assert body["model"] == body["default_model"] != "claude-haiku-4-5"


def test_me1_preferred_model_with_own_key_is_reported(signed_in, enc_secret):
    signed_in.put("/api/me/llm-key", json={"provider": "anthropic", "api_key": KEY,
                                          "model": "claude-haiku-4-5"})
    body = signed_in.get("/api/me").json()
    assert body["key_source"] == "user"
    assert body["model"] == "claude-haiku-4-5"
