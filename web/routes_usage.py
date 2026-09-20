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

from src import user_store

from .auth import current_user, get_context, session_started_at
from .deps import AppContext

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
