"""
Tests for the access-code gate and cookie-bound identity.

Spec: docs/implementation_plan_2026-09-15.md#1.3, W4.a, W4.b
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from tests.web.conftest import ACCESS_CODE

# Routes that must be reachable without a session.
PUBLIC = {("/healthz", "get"), ("/api/session", "post"), ("/api/session", "delete")}


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
                       .replace("{paper_id}", "1"))
            response = client.request(method.upper(), url, json={})
            assert response.status_code == 401, (
                f"{method.upper()} {path} answered {response.status_code} "
                "without a session"
            )
            checked += 1
    assert checked >= 10, f"only {checked} protected routes found — did routing change?"


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


def test_the_access_code_is_compared_in_constant_time():
    """A non-constant-time compare leaks the code one character at a time."""
    import inspect

    from web import auth
    source = inspect.getsource(auth.check_access_code)
    tree = compile(source.strip(), "<check>", "exec", flags=0, dont_inherit=True)
    names = {n for n in tree.co_names}
    for const in tree.co_consts:
        if hasattr(const, "co_names"):
            names |= set(const.co_names)
    assert "compare_digest" in names


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
