"""
Purpose: Process-wide singletons and settings for the web app.
Spec:    docs/implementation_plan_2026-09-15.md#2.1
Tests:   tests/web/test_app.py

Built once at startup and handed to routes through FastAPI dependencies, so a
test can construct an app against a temporary database without touching the
real one.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.access_codes import CodeStore, codes_file_path
from src.db import Database
from src.jobs import JobRegistry
from src.llm_config import load_llm_config
from src.sources.config import load_sources_config

logger = logging.getLogger(__name__)

SESSION_COOKIE = "biorx_session"

# Values shipped in .env.example. Someone who deploys without editing them has
# not chosen a code; treat that as "no code set" rather than as a live
# credential anyone can read off GitHub.
PLACEHOLDER_ACCESS_CODES = {"change-me", "changeme", "your-access-code", "secret"}
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30      # 30 days


@dataclass
class AppContext:
    """Everything a route needs that outlives a single request."""

    db: Database
    jobs: JobRegistry
    llm_config: Dict[str, Any]
    sources_config: Dict[str, Any]
    access_code: str
    session_secret: str
    # Session cookies are Secure (HTTPS-only) by default. A local run over
    # http://localhost, and the test client, set this false explicitly rather
    # than production code branching on a magic value.
    cookie_secure: bool = True
    # Personal access codes (docs/implementation_plan_2026-09-18_invite_codes.md).
    codes: Optional[CodeStore] = None
    orchestrator: Any = None
    startup_warnings: list = field(default_factory=list)

    def get_orchestrator(self):
        """Build the SourceOrchestrator lazily.

        Constructing it imports every adapter; doing that at import time would
        make the app fail to start because of an unrelated source.
        Stores orchestrator.warnings on startup_warnings so routes can surface them.
        """
        if self.orchestrator is None:
            from src.sources.orchestrator import SourceOrchestrator
            self.orchestrator = SourceOrchestrator(self.sources_config)
            from src.llm_config import owner_key_problems
            self.startup_warnings = (list(self.orchestrator.warnings)
                                     + owner_key_problems(self.llm_config))
            for w in self.startup_warnings:
                logger.warning("Startup: %s", w)
        return self.orchestrator


def build_context(db_path: Optional[str] = None,
                  access_code: Optional[str] = None,
                  session_secret: Optional[str] = None,
                  cookie_secure: Optional[bool] = None,
                  access_codes_file: Optional[str] = None) -> AppContext:
    """Assemble the app context from the environment, with test overrides."""
    import secrets

    code = access_code if access_code is not None else os.environ.get("ACCESS_CODE", "")
    if code.strip().lower() in PLACEHOLDER_ACCESS_CODES:
        logger.error(
            "ACCESS_CODE is still the placeholder from .env.example — refusing "
            "every request. Set it to a code of your own."
        )
        code = ""
    codes = CodeStore(access_codes_file or codes_file_path())
    if not code and not codes.entries():
        logger.warning(
            "No ACCESS_CODE and no personal access codes in %s — nobody can sign "
            "in. Add codes with: python -m src.access_codes add --for NAME", codes.path)
    elif code:
        logger.info("Shared ACCESS_CODE is set: accounts from before personal codes "
                    "can still sign in by name. Remove it once everyone has a code.")

    secret = session_secret or os.environ.get("SESSION_SECRET", "")
    if not secret:
        # A per-process secret keeps cookies signed without requiring another
        # variable; the cost is that sessions end at a restart, which is
        # acceptable for a handful of users and is documented in the README.
        secret = secrets.token_urlsafe(32)
        logger.info("SESSION_SECRET not set — using a per-process secret; "
                    "sessions will not survive a restart")

    if cookie_secure is None:
        # Opt out explicitly for a plain-HTTP local run; secure by default so a
        # deployment cannot lose the flag by omission.
        cookie_secure = os.environ.get("SESSION_COOKIE_INSECURE", "") != "1"

    llm_config = load_llm_config()
    from src.llm_config import default_provider, default_provider_source
    logger.info("Default LLM provider: %s (from %s)",
                default_provider(llm_config), default_provider_source(llm_config))

    return AppContext(
        db=Database(db_path) if db_path else Database(),
        jobs=JobRegistry(),
        llm_config=llm_config,
        sources_config=load_sources_config(),
        access_code=code,
        session_secret=secret,
        cookie_secure=cookie_secure,
        codes=codes,
    )
