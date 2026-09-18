"""
Purpose: The access-code gate and the cookie-bound user identity.
Spec:    docs/implementation_plan_2026-09-15.md#1.3, W4.a, W4.b
Tests:   tests/web/test_auth.py

Identity is a server-issued opaque id carried in a signed cookie. The display
name is a label with no authority.

That distinction is the whole point. With a shared access code and a
self-declared name, anyone holding the code could type a colleague's name and
spend that colleague's API key. A name selects a user only together with that
user's PIN (src/accounts.py, 2026-09-18); the cookie then carries the opaque id.
"""

from __future__ import annotations

import hmac
import logging
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from src import user_store

from .deps import SESSION_COOKIE, SESSION_MAX_AGE_SECONDS, AppContext

logger = logging.getLogger(__name__)

SALT = "biorx-session-v1"


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=SALT)


def check_access_code(supplied: str, expected: str) -> bool:
    """Constant-time comparison, so the code cannot be recovered by timing."""
    if not expected:
        return False
    return hmac.compare_digest(supplied.strip(), expected.strip())


def issue_session(response: Response, ctx: AppContext, user_id: str,
                  secure: bool = True) -> None:
    """Sign the user id into the session cookie."""
    token = _serializer(ctx.session_secret).dumps(user_id)
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,          # not readable by page scripts
        samesite="lax",         # not sent on cross-site POSTs
        secure=secure,          # HTTPS only in production
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)


def read_session(ctx: AppContext, token: Optional[str]) -> Optional[str]:
    """Return the user id in a cookie, or None if it is absent or untrustworthy."""
    if not token:
        return None
    try:
        return _serializer(ctx.session_secret).loads(
            token, max_age=SESSION_MAX_AGE_SECONDS
        )
    except SignatureExpired:
        logger.info("Session cookie expired")
        return None
    except BadSignature:
        # Tampered, or signed with a different secret (a restart without
        # SESSION_SECRET set). Either way it grants nothing.
        logger.info("Session cookie failed signature check")
        return None


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx


async def current_user(
    request: Request,
    ctx: AppContext = Depends(get_context),
    biorx_session: Optional[str] = Cookie(default=None),
) -> str:
    """The signed-in user's id, or 401.

    Applied to every /api route except the one that creates a session, so a
    write endpoint cannot be reached unauthenticated (security S3).
    """
    user_id = read_session(ctx, biorx_session)
    if user_id:
        # A user merged into another (src/accounts.py) keeps working: the
        # cookie resolves to the account the data now lives in.
        from src.accounts import resolve_user_id
        user_id = resolve_user_id(ctx.db, user_id) or user_id
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Enter the access code to continue.",
        )
    if user_store.get_user(ctx.db, user_id) is None:
        # A validly-signed cookie for a user row that no longer exists.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This session is no longer valid. Enter the access code again.",
        )
    return user_id
