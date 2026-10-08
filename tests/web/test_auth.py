"""
Tests for the access-code gate and cookie-bound identity.

Spec: docs/implementation_plan_2026-09-15.md#1.3, W4.a, W4.b
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from tests.web.conftest import TEST_PIN, account_body

# Routes that must be reachable without a session, each with its reason. A
# route added to this set is a deliberate decision, not an oversight.
PUBLIC = {
    ("/healthz", "get"),          # liveness, for the platform
    ("/api/gate", "get"),         # what the sign-in page needs (review M7)
    ("/api/session", "post"),     # the sign-in itself
    ("/api/session/lookup", "post"),   # "Welcome back, NAME" before sign-in (PC14/PC15)
    ("/api/session", "delete"),   # signing out must work from a stale session
    ("/", "get"),                 # the page shell, so a visitor sees the form
}

# The exact number of authenticated operations. An exact count, not a floor:
# a floor stays satisfied while the route table halves (learnings P29). Update
# this deliberately when a route is added or removed.
PROTECTED_ROUTE_COUNT = 42   # + GET /api/config (review M7): moved off /healthz   # + GET /api/jobs/running (review A7): one user's
                             # own jobs
                             # + GET /api/vocabulary (review S3): served to
                             # the signed-in page with the filter editor
                             # + the four review routes (M4): POST /api/reviews,
                             # GET /api/reviews/{job}, and the stored-review and
                             # preview routes — all read or spend on one user's
                             # own list, so all need a session
                             # + GET /api/usage/estimate (M5): it reads this
                             # user's cap allowance, so it needs a session
                             # + GET /api/usage/session (M1.C.1): it reports
                             # one user's spending, so it needs a session
                             # + GET /api/references/{id}/save (M6): one user's
                             # own list, so it needs a session too


def test_wrong_access_code_is_rejected(client):
    r = client.post("/api/session", json={"code": "ZZZZ-ZZZZ-ZZZZ", "pin": "whatever-1"})
    assert r.status_code == 401
    assert "biorx_session" not in client.cookies


def test_an_empty_request_is_rejected(client):
    assert client.post("/api/session", json={"code": ""}).status_code == 400
    assert client.post("/api/session", json={}).status_code == 400
    assert "biorx_session" not in client.cookies


def test_d2_old_name_sign_in_is_gone(ctx, client):
    """Decision D2 (2026-09-30): the shared access code + name + PIN sign-in
    and its recovery route are removed. An old account with a login name and
    a PIN cannot get in with them; the fields are ignored, not honoured."""
    from tests.web.conftest import legacy_account
    legacy_account(ctx.db, "Oldtimer", "old-pin-111")
    r = client.post("/api/session", json={"access_code": "anything", "name": "Oldtimer",
                                           "pin": "old-pin-111"})
    assert r.status_code == 400 and "access code" in r.json()["detail"]
    assert "biorx_session" not in client.cookies
    r = client.post("/api/session/recover", json={"access_code": "x", "name": "Oldtimer",
                                                   "recovery_code": "AAAA", "new_pin": "n-pin-111"})
    assert r.status_code in (404, 405)
    assert "access_code_set" not in client.get("/api/gate").json()
    from src import accounts
    for gone in ("sign_in", "create_account", "recover", "new_recovery_code"):
        assert not hasattr(accounts, gone), gone
    from web import deps
    assert not hasattr(deps, "PLACEHOLDER_ACCESS_CODES")


def test_the_right_access_code_issues_a_session(client):
    r = client.post("/api/session", json=account_body(name="Dave"))
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
    # Foreign keys are on (review M30): the rows that point at the user go first.
    for table in ("user_filters", "user_reference_lists"):
        ctx.db.conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
    ctx.db.conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    ctx.db.conn.commit()
    assert client.get("/api/me").status_code == 401


def test_the_cookie_is_httponly_and_samesite(client):
    r = client.post("/api/session", json=account_body())
    header = r.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header


def test_the_cookie_is_secure_when_configured(tmp_path):
    """Secure is the default; only an explicit opt-out turns it off."""
    from fastapi.testclient import TestClient
    from web.app import create_app
    from web.deps import build_context

    secure_ctx = build_context(db_path=str(tmp_path / "s.db"),
                               session_secret="x" * 32)
    try:
        with TestClient(create_app(secure_ctx)) as c:
            r = c.post("/api/session", json=account_body())
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


def test_typing_another_users_name_or_code_without_the_pin_does_not_reach_their_key(
    ctx, app, enc_secret
):
    """
    The attack the PIN exists to prevent. Alice's code is not secret (it sits in
    a readable file), so a second person may know it — without her PIN they get
    neither her account nor her stored key. Typing her name does not work
    either: there is no name sign-in (decision D2).
    """
    from fastapi.testclient import TestClient

    alice = TestClient(app)
    body = account_body(name="Alice", pin="alice-pin-1")
    assert alice.post("/api/session", json=body).status_code == 200
    alice.put("/api/me/llm-key",
              json={"provider": "anthropic", "api_key": "sk-ant-ALICEKEY9999"})
    assert alice.get("/api/me").json()["key_source"] == "user"

    impostor = TestClient(app)
    r = impostor.post("/api/session", json={"code": body["code"], "pin": "guess-123"})
    assert r.status_code == 401
    r = impostor.post("/api/session", json={"name": "Alice", "pin": "guess-123"})
    assert r.status_code == 400
    assert impostor.get("/api/me").status_code == 401
    assert "biorx_session" not in impostor.cookies


def test_no_codes_file_refuses_everyone(tmp_path):
    from fastapi.testclient import TestClient
    from web.app import create_app
    from web.deps import build_context

    empty = build_context(db_path=str(tmp_path / "e.db"),
                          session_secret="y" * 32, cookie_secure=False,
                          access_codes_file=str(tmp_path / "none.yaml"))
    try:
        with TestClient(create_app(empty)) as c:
            assert c.post("/api/session", json={"code": ""}).status_code == 400
            assert c.post("/api/session", json={"code": "ABCD-EFGH-JKLM",
                                                "pin": "whatever-1"}).status_code == 401
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


# ── csdp security review 2026-09-18 ───────────────────────────────────────────

def test_security_headers(client, signed_in):
    page = client.get("/")
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["x-content-type-options"] == "nosniff"
    csp = page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "script-src 'self'" in csp
    api = client.get("/api/me")
    assert api.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in api.headers      # JSON/PDF: no CSP


def test_a_broken_merge_chain_is_refused_not_treated_as_the_old_account(ctx, client):
    from fastapi import Response
    from src import user_store
    from web.auth import issue_session
    a = user_store.create_user(ctx.db, "a")
    b = user_store.create_user(ctx.db, "b")
    ctx.db.conn.execute("UPDATE users SET merged_into = ? WHERE user_id = ?", (b, a))
    ctx.db.conn.execute("UPDATE users SET merged_into = ? WHERE user_id = ?", (a, b))
    ctx.db.conn.commit()
    resp = Response()
    issue_session(resp, ctx, a, secure=False)
    client.cookies.set("biorx_session", resp.headers["set-cookie"].split(";")[0].split("=", 1)[1])
    assert client.get("/api/me").status_code == 401


def test_a_cookie_from_before_session_nonces_still_works(ctx, client):
    from itsdangerous import URLSafeTimedSerializer
    from src import user_store
    from src.access_codes import code_key
    from tests.web.conftest import add_code
    uid = user_store.create_user(ctx.db, "old cookie")
    # Every account has a code now (decision D2); an account without one is
    # refused whatever its cookie says.
    ctx.db.conn.execute("INSERT INTO access_code_bindings (code_key, user_id) VALUES (?, ?)",
                        (code_key(add_code("old cookie")), uid))
    ctx.db.conn.commit()
    token = URLSafeTimedSerializer(ctx.session_secret, salt="biorx-session-v1").dumps(uid)
    client.cookies.set("biorx_session", token)
    assert client.get("/api/me").status_code == 200


# ── M4: sign-in attempts are limited ─────────────────────────────────────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#M4

def test_m4_sign_in_rate_limited(ctx, app, client, monkeypatch):
    from src.sign_in_limits import SignInLimiter
    ctx.sign_in_limiter = SignInLimiter(per_minute=10, concurrent=4)
    codes = [client.post("/api/session/lookup", json={"code": "ZZZZ-ZZZZ-ZZZZ"}).status_code
             for _ in range(11)]
    assert codes[:10] == [401] * 10          # wrong code, answered normally
    assert codes[10] == 429
    # One budget across the three routes.
    assert client.post("/api/session", json={"code": "ZZZZ-ZZZZ-ZZZZ", "pin": "x"}).status_code == 429


def test_m4_limit_is_per_address(ctx, monkeypatch):
    from src.sign_in_limits import SignInLimiter, client_address
    lim = SignInLimiter(per_minute=1, concurrent=1)
    assert lim.allow("1.1.1.1") and not lim.allow("1.1.1.1") and lim.allow("2.2.2.2")

    class Req:
        headers = {"x-forwarded-for": "9.9.9.9, 10.0.0.1"}
        client = type("C", (), {"host": "10.0.0.1"})()
    assert client_address(Req(), trust_proxy=True) == "9.9.9.9"
    assert client_address(Req(), trust_proxy=False) == "10.0.0.1"   # header not trusted


def test_m4_allowance_refills():
    from src.sign_in_limits import SignInLimiter
    now = [0.0]
    lim = SignInLimiter(per_minute=60, concurrent=1, clock=lambda: now[0])
    for _ in range(60):
        assert lim.allow("a")
    assert not lim.allow("a")
    now[0] += 1.0                      # one second: one attempt back
    assert lim.allow("a") and not lim.allow("a")


def test_m4_scrypt_concurrency_bounded(ctx, client):
    from unittest.mock import patch
    from src.sign_in_limits import SignInLimiter
    ctx.sign_in_limiter = SignInLimiter(per_minute=100, concurrent=1)
    assert ctx.sign_in_limiter.try_slot()          # another sign-in is hashing
    with patch("src.accounts.verify_secret") as hashed:
        r = client.post("/api/session", json={"code": "ZZZZ-ZZZZ-ZZZZ", "pin": "x"})
    assert r.status_code == 503 and "busy" in r.json()["detail"]
    hashed.assert_not_called()
    ctx.sign_in_limiter.release_slot()
    assert client.post("/api/session", json={"code": "ZZZZ-ZZZZ-ZZZZ", "pin": "x"}).status_code == 401


def test_t15a_a_busy_answer_does_not_use_an_attempt(ctx, client):
    """Plan 2026-09-29 T1.5a: the attempt was spent before the hashing slot
    was taken, so 503s used up an address's allowance."""
    from src.sign_in_limits import SignInLimiter
    ctx.sign_in_limiter = SignInLimiter(per_minute=2, concurrent=1)
    assert ctx.sign_in_limiter.try_slot()                  # the server is busy
    for _ in range(5):
        assert client.post("/api/session/lookup",
                           json={"code": "ZZZZ-ZZZZ-ZZZZ"}).status_code == 503
    ctx.sign_in_limiter.release_slot()
    codes = [client.post("/api/session/lookup", json={"code": "ZZZZ-ZZZZ-ZZZZ"}).status_code
             for _ in range(3)]
    assert codes == [401, 401, 429]                        # both attempts were still there


def test_t15a_a_limited_request_gives_its_slot_back(ctx, client):
    from src.sign_in_limits import SignInLimiter
    ctx.sign_in_limiter = SignInLimiter(per_minute=1, concurrent=1)
    assert client.post("/api/session/lookup", json={"code": "ZZZZ-ZZZZ-ZZZZ"}).status_code == 401
    assert client.post("/api/session/lookup", json={"code": "ZZZZ-ZZZZ-ZZZZ"}).status_code == 429
    assert ctx.sign_in_limiter.try_slot()                  # not left held by the 429
    ctx.sign_in_limiter.release_slot()


@pytest.mark.parametrize("section,per_minute,concurrent", [
    ({"attempts_per_minute": "lots", "max_concurrent": 3}, 20, 3),
    ({"attempts_per_minute": 5, "max_concurrent": None}, 5, 4),
    ({"attempts_per_minute": 0, "max_concurrent": -2}, 20, 4),
    ({"attempts_per_minute": float("inf"), "max_concurrent": float("inf")}, 20, 4),
    ({"attempts_per_minute": 2.5, "max_concurrent": True}, 20, 4),
    ("not a section", 20, 4),
])
def test_t15c_bad_sign_in_settings_fall_back(section, per_minute, concurrent, caplog):
    """Plan 2026-09-29 T1.5c: int() on a typo stopped the server starting."""
    from src.sign_in_limits import from_config
    lim = from_config({"sign_in": section})
    assert lim.per_minute == per_minute
    taken = 0
    while lim.try_slot():
        taken += 1
    assert taken == concurrent
    assert "sign_in" in caplog.text


# ── M6: signing out ends the session on the server ───────────────────────────
# Spec: docs/implementation_plan_2026-09-28_review_fixes.md#M6

def test_m6_sign_out_invalidates_copies(app):
    from fastapi.testclient import TestClient
    body = account_body()
    a = TestClient(app)
    assert a.post("/api/session", json=body).status_code == 200
    copied = a.cookies.get("biorx_session")
    thief = TestClient(app)
    thief.cookies.set("biorx_session", copied)
    assert thief.get("/api/me").status_code == 200
    assert a.delete("/api/session").status_code == 200
    assert thief.get("/api/me").status_code == 401        # the copy is dead too


def test_m6_an_ended_cookie_cannot_sign_the_user_out(app):
    """Adversarial: replaying an old, already-ended cookie to DELETE must not
    end the user's current session."""
    from fastapi.testclient import TestClient
    body = account_body()
    first = TestClient(app)
    first.post("/api/session", json=body)
    old = first.cookies.get("biorx_session")
    first.delete("/api/session")                          # old cookie now ended
    code_body = {"code": body["code"], "pin": body["pin"]}
    current = TestClient(app)
    assert current.post("/api/session", json=code_body).status_code == 200
    replay = TestClient(app)
    replay.cookies.set("biorx_session", old)
    replay.delete("/api/session")
    assert current.get("/api/me").status_code == 200


def test_m10_current_user_is_sync():
    """Review M10: it runs blocking SQLite and file checks; as a plain def,
    FastAPI runs it in the thread pool, not on the event loop."""
    import inspect
    from web.auth import current_user
    assert not inspect.iscoroutinefunction(current_user)
