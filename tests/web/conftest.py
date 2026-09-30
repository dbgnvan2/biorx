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
    monkeypatch.delenv("ACCESS_CODE_DAYS", raising=False)
    # Personal access codes live here for every test (account_body adds them).
    monkeypatch.setenv("ACCESS_CODES_FILE", str(tmp_path / "access_codes.yaml"))


@pytest.fixture(autouse=True)
def _abstract_recovery_is_offline_by_default():
    """Summary jobs call recover_abstract() whenever a paper has no text, and it
    reaches Europe PMC, Crossref, OpenAlex and publisher pages. Default every web
    test to "nothing found" so none of them depends on the network by accident;
    tests about recovery patch it explicitly, which takes precedence.

    Found by the N2 gate: test_a_paper_with_no_text_at_all_is_an_error made real
    lookups and raced its 5-second wait. The suite-wide guard in
    tests/conftest.py now fails any test that tries.
    """
    from unittest.mock import patch
    from src.paper_meta import AbstractRecovery
    with patch("web.routes_summaries.recover_abstract",
               return_value=AbstractRecovery(tried=["stubbed"])):
        yield


@pytest.fixture(autouse=True)
def _full_text_finders_are_offline_by_default(monkeypatch):
    """The full-text finders (Unpaywall, OpenAlex, Semantic Scholar) answer "not
    found" in every web test unless a test sets its own, so none reaches the
    network (plan 2026-09-19 C2)."""
    monkeypatch.setattr("web.routes_summaries._FINDER_GET_JSON", lambda url, params: None)


@pytest.fixture
def ctx(tmp_path):
    from web.deps import build_context
    context = build_context(
        db_path=str(tmp_path / "app.db"),
        session_secret="a-test-session-secret-long-enough",
        cookie_secure=False,          # the test client speaks plain HTTP
    )
    yield context
    # Drain before closing: a worker mid-write holds a connection, and closing
    # it from another thread is a segfault, not an exception.
    drained = context.jobs.shutdown(wait=True)
    if drained:
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


TEST_PIN = "test-pin-123"


def add_code(for_name: str = None, account: str = "") -> str:
    """Add a personal access code to this test's codes file and return it."""
    import uuid
    from src.access_codes import add_entry
    return add_entry(os.environ["ACCESS_CODES_FILE"],
                     for_name or f"user-{uuid.uuid4().hex[:10]}", account=account)


def account_body(name: str = None, pin: str = TEST_PIN) -> dict:
    """A /api/session body: a fresh personal access code for `name` + a PIN —
    its first use creates the account
    (docs/implementation_plan_2026-09-18_invite_codes.md#PC3)."""
    return {"code": add_code(name), "pin": pin}


@pytest.fixture
def signed_in(client):
    """A client signed in with a personal access code + PIN."""
    resp = client.post("/api/session", json=account_body(name="Tester"))
    assert resp.status_code == 200, resp.text
    return client


@pytest.fixture
def enc_secret(monkeypatch):
    monkeypatch.setenv("KEY_ENC_SECRET", "a-long-enough-encryption-secret")


@pytest.fixture(autouse=True)
def papers_at_source(monkeypatch):
    """A stand-in for the publication sources the summary route looks papers up in.

    Since review A1 the route takes only a paper's DOI / canonical_id from the
    request and looks the paper up itself. Most summary tests post a paper they
    made up and need the route to find it, so by default ("echo") the stand-in
    knows every paper a test posts to /api/summaries — i.e. the posted paper is
    treated as the real one at its source. Tests of A1 itself set
    `papers_at_source["echo"] = False` and register what the source really has
    in `papers_at_source["papers"]` (keyed by DOI or canonical_id).
    """
    from fastapi.testclient import TestClient
    known = {"echo": True, "papers": {}, "fail": None}

    def fake_lookup(ref, _cfg=None):
        if known["fail"] is not None:
            raise known["fail"]
        for key in (ref.get("doi"), ref.get("canonical_id")):
            if key and key in known["papers"]:
                return dict(known["papers"][key])
        return None

    monkeypatch.setattr("web.routes_summaries._PAPER_LOOKUP", fake_lookup)
    real_post = TestClient.post

    def post(self, url, *args, **kwargs):
        body = kwargs.get("json")
        if known["echo"] and url == "/api/summaries" and isinstance(body, dict):
            paper = body.get("paper") or {}
            for key in (paper.get("doi"), paper.get("canonical_id")):
                if key:
                    known["papers"][key] = dict(paper)
        return real_post(self, url, *args, **kwargs)

    monkeypatch.setattr(TestClient, "post", post)
    return known


def settle_jobs(ctx, timeout: float = 10.0) -> None:
    """Wait until no background job in ctx is queued or running.

    Call it before leaving a `with patch(...)` block (or a stub fixture) that
    a job depends on. A test that returns while its job still runs lets the
    job carry on after the stub is gone, and it then reaches the real network
    — intermittently, by timing, which is how CI failed on and off from
    review batch 2 to batch 7 (a real PDF download after `no_pdf` was undone).
    """
    import time
    from src.jobs import TERMINAL
    deadline = time.time() + timeout
    while time.time() < deadline:
        with ctx.jobs._lock:
            busy = [j for j in ctx.jobs._jobs.values() if j.status not in TERMINAL]
        if not busy:
            return
        time.sleep(0.01)
    raise AssertionError(f"{len(busy)} job(s) still running after {timeout}s")


def legacy_account(db, name: str, pin: str = TEST_PIN, _new_user_id=None) -> tuple:
    """An account made before personal codes: a login name + PIN, no code.

    The name sign-in that made these was removed (decision D2, 2026-09-30),
    but such rows still exist and an access_codes.yaml `account:` entry can
    still name one, so tests set them up directly. Returns (user_id, "") so
    it reads like the old create_account(...) it replaces.
    """
    from src import user_store
    from src.accounts import hash_secret
    user_id = (_new_user_id or user_store.new_user_id)()
    clean = " ".join(name.split())
    db.conn.execute("INSERT INTO users (user_id, display_name, login_name, pin_hash) "
                    "VALUES (?, ?, ?, ?)", (user_id, clean, clean, hash_secret(pin)))
    db.conn.commit()
    return user_id, ""
