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


def test_shutdown_waits_for_a_running_worker_before_the_database_closes(ctx):
    """
    Closing the database while a worker is mid-write is a segmentation fault,
    not an exception — reproduced once in five full-suite runs before the drain
    existed. Shutdown must therefore report whether it actually drained, and
    the caller must not close anything until it has.
    """
    import threading
    import time as _time

    release = threading.Event()
    touched = []

    def slow(job):
        _time.sleep(0.05)
        touched.append(ctx.db.conn.execute("SELECT 1").fetchone()[0])
        release.set()
        return "done"

    ctx.jobs.submit("search", "u1", slow)
    drained = ctx.jobs.shutdown(wait=True, timeout=5)

    assert drained is True
    assert release.is_set(), "shutdown returned before the worker finished"
    assert touched == [1]


def test_shutdown_reports_failure_when_a_worker_will_not_stop(ctx, caplog):
    """The honest answer when a job ignores cancellation — so the caller leaves
    the connections alone rather than closing them under a live thread."""
    import threading

    hold = threading.Event()
    try:
        ctx.jobs.submit("search", "u1", lambda j: hold.wait(timeout=30))
        import time as _time
        _time.sleep(0.05)
        with caplog.at_level("ERROR"):
            drained = ctx.jobs.shutdown(wait=True, timeout=0.2)
        assert drained is False
        assert any("did not drain" in r.getMessage() for r in caplog.records)
    finally:
        hold.set()


def test_the_orchestrator_is_built_lazily(ctx):
    """Constructing it imports every adapter; doing that at startup would let an
    unrelated source stop the app from booting."""
    assert ctx.orchestrator is None


def test_h_healthz_surfaces_startup_warnings_when_no_contact_email(client, monkeypatch):
    """
    /healthz builds the orchestrator and returns startup_warnings so a web caller
    can surface "Unpaywall off, no contact email" to the user (Batch H, F2/P25).

    With no contact email and Unpaywall enabled, the orchestrator warns — and that
    warning must appear in the healthz response, not be silently dropped.
    """
    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
    # Also clear any contact email the user may have set locally in
    # sources_config.yaml so the warning path fires unconditionally.
    ctx = client.app.state.ctx
    ctx.sources_config = dict(ctx.sources_config)
    ctx.sources_config.pop("contact_email", None)
    ctx.orchestrator = None  # force a rebuild with the cleared config
    body = client.get("/healthz").json()
    assert "startup_warnings" in body, "/healthz must include startup_warnings key"
    assert any("BIORX_CONTACT_EMAIL" in w for w in body["startup_warnings"]), (
        "expected at least one warning mentioning BIORX_CONTACT_EMAIL; "
        f"got: {body['startup_warnings']}"
    )


def test_h_app_js_reads_healthz_startup_warnings_on_boot():
    """
    app.js must render startup_warnings from /healthz on boot.

    Tests the source text because the JS runs in a browser; a browser test is an
    integration-only path. The assertion anchors to startup_warnings.join(...) —
    a token that exists ONLY on the notice/render line, so deleting that line
    (keeping the fetch) would fail this test (P27 mutation check).
    Comments are stripped first to avoid matching the comment that explains the call
    rather than the call itself (P19 corollary / test_frontend_wiring.py pattern).
    """
    import re
    from pathlib import Path
    src = (Path(__file__).parent.parent.parent / "web" / "static" / "app.js").read_text()
    # Strip block and line comments (mirrors _js_without_comments in test_frontend_wiring.py).
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.MULTILINE)
    assert "startup_warnings.join" in src, (
        "app.js render call (notice ... startup_warnings.join ...) was not found "
        "after comment-stripping; deleting the render line while keeping the "
        "fetch would make this test red"
    )


def test_c4_healthz_lists_full_text_finders(client, ctx, monkeypatch):
    """C4: /healthz says where summaries look for full text; Unpaywall only
    when a contact email is configured."""
    ctx.sources_config = {"contact_email": "", "full_text": {"find_by_title": False}}
    monkeypatch.delenv("BIORX_CONTACT_EMAIL", raising=False)
    body = client.get("/healthz").json()
    assert body["full_text_finders"] == ["the paper's own link", "OpenAlex", "Semantic Scholar"]
    assert body["find_by_title_default"] is False
    monkeypatch.setenv("BIORX_CONTACT_EMAIL", "me@example.org")
    assert "Unpaywall" in client.get("/healthz").json()["full_text_finders"]
