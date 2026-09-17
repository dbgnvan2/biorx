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

from src import crypto, user_store
from src.filters_store import load_filters_file
from src.llm_config import default_provider, provider_config, summary_daily_cap

from .auth import check_access_code, clear_session, current_user, get_context, issue_session
from .deps import AppContext

logger = logging.getLogger(__name__)
router = APIRouter()


class SessionRequest(BaseModel):
    access_code: str = Field(min_length=1, max_length=200)
    display_name: str = Field(default="", max_length=100)


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
    effective_model = preferred_model or (pconf.model if pconf else "")

    used = user_store.owner_usage_today(ctx.db, user_id)
    cap = summary_daily_cap(ctx.llm_config)
    return {
        "user_id": user_id,
        "display_name": user.get("display_name", ""),
        "provider": effective,
        "model": effective_model,
        "default_model": pconf.model if pconf else "",
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


@router.post("/api/session")
def create_session(body: SessionRequest, response: Response,
                   ctx: AppContext = Depends(get_context)):
    """Exchange the shared access code for a signed session cookie.

    The display name is stored as a label. It is never used to look a user up;
    identity is the opaque id minted here.
    """
    if not check_access_code(body.access_code, ctx.access_code):
        logger.info("Rejected a session request with a wrong access code")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="That access code is not right.")

    user_id = user_store.create_user(ctx.db, body.display_name)
    try:
        seeded = user_store.seed_filters_from_file(
            ctx.db, user_id, load_filters_file("filters.json")
        )
        logger.info("Seeded %d filters for new user %s", seeded, user_id)
    except Exception:
        # A missing or unreadable filters.json must not stop someone signing in.
        logger.exception("Could not seed filters for %s", user_id)

    issue_session(response, ctx, user_id, secure=ctx.cookie_secure)
    return _me(ctx, user_id)


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
