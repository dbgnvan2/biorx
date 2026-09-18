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
    """An account from before personal codes (name + PIN), signed in the old
    way. The web page no longer creates these (invite_codes plan PC8)."""
    ctx = app.state.ctx
    _, code = accounts.create_account(ctx.db, name, pin, user_store.new_user_id)
    c, r = _sign_in(app, name, pin)
    assert r.status_code == 200, r.text
    me = r.json()
    me["recovery_code"] = code
    return c, me


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


# ── AC2 ───────────────────────────────────────────────────────────────────────

def test_ac2_wrong_pin_and_unknown_name_look_the_same(app):
    _create(app)
    _, wrong = _sign_in(app, "Dave", "nope-nope-1")
    _, unknown = _sign_in(app, "Nobody", "nope-nope-1")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_ac2_taken_name_is_refused(ctx):
    accounts.create_account(ctx.db, "Dave", "dave-pin-1", user_store.new_user_id)
    with pytest.raises(accounts.NameTaken):
        accounts.create_account(ctx.db, "DAVE", "other-pin-1", user_store.new_user_id)


def test_ac2_short_pin_and_bad_name_are_refused(ctx, app):
    with pytest.raises(accounts.AccountError):
        accounts.create_account(ctx.db, "Ok name", "123", user_store.new_user_id)
    with pytest.raises(accounts.AccountError):
        accounts.create_account(ctx.db, "x", "long-enough", user_store.new_user_id)
    c = _client(app)
    assert c.post("/api/session", json={"access_code": ACCESS_CODE}).status_code == 400


def test_ac2_access_code_still_required(app):
    _create(app, name="Sneaky", pin="sneaky-pin")
    body = account_body(name="Sneaky", pin="sneaky-pin", create=False)
    body["access_code"] = "wrong"
    assert _client(app).post("/api/session", json=body).status_code == 401


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


# AC6 (claim a name for a cookie-only user) was removed with personal access
# codes: a code now makes the account (invite_codes plan PC3).


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


# ── csdp security review 2026-09-18 ───────────────────────────────────────────

@pytest.mark.parametrize("path", ["name", "code"])
def test_ac4_parallel_wrong_pins_are_all_counted(ctx, monkeypatch, caplog, path):
    """200 wrong PINs sent at once were all checked (187 before the first lock)
    because each read the count before any wrote it. At most
    LOGIN_MAX_FAILURES may be checked per lock window."""
    import threading
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "5")
    uid, _ = accounts.create_account(ctx.db, "Target", "right-pin-1", user_store.new_user_id)
    outcomes, barrier = [], threading.Barrier(30)

    def guess(i):
        barrier.wait()
        try:
            if path == "name":
                accounts.sign_in(ctx.db, "Target", f"wrong-pin-{i:03d}")
            else:
                accounts.sign_in_user(ctx.db, uid, f"wrong-pin-{i:03d}")
            outcomes.append("in")
        except accounts.AccountLocked:
            outcomes.append("locked")
        except accounts.BadCredentials:
            outcomes.append("checked")
    threads = [threading.Thread(target=guess, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("in") == 0
    assert outcomes.count("checked") <= 5, outcomes.count("checked")
    locked_logs = [r for r in caplog.records if "Account locked" in r.getMessage()]
    assert len(locked_logs) == 1, len(locked_logs)          # logged once, not per request
    with pytest.raises(accounts.AccountLocked):
        accounts.sign_in(ctx.db, "Target", "right-pin-1")


def test_ac5_recovering_a_pin_ends_other_sessions(app):
    c_old, me = _create(app, name="Stolen", pin="old-pin-11")
    assert c_old.get("/api/me").status_code == 200
    r = _client(app).post("/api/session/recover", json={
        "access_code": ACCESS_CODE, "name": "Stolen", "recovery_code": me["recovery_code"],
        "new_pin": "new-pin-22"})
    assert r.status_code == 200
    stale = c_old.get("/api/me")
    assert stale.status_code == 401 and "PIN was changed" in stale.json()["detail"]


def test_ac7_merge_refuses_a_target_that_is_already_merged(ctx):
    a = user_store.create_user(ctx.db, "a")
    b = user_store.create_user(ctx.db, "b")
    c = user_store.create_user(ctx.db, "c")
    accounts.merge_users(ctx.db, b, c)
    with pytest.raises(accounts.AccountError, match="already merged"):
        accounts.merge_users(ctx.db, a, b)            # would strand a's data
    with pytest.raises(accounts.AccountError, match="already merged"):
        accounts.merge_users(ctx.db, b, a)            # b is already gone
    assert accounts.merge_users(ctx.db, a, c)["filters"] == 0
