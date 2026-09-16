"""
Shared fixtures for the web tests.

Every fixture binds the app to a temporary database. No test may construct a
production object with default arguments (learnings P28), and a session-scoped
guard fails the run if the real database file is touched.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

REAL_DB = Path("~/preprints/biorxiv.db").expanduser()


@pytest.fixture(scope="session", autouse=True)
def _real_database_is_never_touched():
    """Fingerprint the real database and fail the run if a test wrote to it.

    A guard that patches a class can be defeated by a second import spelling;
    checking the artifact itself catches that (learnings P28/P6).
    """
    def fingerprint():
        if not REAL_DB.exists():
            return None
        st = REAL_DB.stat()
        return (st.st_size, st.st_mtime_ns)

    before = fingerprint()
    yield
    after = fingerprint()
    assert before == after, (
        f"a test modified the real database at {REAL_DB} "
        f"({before} -> {after})"
    )


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """No test inherits the developer's provider keys or paths."""
    for var in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "LLM_PROVIDER",
                "ANTHROPIC_MODEL", "DEEPSEEK_MODEL", "KEY_ENC_SECRET",
                "SUMMARY_DAILY_CAP_PER_USER", "SESSION_SECRET", "ACCESS_CODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("BIORX_DB_PATH", str(tmp_path / "web-test.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


ACCESS_CODE = "shared-code-for-tests"


@pytest.fixture
def ctx(tmp_path):
    from web.deps import build_context
    context = build_context(
        db_path=str(tmp_path / "app.db"),
        access_code=ACCESS_CODE,
        session_secret="a-test-session-secret-long-enough",
        cookie_secure=False,          # the test client speaks plain HTTP
    )
    yield context
    context.jobs.shutdown()
    context.db.close()


@pytest.fixture
def app(ctx):
    from web.app import create_app
    return create_app(ctx)


@pytest.fixture
def client(app):
    """A client that does NOT run the app lifespan.

    Exiting a `with TestClient(app)` block runs the shutdown handler, which
    shuts the job pool down permanently — so a second client over the same app
    could no longer submit a job. Teardown belongs to the ctx fixture; the
    lifespan itself is covered by its own test in test_app.py.
    """
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def other_client(app):
    """A second signed-in identity over the same app, for isolation tests."""
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def signed_in(client):
    """A client that has already exchanged the access code for a cookie."""
    resp = client.post("/api/session",
                       json={"access_code": ACCESS_CODE, "display_name": "Tester"})
    assert resp.status_code == 200, resp.text
    return client


@pytest.fixture
def enc_secret(monkeypatch):
    monkeypatch.setenv("KEY_ENC_SECRET", "a-long-enough-encryption-secret")
