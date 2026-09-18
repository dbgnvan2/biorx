"""
Purpose: The FastAPI application factory.
Spec:    docs/implementation_plan_2026-09-15.md#2.1, #2.2, W5.a, W7.a
Tests:   tests/web/test_app.py

Run locally:   uvicorn web.app:app --reload
In a container: uvicorn web.app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import hashlib
import logging
import re
import os
import sys
from pathlib import Path

# Allow `uvicorn web.app:app` from the repository root.
sys.path.insert(0, str(Path(__file__).parent.parent))

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import (routes_discover, routes_filters, routes_references,
               routes_searches, routes_session, routes_summaries)
from .deps import AppContext, build_context

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


# Scripts and styles come only from /static; inline style="" attributes are
# used in index.html, so styles allow 'unsafe-inline' (scripts do not).
CONTENT_SECURITY_POLICY = ("default-src 'self'; script-src 'self'; "
                           "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                           "object-src 'none'; base-uri 'none'; form-action 'self'; "
                           "frame-ancestors 'none'")


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

    @application.middleware("http")
    async def _security_headers(request, call_next):
        # csdp security review 2026-09-18: the page could be framed, and the
        # PDF proxy serves other hosts' bytes from this origin.
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if response.headers.get("content-type", "").startswith("text/html"):
            # Only the page: a CSP on a PDF response can break the browser's viewer.
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response

    application.include_router(routes_session.router)
    application.include_router(routes_filters.router)
    application.include_router(routes_searches.router)
    application.include_router(routes_summaries.router)
    application.include_router(routes_references.router)
    application.include_router(routes_discover.router)

    def _codes_file_warning(c: AppContext) -> list:
        # Public route: a count only, never names or codes (they are in the log).
        if c.codes is None:
            return []
        n = len(c.codes.current_warnings())
        out = ([f"The access codes file has {n} problem(s) — see the server log."]
               if n else [])
        if c.codes.missing:
            out.append("There is no access codes file, so nobody can sign in with a "
                       "personal code — see the server log.")
        return out

    @application.get("/healthz")
    def healthz():
        """Liveness plus the effective configuration. Never reports a secret —
        only whether one is present. Includes startup_warnings so the caller
        can surface "Unpaywall off, no contact email" to the user (P25)."""
        c: AppContext = application.state.ctx
        from src import crypto
        from src.llm_config import default_provider, provider_config
        from src.accounts import pin_min_length as accounts_pin_min_length

        provider = default_provider(c.llm_config)
        pconf = provider_config(c.llm_config, provider)
        orch = c.get_orchestrator()
        enabled_sources = orch.get_enabled_sources() if orch else []
        from src.sources.orchestrator import _SOURCE_LABELS
        from src.sources.config import get_default_selected_sources
        server_defaults = set(get_default_selected_sources(c.sources_config or {}))
        sources_list = [
            {"id": sid, "label": _SOURCE_LABELS.get(sid, sid), "enabled": True,
             # D1: the server's suggestion for users who have not chosen
             # their own defaults (sources_config.yaml default_selected).
             "default_selected": sid in server_defaults}
            for sid in enabled_sources
        ]
        return {
            "ok": True,
            "access_code_set": bool(c.access_code),
            "byo_keys_enabled": crypto.is_enabled(),
            "provider": provider,
            "model": pconf.model if pconf else "",
            "owner_key_set": bool(pconf.owner_key()) if pconf else False,
            "db_path": str(c.db.db_path),
            "startup_warnings": list(c.startup_warnings) + _codes_file_warning(c),
            "codes_in_use": bool(c.codes and c.codes.entries()),
            "pin_min_length": accounts_pin_min_length(),
            "sources": sources_list,
        }

    if STATIC_DIR.exists():
        application.mount("/static", StaticFiles(directory=str(STATIC_DIR)),
                          name="static")

        @application.middleware("http")
        async def _no_stale_page(request, call_next):
            # C1: without this the browser reused its cached page and scripts
            # after an update (seen 2026-09-18). no-cache still lets it reuse
            # an unchanged file after a cheap 304 check.
            response = await call_next(request)
            if request.url.path == "/" or request.url.path.startswith("/static/"):
                response.headers["Cache-Control"] = "no-cache"
            return response

        @application.get("/")
        def index():
            return HTMLResponse(versioned_index(STATIC_DIR))

    return application


def versioned_index(static_dir: Path) -> str:
    """index.html with each local asset URL carrying a hash of the file (C2),
    so a browser that ignores no-cache still fetches a changed script."""
    html = (static_dir / "index.html").read_text()

    def stamp(match):
        name = match.group(2)
        path = static_dir / name
        if not path.is_file():
            return match.group(0)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        return f'{match.group(1)}/static/{name}?v={digest}"'

    return re.sub(r'((?:src|href)=")/static/([\w.-]+)"', stamp, html)


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
