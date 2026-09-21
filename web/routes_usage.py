"""
What this session has spent on model calls.

Purpose: Report the signed-in user's token usage since they signed in.
Spec:    docs/implementation_plan_2026-09-20_references_batch.md#M1.C.1
Tests:   tests/web/test_usage_routes.py

Read-only. The numbers come from usage_events, which is written by
user_store.record_spend on every path that calls a model.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Cookie, Depends

from src import tokens, user_store
from src.crypto import KeyEncryptionUnavailable
from src.llm_config import summary_daily_cap
from src.llm_providers import resolve_client

from .auth import current_user, get_context, session_started_at
from .deps import AppContext
from .routes_summaries import USAGE_KIND

logger = logging.getLogger(__name__)

router = APIRouter()

_EMPTY = {"prompt": 0, "completion": 0, "total": 0,
          "counted_calls": 0, "uncounted_calls": 0}


@router.get("/api/usage/session")
def session_usage(ctx: AppContext = Depends(get_context),
                  user_id: str = Depends(current_user),
                  biorx_session: Optional[str] = Cookie(default=None)):
    """Tokens this user has spent since this session began.

    The window starts at the cookie's issue time, so signing out and back in
    starts a fresh count. With no readable issue time the answer is zeros, not
    a lifetime total: current_user has already accepted the cookie by the time
    this runs, so the only way here is a serializer that can no longer read it,
    and inventing a wider window would show this user spending that is not
    theirs to see in this session.
    """
    started = session_started_at(ctx, biorx_session)
    if started is None:
        logger.warning("Session usage asked for with no readable cookie timestamp; "
                       "reporting an empty window rather than a lifetime total")
        return dict(_EMPTY, session_start=None)

    totals = user_store.session_token_totals(ctx.db, user_id, started)
    return dict(totals, session_start=started.isoformat())


@router.get("/api/usage/estimate")
def estimate(papers: int = 0,
             ctx: AppContext = Depends(get_context),
             user_id: str = Depends(current_user)):
    """What summarizing `papers` papers is likely to cost (M5.A.1, M5.A.3).

    A range, never a figure: the upper bound is max_text_chars, the hard cap on
    what is ever sent, and the lower bound a short paper — what this cannot know
    is which a given paper turns out to be. `exact` is False and the caller must
    label it an estimate.

    Also reports which key would pay and, on the shared key, how much of today's
    allowance is left, so the confirm dialog can say who is being charged before
    anything is spent.
    """
    resolved = resolve_client(*_user_credentials(ctx, user_id), config=ctx.llm_config)

    figures = tokens.estimate_summary_tokens(max(0, papers), ctx.llm_config,
                                             resolved.model)
    cap = summary_daily_cap(ctx.llm_config)
    remaining = None
    if resolved.billed_to_owner:
        remaining = max(0, cap - user_store.owner_usage_today(
            ctx.db, user_id, USAGE_KIND))

    return dict(figures,
                provider=resolved.provider,
                key_source=resolved.key_source,
                billed_to_owner=resolved.billed_to_owner,
                cap_remaining=remaining)


def _user_credentials(ctx: AppContext, user_id: str):
    """(provider, key, model) for this user, or blanks when none is stored.

    A key that will not decrypt is treated as absent here rather than raising:
    this endpoint only estimates, and POST /api/summaries answers that problem
    properly, with the right status, before anything is spent. Only that one
    failure is caught — a broad except here would hide a real database error
    behind a plausible-looking estimate (P2).
    """
    try:
        provider, key = user_store.get_llm_key(ctx.db, user_id)
    except KeyEncryptionUnavailable:
        logger.info("Estimating with no user key: the stored one cannot be read")
        provider, key = "", ""
    user = user_store.get_user(ctx.db, user_id) or {}
    return provider, key, user.get("preferred_model") or ""
