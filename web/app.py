"""
Purpose: The FastAPI application factory.
Spec:    docs/implementation_plan_2026-09-15.md#2.1, #2.2, W5.a, W7.a
Tests:   tests/web/test_app.py

Run locally:   uvicorn web.app:app --reload
In a container: uvicorn web.app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Allow `uvicorn web.app:app` from the repository root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import routes_filters, routes_searches, routes_session, routes_summaries
from .deps import AppContext, build_context

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(ctx: AppContext = None) -> FastAPI:
    """Build the app. Tests pass a context bound to a temporary database."""
    @asynccontextmanager
    async def lifespan(inner_app: FastAPI):
        yield
        ctx_ = inner_app.state.ctx
        drained = ctx_.jobs.shutdown(wait=True)
        if drained:
            ctx_.db.close()
        else:
            # A worker still holds a connection. Closing it under them
            # segfaults the process; leaving the handles to the exiting
            # process does not.
            logger.error("Jobs did not drain — leaving database connections "
                         "to be reclaimed at process exit")

    application = FastAPI(
        lifespan=lifespan,
        title="BioRx",
        description="Search nine publication sources and summarize what you find.",
        docs_url=None,        # no interactive docs behind a shared access code
        redoc_url=None,
    )
    application.state.ctx = ctx if ctx is not None else build_context()

    application.include_router(routes_session.router)
    application.include_router(routes_filters.router)
    application.include_router(routes_searches.router)
    application.include_router(routes_summaries.router)

    @application.get("/healthz")
    def healthz():
        """Liveness plus the effective configuration. Never reports a secret —
        only whether one is present. Includes startup_warnings so the caller
        can surface "Unpaywall off, no contact email" to the user (P25)."""
        c: AppContext = application.state.ctx
        from src import crypto
        from src.llm_config import default_provider, provider_config

        provider = default_provider(c.llm_config)
        pconf = provider_config(c.llm_config, provider)
        c.get_orchestrator()
        return {
            "ok": True,
            "access_code_set": bool(c.access_code),
            "byo_keys_enabled": crypto.is_enabled(),
            "provider": provider,
            "model": pconf.model if pconf else "",
            "owner_key_set": bool(pconf.owner_key()) if pconf else False,
            "db_path": str(c.db.db_path),
            "startup_warnings": list(c.startup_warnings),
        }

    if STATIC_DIR.exists():
        application.mount("/static", StaticFiles(directory=str(STATIC_DIR)),
                          name="static")

        @application.get("/")
        def index():
            return FileResponse(str(STATIC_DIR / "index.html"))

    return application


# `app` is built on first attribute access, not at import (PEP 562).
#
# Building it at module scope would open a Database at the default path merely
# because something imported this module — which in a test means opening the
# developer's real database (learnings P28). `uvicorn web.app:app` still works:
# uvicorn imports the module and then reads the attribute, which builds it.
_app = None


def __getattr__(name):
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
