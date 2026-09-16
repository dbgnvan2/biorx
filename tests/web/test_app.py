"""
Tests for the application factory itself.

Spec: docs/implementation_plan_2026-09-15.md#2.1, W7.a
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent.parent


def test_importing_the_app_module_opens_no_database(monkeypatch):
    """
    Building the app at import time would open a Database at the default path
    merely because something imported the module — in a test, the developer's
    real database (learnings P28). `app` is therefore built lazily.
    """
    import importlib

    import web.app as web_app
    importlib.reload(web_app)
    assert web_app._app is None


def test_uvicorn_can_still_read_the_app_attribute(monkeypatch, tmp_path):
    """The lazy attribute must still serve `uvicorn web.app:app`."""
    import importlib

    monkeypatch.setenv("BIORX_DB_PATH", str(tmp_path / "lazy.db"))
    monkeypatch.setenv("ACCESS_CODE", "x")
    import web.app as web_app
    importlib.reload(web_app)
    try:
        application = web_app.app
        assert application.title == "BioRx"
        assert web_app._app is application       # built once, cached
    finally:
        if web_app._app is not None:
            web_app._app.state.ctx.jobs.shutdown()
            web_app._app.state.ctx.db.close()
            web_app._app = None


def test_healthz_reports_configuration_but_never_a_secret(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-NEVERSHOWTHIS")
    body = client.get("/healthz").json()

    assert body["ok"] is True
    assert body["access_code_set"] is True
    assert set(body) >= {"provider", "model", "owner_key_set", "byo_keys_enabled"}
    assert "NEVERSHOWTHIS" not in str(body)
    assert isinstance(body["owner_key_set"], bool)


def test_healthz_needs_no_session(client):
    assert client.get("/healthz").status_code == 200


def test_interactive_api_docs_are_not_exposed(client):
    """A shared access code is not an authentication system; don't publish a
    browsable API behind it."""
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_the_lifespan_shuts_the_job_pool_and_database_down(app):
    """Covered explicitly, because the other tests deliberately do not run it."""
    ctx = app.state.ctx
    with TestClient(app):
        pass
    assert ctx.jobs._pool._shutdown is True


def test_the_orchestrator_is_built_lazily(ctx):
    """Constructing it imports every adapter; doing that at startup would let an
    unrelated source stop the app from booting."""
    assert ctx.orchestrator is None
