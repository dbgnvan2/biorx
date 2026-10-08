"""
Limits on sign-in attempts: per client address, and on password hashing.

Purpose: Keep unauthenticated sign-in requests from exhausting the server.
Spec:    docs/implementation_plan_2026-09-28_review_fixes.md#M4
Tests:   tests/web/test_auth.py::test_m4_sign_in_rate_limited,
         tests/web/test_auth.py::test_m4_scrypt_concurrency_bounded

Every sign-in attempt, right or wrong, runs scrypt on purpose (~16 MB, tens of
ms), and the routes run in a ~40-thread pool, so a flood of made-up codes could
use up memory and threads with no credentials at all. Two limits:
- attempts per client address per minute (a token bucket), and
- how many sign-ins may be hashing at once; beyond it the answer is 503.
Settings: llm_config.yaml `sign_in:`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional, Tuple

DEFAULT_PER_MINUTE = 20
DEFAULT_CONCURRENT = 4
logger = logging.getLogger(__name__)

MAX_TRACKED = 10_000        # addresses remembered; the oldest are dropped


class SignInLimiter:
    def __init__(self, per_minute: int = DEFAULT_PER_MINUTE,
                 concurrent: int = DEFAULT_CONCURRENT, clock=time.monotonic):
        self.per_minute = max(1, int(per_minute))
        self._clock = clock
        self._buckets: Dict[str, Tuple[float, float]] = {}   # addr -> (tokens, last)
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max(1, int(concurrent)))

    def allow(self, address: str) -> bool:
        """Take one attempt from this address's allowance, if it has one."""
        now = self._clock()
        rate = self.per_minute / 60.0
        with self._lock:
            tokens, last = self._buckets.get(address, (float(self.per_minute), now))
            tokens = min(float(self.per_minute), tokens + (now - last) * rate)
            if tokens < 1:
                self._buckets[address] = (tokens, now)
                return False
            self._buckets[address] = (tokens - 1, now)
            if len(self._buckets) > MAX_TRACKED:
                oldest = min(self._buckets, key=lambda a: self._buckets[a][1])
                del self._buckets[oldest]
        return True

    def try_slot(self) -> bool:
        return self._slots.acquire(blocking=False)

    def release_slot(self) -> None:
        self._slots.release()


def _setting(section: dict, name: str, default: int) -> int:
    """A whole number from `sign_in:`, or the default with a warning — a typo
    in the config must not stop the server starting (plan 2026-09-29 T1.5c)."""
    from src.config_values import config_int
    # The shared reader (TG6): inf, a fraction or a bool falls back too.
    return config_int(section, name, default, minimum=1, name=f"sign_in.{name}")


def from_config(config: Optional[dict]) -> SignInLimiter:
    section = (config or {}).get("sign_in") or {}
    if not isinstance(section, dict):
        logger.warning("llm_config.yaml sign_in: is not a section; using the defaults")
        section = {}
    return SignInLimiter(per_minute=_setting(section, "attempts_per_minute", DEFAULT_PER_MINUTE),
                         concurrent=_setting(section, "max_concurrent", DEFAULT_CONCURRENT))


def client_address(request, trust_proxy: bool) -> str:
    """The client's address. Behind a proxy (Railway), the first address in
    X-Forwarded-For — only when TRUST_PROXY=1, or any client could claim
    any address and never be limited."""
    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"
