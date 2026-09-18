"""
Tests for the access-code gate and cookie-bound identity.

Spec: docs/implementation_plan_2026-09-15.md#1.3, W4.a, W4.b
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from tests.web.conftest import ACCESS_CODE

# Routes that must be reachable without a session, each with its reason. A
# route added to this set is a deliberate decision, not an oversight.
PUBLIC = {
    ("/healthz", "get"),          # liveness, for the platform
    ("/api/session", "post"),     # the sign-in itself
    ("/api/session", "delete"),   # signing out must work from a stale session
    ("/", "get"),                 # the page shell, so a visitor sees the form
}

# The exact number of authenticated operations. An exact count, not a floor:
# a floor stays satisfied while the route table halves (learnings P29). Update
# this deliberately when a route is added or removed.
PROTECTED_ROUTE_COUNT = 29


def test_wrong_access_code_is_rejected(client):
    r = client.post("/api/session", json={"access_code": "not-the-code"})
    assert r.status_code == 401
    assert "biorx_session" not in client.cookies


def test_an_empty_access_code_is_rejected(client):
    assert client.post("/api/session", json={"access_code": ""}).status_code == 422


def test_the_right_access_code_issues_a_session(client):
    r = client.post("/api/session",
                    json={"access_code": ACCESS_CODE, "display_name": "Dave"})
    assert r.status_code == 200
    assert client.cookies.get("biorx_session")
    assert r.json()["display_name"] == "Dave"
    assert r.json()["user_id"]


def test_no_route_can_be_reached_without_a_session(app, client):
    """
    Enumerates the app's own route table rather than a hand-kept list, so a new
    route added without auth fails here (security S3). An exact enumeration,
    not a floor (learnings P29).
    """
    paths = app.openapi()["paths"]
    checked = 0
    for path, methods in paths.items():
        for method in methods:
            if (path, method) in PUBLIC:
                continue
            url = (path.replace("{filter_id}", "1").replace("{job_id}", "abc")
                       .replace("{paper_id}", "1").replace("{list_id}", "1")
                       .replace("{item_id}", "1").replace("{filename}", "sources_config.yaml"))
            response = client.request(method.upper(), url, json={})
            assert response.status_code == 401, (
                f"{method.upper()} {path} answered {response.status_code} "
                "without a session"
            )
            checked += 1
    assert checked == PROTECTED_ROUTE_COUNT, (
        f"{checked} protected operations found, expected {PROTECTED_ROUTE_COUNT}. "
        "If a route was added or removed, update PROTECTED_ROUTE_COUNT — and if "
        "it was added, confirm it is meant to require a session."
    )


def test_every_public_route_exists(app):
    """The exemption list must not name a route that no longer exists."""
    paths = app.openapi()["paths"]
    for path, method in PUBLIC:
        if path == "/healthz":
            assert path in paths
            continue
        assert method in paths[path], f"{method} {path} is exempted but absent"


def test_cookie_is_signed_and_tamper_evident(client, signed_in):
    original = client.cookies.get("biorx_session")
    tampered = original[:-4] + ("aaaa" if not original.endswith("aaaa") else "bbbb")
    client.cookies.set("biorx_session", tampered)
    assert client.get("/api/me").status_code == 401


def test_a_cookie_signed_with_another_secret_is_refused(ctx, client, signed_in):
    from itsdangerous import URLSafeTimedSerializer
    forged = URLSafeTimedSerializer("a-different-secret", salt="biorx-session-v1")
    client.cookies.set("biorx_session", forged.dumps("some-user-id"))
    assert client.get("/api/me").status_code == 401


def test_a_valid_cookie_for_a_deleted_user_is_refused(ctx, client, signed_in):
    user_id = client.get("/api/me").json()["user_id"]
    ctx.db.conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    ctx.db.conn.commit()
    assert client.get("/api/me").status_code == 401


def test_the_cookie_is_httponly_and_samesite(client):
    r = client.post("/api/session", json={"access_code": ACCESS_CODE})
    header = r.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header


def test_the_cookie_is_secure_when_configured(tmp_path):
    """Secure is the default; only an explicit opt-out turns it off."""
    from fastapi.testclient import TestClient
    from web.app import create_app
    from web.deps import build_context

    secure_ctx = build_context(db_path=str(tmp_path / "s.db"),
                               access_code=ACCESS_CODE,
                               session_secret="x" * 32)
    try:
        with TestClient(create_app(secure_ctx)) as c:
            r = c.post("/api/session", json={"access_code": ACCESS_CODE})
            assert "secure" in r.headers["set-cookie"].lower()
    finally:
        secure_ctx.jobs.shutdown(); secure_ctx.db.close()


def test_logging_out_clears_the_session(client, signed_in):
    assert client.get("/api/me").status_code == 200
    client.delete("/api/session")
    assert client.get("/api/me").status_code == 401


# ── Identity is server-issued; the display name has no authority (§1.3) ───────

def test_display_name_change_does_not_change_user_id(client, signed_in):
    before = client.get("/api/me").json()["user_id"]
    client.patch("/api/me", json={"display_name": "Someone Else"})
    assert client.get("/api/me").json()["user_id"] == before


def test_typing_another_users_display_name_does_not_reach_their_key(
    ctx, app, enc_secret
):
    """
    The attack the opaque id exists to prevent: with a shared access code, a
    second person types the first person's name and would otherwise inherit
    their stored API key.
    """
    from fastapi.testclient import TestClient

    alice = TestClient(app)
    if True:
        alice.post("/api/session",
                   json={"access_code": ACCESS_CODE, "display_name": "Alice"})
        alice.put("/api/me/llm-key",
                  json={"provider": "anthropic", "api_key": "sk-ant-ALICEKEY9999"})
        alice_me = alice.get("/api/me").json()
        assert alice_me["key_source"] == "user"

    impostor = TestClient(app)
    if True:
        impostor.post("/api/session",
                      json={"access_code": ACCESS_CODE, "display_name": "Alice"})
        me = impostor.get("/api/me").json()

    assert me["user_id"] != alice_me["user_id"]
    assert me["key_source"] != "user"
    assert me["key_last4"] == ""



def test_an_unset_access_code_refuses_everyone(tmp_path):
    from fastapi.testclient import TestClient
    from web.app import create_app
    from web.deps import build_context

    empty = build_context(db_path=str(tmp_path / "e.db"), access_code="",
                          session_secret="y" * 32, cookie_secure=False)
    try:
        with TestClient(create_app(empty)) as c:
            assert c.post("/api/session", json={"access_code": ""}).status_code == 422
            assert c.post("/api/session", json={"access_code": "anything"}).status_code == 401
    finally:
        empty.jobs.shutdown(); empty.db.close()


# ── The cookie check stands on its own, not only on the user lookup ───────────

@pytest.mark.parametrize("token", [None, "", "garbage", "a.b.c"])
def test_read_session_rejects_a_missing_or_malformed_cookie(ctx, token):
    """
    Unit-level, so the guard is covered independently of the user-row lookup
    that would also reject an unknown id. Defence in depth needs both halves
    tested, or removing one looks harmless.
    """
    from web.auth import read_session
    assert read_session(ctx, token) is None


def test_read_session_accepts_a_cookie_it_issued(ctx):
    from itsdangerous import URLSafeTimedSerializer
    from web.auth import read_session
    token = URLSafeTimedSerializer(ctx.session_secret, salt="biorx-session-v1").dumps("u-1")
    assert read_session(ctx, token) == "u-1"


def test_an_expired_cookie_is_refused(ctx, monkeypatch):
    from itsdangerous import URLSafeTimedSerializer
    from web import auth
    token = URLSafeTimedSerializer(ctx.session_secret, salt="biorx-session-v1").dumps("u-1")
    monkeypatch.setattr(auth, "SESSION_MAX_AGE_SECONDS", -1)
    assert auth.read_session(ctx, token) is None


def test_the_placeholder_access_code_is_not_a_live_credential(tmp_path, monkeypatch):
    """
    `.env.example` ships ACCESS_CODE=change-me. Someone who deploys without
    editing it has not chosen a code — and that code is readable on GitHub.
    Treat it as unset rather than as a credential.
    """
    from fastapi.testclient import TestClient
    from web.app import create_app
    from web.deps import PLACEHOLDER_ACCESS_CODES, build_context

    for placeholder in sorted(PLACEHOLDER_ACCESS_CODES):
        ctx_ = build_context(db_path=str(tmp_path / f"{placeholder}.db"),
                             access_code=placeholder,
                             session_secret="z" * 32, cookie_secure=False)
        try:
            client = TestClient(create_app(ctx_))
            assert client.post("/api/session",
                               json={"access_code": placeholder}).status_code == 401
        finally:
            ctx_.jobs.shutdown(); ctx_.db.close()


def test_a_real_access_code_that_merely_resembles_one_still_works(tmp_path):
    from fastapi.testclient import TestClient
    from web.app import create_app
    from web.deps import build_context

    code = "change-me-for-real-2026"          # not the placeholder itself
    ctx_ = build_context(db_path=str(tmp_path / "real.db"), access_code=code,
                         session_secret="z" * 32, cookie_secure=False)
    try:
        client = TestClient(create_app(ctx_))
        assert client.post("/api/session", json={"access_code": code}).status_code == 200
    finally:
        ctx_.jobs.shutdown(); ctx_.db.close()
