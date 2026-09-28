"""
Admission and settlement for every billed model call the web app makes.

Purpose: One implementation of "who pays, may they, and what did it cost",
         shared by summaries, discover and reviews.
Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#M12, #A14, #M1
Tests:   tests/web/test_spend.py

Before this module the settlement block (record the spend if the provider was
reached, else give the reserved slot back, and always release the thread's
database connection) was copied into three routes, and the copies had drifted:
one route turned credential errors into a 500, another raised HTTPException
inside a worker. Routes now call admit() and wrap the work in BilledCall.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from src import user_store
from src.crypto import KeyEncryptionUnavailable
from src.llm_config import summary_daily_cap
from src.llm_providers import LLMError, NoLLMCredentialError, resolve_client

logger = logging.getLogger(__name__)

# Every model call is logged and capped under this one kind. Discover and
# reviews share it deliberately: they draw on the same daily allowance.
USAGE_KIND = "summary"


# The "resolved client" for a job that will not call a model (a stored summary
# is returned): nobody pays and nothing is reserved.
from types import SimpleNamespace as _NS
NO_SPEND = _NS(client=None, provider="", model="", key_source="none", billed_to_owner=False)


class SpendRefused(Exception):
    """A billed call may not start. `status` is the HTTP status a route answers
    with; the message is the user-facing reason."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def resolve_credentials(db, llm_config, user_id: str, inline_key: str = "",
                        inline_provider: str = "", inline_model: str = ""):
    """Which client would run for this user, reserving nothing.

    Used by the pre-spend estimate as well as by admit(), so both answer
    "who pays" the same way (gate 2026-09-21 finding 1).

    Raises the provider module's LLMError family (a caller that only
    estimates reports "no key" rather than failing) and SpendRefused(503) for a
    stored key that will not decrypt.
    """
    if inline_key.strip():
        # A key from the browser's localStorage, sent per request, never stored.
        return resolve_client(user_provider=inline_provider,
                              user_key=inline_key.strip(),
                              user_model=inline_model, config=llm_config)
    try:
        provider, key = user_store.get_llm_key(db, user_id)
    except KeyEncryptionUnavailable as e:
        # A stored key that will not decrypt must not silently fall through
        # to the owner's credential (learnings P2).
        raise SpendRefused(503, f"Your stored key could not be read: {e}") from e
    user_data = user_store.get_user(db, user_id) or {}
    return resolve_client(user_provider=provider, user_key=key,
                          user_model=user_data.get("preferred_model") or "",
                          config=llm_config)


def admit(db, llm_config, user_id: str, inline_key: str = "",
          inline_provider: str = "", inline_model: str = "") -> Tuple[Any, Optional[int]]:
    """Resolve the client and, on the owner's key, reserve one slot of today's cap.

    Returns (resolved, usage_id); usage_id is None when nothing was reserved.
    Raises SpendRefused (400 no key, 429 cap reached, 503 provider problem).

    The slot is reserved atomically here: reading the count now and writing
    after the job would let a burst of requests all pass (check-then-act).
    """
    try:
        resolved = resolve_credentials(db, llm_config, user_id, inline_key,
                                       inline_provider, inline_model)
    except NoLLMCredentialError as e:
        raise SpendRefused(400, str(e)) from e
    except LLMError as e:
        raise SpendRefused(503, str(e)) from e
    if not resolved.billed_to_owner:
        return resolved, None
    cap = summary_daily_cap(llm_config)
    usage_id = user_store.reserve_owner_usage(db, user_id, USAGE_KIND, cap,
                                              resolved.provider, resolved.model)
    if usage_id is None:
        raise SpendRefused(
            429, f"You have used today's {cap} summaries on the shared key. "
                 "Add your own API key in LLM settings to continue.")
    return resolved, usage_id


def release_unused(db, usage_id: Optional[int]) -> None:
    """Give back a reserved slot whose job never ran (review A14): cancelled
    while queued, or refused by a pool that was shutting down."""
    try:
        if usage_id is not None:
            user_store.release_usage(db, usage_id)
    finally:
        db.release()


class BilledCall:
    """Settles one billed job, however it ends.

    with BilledCall(db, user_id, resolved, usage_id, job) as call:
        ...
        call.model_called()                  # just before the provider call
        text, usage = client.generate(...)
        job.token_usage = usage

    On exit: if the provider was reached, what it cost is recorded (taking the
    usage an exception carries when the reply was unusable, M1.B.3); if it
    was never reached, the reserved slot is given back. The worker thread's
    database connection is always released.
    """

    def __init__(self, db, user_id: str, resolved, usage_id: Optional[int], job):
        self.db, self.user_id = db, user_id
        self.resolved, self.usage_id, self.job = resolved, usage_id, job
        self.provider_called = False

    def model_called(self) -> None:
        self.provider_called = True

    def __enter__(self) -> "BilledCall":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc is not None and not self.job.token_usage.counted:
                carried = getattr(exc, "usage", None)
                if carried is not None:
                    self.job.token_usage = carried
            if self.provider_called:
                user_store.record_spend(self.db, self.user_id, USAGE_KIND,
                                        self.resolved.provider, self.resolved.model,
                                        self.resolved.key_source, self.usage_id,
                                        self.job.token_usage)
            elif self.usage_id is not None:
                user_store.release_usage(self.db, self.usage_id)
        finally:
            # Always, even if settling raised: a pooled thread must not keep
            # its connection.
            self.db.release()
        return False
