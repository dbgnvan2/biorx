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
Since personal access codes (docs/implementation_plan_2026-09-18_invite_codes.md)
the code picks the user and the PIN proves it; every request also checks that
the user's code is still valid.
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
SIGN_IN_MESSAGE = "Sign in to continue."


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=SALT)


def check_access_code(supplied: str, expected: str) -> bool:
    """Constant-time comparison, so the code cannot be recovered by timing."""
    if not expected:
        return False
    # Bytes, not str: compare_digest raises TypeError on non-ASCII strings,
    # which turned a typed "é" into a 500 (csdp security review).
    return hmac.compare_digest(supplied.strip().encode(), expected.strip().encode())


def issue_session(response: Response, ctx: AppContext, user_id: str,
                  secure: bool = True) -> None:
    """Sign the user id, and the account's session nonce, into the cookie. A
    SignedIn id carries the nonce read when its PIN was accepted; use it."""
    nonce = getattr(user_id, "nonce", None)
    user_id = getattr(user_id, "cookie_user", None) or user_id
    if nonce is None:
        row = ctx.db.conn.execute("SELECT session_nonce FROM users WHERE user_id = ?",
                                  (user_id,)).fetchone()
        nonce = (row["session_nonce"] or "") if row else ""
    user_id = str(user_id)
    token = _serializer(ctx.session_secret).dumps({"u": user_id, "n": nonce})
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
    got = read_session_nonce(ctx, token)
    return got[0] if got else None


def read_session_nonce(ctx: AppContext, token: Optional[str]):
    """(user_id, nonce) from a cookie, or None. Cookies from before nonces carry
    a bare user id and count as nonce "" (valid until the account's first reset)."""
    if not token:
        return None
    try:
        data = _serializer(ctx.session_secret).loads(
            token, max_age=SESSION_MAX_AGE_SECONDS
        )
        if isinstance(data, str):
            return data, ""
        if isinstance(data, dict) and isinstance(data.get("u"), str):
            return data["u"], str(data.get("n") or "")
        return None
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
    got = read_session_nonce(ctx, biorx_session)
    user_id = None
    if got:
        cookie_user, nonce = got
        # A user merged into another (src/accounts.py) resolves to the account
        # the data now lives in. A broken merge chain (None) is refused, not
        # treated as the merged-away account.
        from src.accounts import resolve_user_id
        user_id = resolve_user_id(ctx.db, cookie_user)
        if user_id:
            row = ctx.db.conn.execute("SELECT session_nonce FROM users WHERE user_id = ?",
                                      (cookie_user,)).fetchone()
            # Compared on the account the cookie NAMES. A reset of the account
            # it resolves to renews the nonce of every account merged into it
            # (accounts.end_sessions), so merged-away cookies end too; a merge
            # changes no nonce, so it neither signs anyone out nor revives an
            # ended cookie.
            if not hmac.compare_digest((row["session_nonce"] or "").encode(), nonce.encode()):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                    detail="Your PIN was changed. Sign in again.")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=SIGN_IN_MESSAGE,
        )
    if user_store.get_user(ctx.db, user_id) is None:
        # A validly-signed cookie for a user row that no longer exists.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This session is no longer valid. Sign in again.",
        )
    if ctx.codes is not None:
        # A code that has expired, been turned off or deleted ends the session
        # on the next request, not when the 30-day cookie runs out (PC5).
        from src.access_codes import session_refusal
        reason = session_refusal(ctx.db, ctx.codes, user_id, bool(ctx.access_code))
        if reason:
            from src.access_codes import ENTRY_PROBLEM_MESSAGE, UNAVAILABLE_MESSAGE
            code = (status.HTTP_503_SERVICE_UNAVAILABLE
                    if reason in (UNAVAILABLE_MESSAGE, ENTRY_PROBLEM_MESSAGE)
                    else status.HTTP_401_UNAUTHORIZED)
            raise HTTPException(status_code=code, detail=reason)
    return user_id
