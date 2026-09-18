"""
Web accounts: name + PIN, recovery code, lockout, claim, merge.

Spec: docs/implementation_plan_2026-09-18_accounts.md#AC1-AC9
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

from src import accounts, user_store
from tests.web.conftest import ACCESS_CODE, account_body


def _client(app):
    return TestClient(app)


def _create(app, name="Dave", pin="dave-pin-1"):
    c = _client(app)
    r = c.post("/api/session", json=account_body(name=name, pin=pin))
    assert r.status_code == 200, r.text
    return c, r.json()


def _sign_in(app, name, pin):
    c = _client(app)
    return c, c.post("/api/session", json=account_body(name=name, pin=pin, create=False))


# ── AC1 ───────────────────────────────────────────────────────────────────────

def test_ac1_same_name_and_pin_return_the_same_user_and_data(app):
    c1, me = _create(app)
    fid = c1.post("/api/filters", json={"name": "Mine", "filter": {"text_groups": []}}).json()["id"]
    c1.delete("/api/session")
    c2, r = _sign_in(app, "  dave ", "dave-pin-1")        # case and spaces ignored
    assert r.status_code == 200
    assert r.json()["user_id"] == me["user_id"]
    assert any(f["id"] == fid for f in c2.get("/api/filters").json()["filters"])


def test_ac1_new_account_gets_seeded_filters_and_a_recovery_code(app):
    c, me = _create(app, name="Newbie")
    assert me["login_name"] == "Newbie"
    assert len(me["recovery_code"].replace("-", "")) == 16
    assert c.get("/api/filters").json()["filters"], "no seeded filters"


# ── AC2 ───────────────────────────────────────────────────────────────────────

def test_ac2_wrong_pin_and_unknown_name_look_the_same(app):
    _create(app)
    _, wrong = _sign_in(app, "Dave", "nope-nope-1")
    _, unknown = _sign_in(app, "Nobody", "nope-nope-1")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_ac2_taken_name_is_409(app):
    _create(app)
    c = _client(app)
    assert c.post("/api/session", json=account_body(name="DAVE", pin="other-pin-1")).status_code == 409


def test_ac2_short_pin_and_bad_name_are_400(app):
    c = _client(app)
    assert c.post("/api/session", json=account_body(name="Ok name", pin="123")).status_code == 400
    assert c.post("/api/session", json=account_body(name="x", pin="long-enough")).status_code == 400
    assert c.post("/api/session", json={"access_code": ACCESS_CODE}).status_code == 400


def test_ac2_access_code_still_required(app):
    c = _client(app)
    body = account_body(name="Sneaky", pin="sneaky-pin")
    body["access_code"] = "wrong"
    assert c.post("/api/session", json=body).status_code == 401


# ── AC3 ───────────────────────────────────────────────────────────────────────

def test_ac3_secrets_are_hashed(ctx, app):
    _, me = _create(app, name="Hashy", pin="plain-pin-42")
    row = dict(ctx.db.conn.execute("SELECT * FROM users WHERE user_id = ?",
                                   (me["user_id"],)).fetchone())
    blob = " ".join(str(v) for v in row.values())
    assert "plain-pin-42" not in blob
    code = me["recovery_code"]
    assert code not in blob and code.replace("-", "") not in blob
    assert row["pin_hash"].startswith("scrypt$") and row["recovery_hash"].startswith("scrypt$")


def test_ac3_verify_secret_rejects_malformed_hashes():
    assert not accounts.verify_secret("x", None)
    assert not accounts.verify_secret("x", "md5$aa$bb")
    assert accounts.verify_secret("pin", accounts.hash_secret("pin"))
    assert not accounts.verify_secret("pin2", accounts.hash_secret("pin"))


# ── AC4 ───────────────────────────────────────────────────────────────────────

def test_ac4_lockout_after_repeated_failures(ctx, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "3")
    monkeypatch.setenv("LOGIN_LOCK_MINUTES", "15")
    accounts.create_account(ctx.db, "Locky", "right-pin-1", user_store.new_user_id)
    t0 = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    for _ in range(3):
        with pytest.raises(accounts.BadCredentials):
            accounts.sign_in(ctx.db, "Locky", "wrong-pin", now=t0)
    with pytest.raises(accounts.AccountLocked):       # even the right PIN
        accounts.sign_in(ctx.db, "Locky", "right-pin-1", now=t0 + timedelta(minutes=5))
    assert accounts.sign_in(ctx.db, "Locky", "right-pin-1", now=t0 + timedelta(minutes=16))


def test_ac4_success_resets_the_count(ctx, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "3")
    accounts.create_account(ctx.db, "Resetty", "right-pin-1", user_store.new_user_id)
    for _ in range(2):
        with pytest.raises(accounts.BadCredentials):
            accounts.sign_in(ctx.db, "Resetty", "wrong-pin")
    accounts.sign_in(ctx.db, "Resetty", "right-pin-1")
    for _ in range(2):                                  # would lock if not reset
        with pytest.raises(accounts.BadCredentials):
            accounts.sign_in(ctx.db, "Resetty", "wrong-pin")
    assert accounts.sign_in(ctx.db, "Resetty", "right-pin-1")


def test_ac4_locked_route_is_429(app, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "2")
    _create(app, name="Rate", pin="rate-pin-1")
    for _ in range(2):
        _sign_in(app, "Rate", "bad-pin-00")
    _, r = _sign_in(app, "Rate", "rate-pin-1")
    assert r.status_code == 429
    assert "Try again in" in r.json()["detail"]


# ── AC5 ───────────────────────────────────────────────────────────────────────

def test_ac5_recovery_sets_a_new_pin_and_rotates_the_code(app):
    _, me = _create(app, name="Forgetful", pin="old-pin-11")
    old_code = me["recovery_code"]
    c = _client(app)
    r = c.post("/api/session/recover", json={"access_code": ACCESS_CODE, "name": "forgetful",
                                            "recovery_code": old_code.lower().replace("-", " "),
                                            "new_pin": "new-pin-22"})
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == me["user_id"]
    new_code = r.json()["recovery_code"]
    assert new_code != old_code
    assert _sign_in(app, "Forgetful", "new-pin-22")[1].status_code == 200
    assert _sign_in(app, "Forgetful", "old-pin-11")[1].status_code == 401
    again = _client(app).post("/api/session/recover", json={
        "access_code": ACCESS_CODE, "name": "Forgetful", "recovery_code": old_code,
        "new_pin": "another-33"})
    assert again.status_code == 401                     # the old code is spent


def test_ac5_wrong_recovery_codes_count_toward_lockout(ctx, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "2")
    accounts.create_account(ctx.db, "Guessy", "right-pin-1", user_store.new_user_id)
    for _ in range(2):
        with pytest.raises(accounts.BadCredentials):
            accounts.recover(ctx.db, "Guessy", "AAAA-BBBB-CCCC-DDDD", "new-pin-99")
    with pytest.raises(accounts.AccountLocked):
        accounts.sign_in(ctx.db, "Guessy", "right-pin-1")


def test_ac5_recover_needs_the_access_code(app):
    _, me = _create(app, name="Gated", pin="gated-pin-1")
    r = _client(app).post("/api/session/recover", json={
        "access_code": "wrong", "name": "Gated", "recovery_code": me["recovery_code"],
        "new_pin": "new-pin-22"})
    assert r.status_code == 401


# ── AC6 ───────────────────────────────────────────────────────────────────────

def test_ac6_cookie_only_user_can_claim_a_name(ctx, app):
    from web.auth import issue_session
    from fastapi import Response
    legacy_id = user_store.create_user(ctx.db, "old cookie user")
    user_store.create_reference_list(ctx.db, legacy_id, "Old list")
    c = _client(app)
    resp = Response()
    issue_session(resp, ctx, legacy_id, secure=False)
    c.cookies.set("biorx_session", resp.headers["set-cookie"].split(";")[0].split("=", 1)[1])
    r = c.post("/api/me/account", json={"name": "Claimer", "pin": "claim-pin-1"})
    assert r.status_code == 200, r.text
    assert r.json()["recovery_code"]
    c2, r2 = _sign_in(app, "claimer", "claim-pin-1")
    assert r2.json()["user_id"] == legacy_id
    assert [l["name"] for l in c2.get("/api/references").json()["lists"]] == ["Old list"]
    assert c.post("/api/me/account", json={"name": "Again", "pin": "claim-pin-2"}).status_code == 400


# ── AC7 ───────────────────────────────────────────────────────────────────────

def test_ac7_merge_moves_everything_and_the_old_cookie_follows(ctx, app):
    from web.auth import issue_session
    from fastapi import Response
    src = user_store.create_user(ctx.db, "dave")
    dst = user_store.create_user(ctx.db, "dave")
    user_store.upsert_filter(ctx.db, src, "Shared", {"text_groups": []})
    user_store.upsert_filter(ctx.db, dst, "Shared", {"text_groups": [{"both": "x"}]})
    user_store.upsert_filter(ctx.db, src, "Only src", {"text_groups": []})
    lid = user_store.create_reference_list(ctx.db, src, "Src list")
    pid = ctx.db.insert_paper({"title": "T", "doi": "10.1/m", "canonical_id": "doi:10.1/m"})
    user_store.add_reference_item(ctx.db, lid, pid)

    user_store.upsert_filter(ctx.db, src, "Same", {"days_back": 7})
    user_store.upsert_filter(ctx.db, dst, "Same", {"days_back": 7})
    moved = accounts.merge_users(ctx.db, src, dst)
    assert moved == {"filters": 2, "filters_renamed": 1, "filters_identical_left": 1,
                     "lists": 1, "lists_renamed": 0}
    names = sorted(f["name"] for f in user_store.list_filters(ctx.db, dst))
    assert names == ["Only src", "Same", "Shared", "Shared (merged)"]
    # The identical copy stays with the merged user; nothing is deleted.
    assert [f["name"] for f in user_store.list_filters(ctx.db, src)] == ["Same"]
    assert len(user_store.list_reference_items(ctx.db, lid)) == 1      # items kept

    c = _client(app)
    resp = Response()
    issue_session(resp, ctx, src, secure=False)
    c.cookies.set("biorx_session", resp.headers["set-cookie"].split(";")[0].split("=", 1)[1])
    assert c.get("/api/me").json()["user_id"] == dst
    assert [l["name"] for l in c.get("/api/references").json()["lists"]] == ["Src list"]


def test_ac7_merge_refuses_self_and_unknown(ctx):
    uid = user_store.create_user(ctx.db, "x")
    with pytest.raises(accounts.AccountError):
        accounts.merge_users(ctx.db, uid, uid)
    with pytest.raises(accounts.AccountError):
        accounts.merge_users(ctx.db, uid, "no-such-user")


# ── AC9 ───────────────────────────────────────────────────────────────────────

def test_ac9_thresholds_come_from_env_and_are_documented(monkeypatch):
    monkeypatch.setenv("LOGIN_PIN_MIN_LENGTH", "8")
    assert accounts.pin_min_length() == 8
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "not-a-number")
    assert accounts.max_failures() == 5
    example = (Path(__file__).parent.parent.parent / ".env.example").read_text()
    for var in ("LOGIN_MAX_FAILURES", "LOGIN_LOCK_MINUTES", "LOGIN_PIN_MIN_LENGTH"):
        assert var in example, var
