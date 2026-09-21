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
from pydantic import BaseModel, Field

from src import tokens, user_store
from src.llm_config import summary_daily_cap
from src.llm_providers import LLMError

from .auth import current_user, get_context, session_started_at
from .deps import AppContext
from .routes_summaries import USAGE_KIND, resolve_credentials

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


class EstimateRequest(BaseModel):
    """The same inline-credential fields POST /api/summaries takes.

    A POST, not a GET with query parameters: the inline key must never appear
    in a URL, where it would reach access logs and browser history.
    """
    papers: int = Field(default=0, ge=0, le=10000)
    api_key: str = Field(default="", max_length=500)
    provider: str = Field(default="", max_length=50)
    model: str = Field(default="", max_length=200)


@router.post("/api/usage/estimate")
def estimate(body: EstimateRequest,
             ctx: AppContext = Depends(get_context),
             user_id: str = Depends(current_user)):
    """What summarizing `papers` papers is likely to cost (M5.A.1, M5.A.3).

    A range, never a figure: the upper bound is max_text_chars, the hard cap on
    what is ever sent, and the lower bound a short paper — what this cannot know
    is which a given paper turns out to be. `exact` is False and the caller must
    label it an estimate.

    Credentials resolve through the same function the spend path uses, so the
    dialog cannot name a different payer than the one who will actually be
    billed (gate 2026-09-21 finding 1).
    """
    try:
        resolved = resolve_credentials(ctx, user_id, body.api_key.strip(),
                                       body.provider, body.model)
    except LLMError as e:
        # No usable credential anywhere. The sibling POST /api/summaries
        # answers this properly when the user commits; the estimate reports it
        # rather than 500-ing (gate finding 2).
        logger.info("Estimate asked for with no usable credential: %s", e)
        return dict(tokens.estimate_summary_tokens(body.papers, ctx.llm_config, ""),
                    provider="", key_source="missing", billed_to_owner=False,
                    cap_remaining=None)

    figures = tokens.estimate_summary_tokens(body.papers, ctx.llm_config,
                                             resolved.model)
    remaining = None
    if resolved.billed_to_owner:
        cap = summary_daily_cap(ctx.llm_config)
        remaining = max(0, cap - user_store.owner_usage_today(
            ctx.db, user_id, USAGE_KIND))

    return dict(figures,
                provider=resolved.provider,
                key_source=resolved.key_source,
                billed_to_owner=resolved.billed_to_owner,
                cap_remaining=remaining)
