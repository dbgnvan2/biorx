"""
Purpose: Sign in with the shared access code; read and edit your own profile.
Spec:    docs/implementation_plan_2026-09-15.md#2.2, W3.b, W3.c, W4
Tests:   tests/web/test_auth.py, tests/web/test_llm_key_routes.py
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from src import accounts, crypto, user_store
from src.filters_store import load_filters_file
from src.llm_config import default_provider, provider_config, summary_daily_cap

from .auth import check_access_code, clear_session, current_user, get_context, issue_session
from .deps import AppContext

logger = logging.getLogger(__name__)
router = APIRouter()


class SessionRequest(BaseModel):
    """Sign in (create=False) or create an account (create=True).
    Spec: docs/implementation_plan_2026-09-18_accounts.md#AC1-AC2"""
    access_code: str = Field(min_length=1, max_length=200)
    name: str = Field(default="", max_length=100)
    pin: str = Field(default="", max_length=200)
    create: bool = False


class RecoverRequest(BaseModel):
    access_code: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    recovery_code: str = Field(min_length=1, max_length=100)
    new_pin: str = Field(min_length=1, max_length=200)


class ClaimRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    pin: str = Field(min_length=1, max_length=200)


class DisplayNameRequest(BaseModel):
    display_name: str = Field(default="", max_length=100)


class LlmKeyRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    api_key: str = Field(min_length=8, max_length=500)
    model: str = Field(default="", max_length=200)


class LlmModelRequest(BaseModel):
    model: str = Field(default="", max_length=200)


def _me(ctx: AppContext, user_id: str) -> dict:
    """The profile payload. Never contains a key, only its last four characters."""
    user = user_store.get_user(ctx.db, user_id) or {}
    stored_provider = user.get("llm_provider") or ""
    effective = stored_provider or default_provider(ctx.llm_config)
    pconf = provider_config(ctx.llm_config, effective)

    has_key = bool(user.get("llm_key_ciphertext"))
    if has_key:
        key_source = "user"
    elif pconf and not pconf.needs_key:
        key_source = "none"          # local Ollama: nobody is billed
    elif pconf and pconf.owner_key():
        key_source = "owner"
    else:
        key_source = "missing"

    preferred_model = user.get("preferred_model") or ""
    default_model = pconf.model if pconf else ""
    # resolve_client honours the preferred model only on the user's own stored
    # key; on the owner key or local Ollama the config model runs. Report the
    # one that will actually run. (A key kept only in the browser is not known
    # here; the client shows its model itself.)
    effective_model = (preferred_model or default_model) if key_source == "user" else default_model

    used = user_store.owner_usage_today(ctx.db, user_id)
    cap = summary_daily_cap(ctx.llm_config)
    return {
        "user_id": user_id,
        "display_name": user.get("display_name", ""),
        "login_name": user.get("login_name") or "",
        "provider": effective,
        "model": effective_model,
        "default_model": default_model,
        "preferred_model": preferred_model,
        "key_source": key_source,
        "key_last4": user.get("llm_key_last4") or "",
        "byo_enabled": crypto.is_enabled(),
        "byo_disabled_reason": crypto.unavailable_reason() or "",
        "owner_summaries_used_today": used,
        "owner_summaries_cap": cap,
        "owner_summaries_remaining": max(0, cap - used),
        "available_providers": sorted(ctx.llm_config.get("providers", {})),
    }


def _gate(ctx: AppContext, access_code: str) -> None:
    if not check_access_code(access_code, ctx.access_code):
        logger.info("Rejected a session request with a wrong access code")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="That access code is not right.")


def _account_error(e: accounts.AccountError) -> HTTPException:
    if isinstance(e, accounts.AccountLocked):
        code = status.HTTP_429_TOO_MANY_REQUESTS
    elif isinstance(e, accounts.BadCredentials):
        code = status.HTTP_401_UNAUTHORIZED
    elif isinstance(e, accounts.NameTaken):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=str(e))


def _seed_filters(ctx: AppContext, user_id: str) -> None:
    try:
        seeded = user_store.seed_filters_from_file(
            ctx.db, user_id, load_filters_file("filters.json")
        )
        logger.info("Seeded %d filters for new user %s", seeded, user_id)
    except Exception:
        # A missing or unreadable filters.json must not stop someone signing in.
        logger.exception("Could not seed filters for %s", user_id)


@router.post("/api/session")
def create_session(body: SessionRequest, response: Response,
                   ctx: AppContext = Depends(get_context)):
    """Access code, then name + PIN: sign in, or create an account.

    The name + PIN select the user (accounts.py); identity in the cookie is
    still the opaque server id. A new account's response carries its recovery
    code once — it is not stored in a readable form.
    """
    _gate(ctx, body.access_code)
    if not body.name.strip() or not body.pin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Enter your name and PIN.")
    recovery_code = None
    try:
        if body.create:
            user_id, recovery_code = accounts.create_account(
                ctx.db, body.name, body.pin, user_store.new_user_id)
            _seed_filters(ctx, user_id)
        else:
            user_id = accounts.sign_in(ctx.db, body.name, body.pin)
    except accounts.AccountError as e:
        raise _account_error(e) from e
    issue_session(response, ctx, user_id, secure=ctx.cookie_secure)
    out = _me(ctx, user_id)
    if recovery_code:
        out["recovery_code"] = recovery_code
    return out


@router.post("/api/session/recover")
def recover_session(body: RecoverRequest, response: Response,
                    ctx: AppContext = Depends(get_context)):
    """Forgot PIN: name + recovery code + new PIN. Returns a new recovery code."""
    _gate(ctx, body.access_code)
    try:
        user_id, code = accounts.recover(ctx.db, body.name, body.recovery_code, body.new_pin)
    except accounts.AccountError as e:
        raise _account_error(e) from e
    user_id = accounts.resolve_user_id(ctx.db, user_id) or user_id
    issue_session(response, ctx, user_id, secure=ctx.cookie_secure)
    out = _me(ctx, user_id)
    out["recovery_code"] = code
    return out


@router.post("/api/me/account")
def claim_account(body: ClaimRequest,
                  ctx: AppContext = Depends(get_context),
                  user_id: str = Depends(current_user)):
    """Give the account you are in (cookie only) a name + PIN (AC6)."""
    try:
        code = accounts.claim(ctx.db, user_id, body.name, body.pin)
    except accounts.AccountError as e:
        raise _account_error(e) from e
    out = _me(ctx, user_id)
    out["recovery_code"] = code
    return out


@router.delete("/api/session")
def end_session(response: Response):
    clear_session(response)
    return {"ok": True}


@router.get("/api/me")
def read_me(ctx: AppContext = Depends(get_context),
            user_id: str = Depends(current_user)):
    user_store.touch_user(ctx.db, user_id)
    return _me(ctx, user_id)


@router.patch("/api/me")
def update_me(body: DisplayNameRequest,
              ctx: AppContext = Depends(get_context),
              user_id: str = Depends(current_user)):
    user_store.set_display_name(ctx.db, user_id, body.display_name)
    return _me(ctx, user_id)


@router.put("/api/me/llm-key")
def put_llm_key(body: LlmKeyRequest,
                ctx: AppContext = Depends(get_context),
                user_id: str = Depends(current_user)):
    """Store this user's own API key, encrypted.

    The response carries the masked fragment only; the key never travels back.
    """
    if provider_config(ctx.llm_config, body.provider) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{body.provider!r} is not a configured provider.",
        )
    try:
        user_store.set_llm_key(ctx.db, user_id, body.provider, body.api_key)
        if body.model:
            user_store.set_preferred_model(ctx.db, user_id, body.model)
    except crypto.KeyEncryptionUnavailable as e:
        # Storage is off because KEY_ENC_SECRET is absent. Say so plainly
        # rather than storing the key unencrypted.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=str(e)) from e
    return _me(ctx, user_id)


@router.delete("/api/me/llm-key")
def delete_llm_key(ctx: AppContext = Depends(get_context),
                   user_id: str = Depends(current_user)):
    user_store.clear_llm_key(ctx.db, user_id)
    return _me(ctx, user_id)


@router.put("/api/me/llm-model")
def put_llm_model(body: LlmModelRequest,
                  ctx: AppContext = Depends(get_context),
                  user_id: str = Depends(current_user)):
    """Store this user's preferred model name.

    An empty string clears the preference and falls back to the config default.
    """
    user_store.set_preferred_model(ctx.db, user_id, body.model)
    return _me(ctx, user_id)
