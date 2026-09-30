"""
Web accounts: code + PIN, lockout, merge (the name sign-in and recovery codes
were removed on 2026-09-30, decision D2).

Spec: docs/implementation_plan_2026-09-18_accounts.md#AC1-AC9
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

from src import accounts, user_store
from tests.web.conftest import account_body, legacy_account


def _client(app):
    return TestClient(app)


def _create(app, name="Dave", pin="dave-pin-1"):
    """An account made by a personal code's first use, signed in. The name +
    PIN sign-in is gone (decision D2, 2026-09-30); me["code"] is the code."""
    body = account_body(name=name, pin=pin)
    c = _client(app)
    r = c.post("/api/session", json=body)
    assert r.status_code == 200, r.text
    me = r.json()
    me["code"] = body["code"]
    return c, me


def _sign_in(app, code, pin):
    c = _client(app)
    return c, c.post("/api/session", json={"code": code, "pin": pin})


# ── AC1 ───────────────────────────────────────────────────────────────────────

def test_ac1_same_code_and_pin_return_the_same_user_and_data(app):
    c1, me = _create(app)
    fid = c1.post("/api/filters", json={"name": "Mine", "filter": {"text_groups": []}}).json()["id"]
    c1.delete("/api/session")
    c2, r = _sign_in(app, " " + me["code"].lower() + " ", "dave-pin-1")   # case and spaces ignored
    assert r.status_code == 200
    assert r.json()["user_id"] == me["user_id"]
    assert any(f["id"] == fid for f in c2.get("/api/filters").json()["filters"])


# ── AC2 ───────────────────────────────────────────────────────────────────────

def test_ac2_wrong_pin_and_unknown_code_look_the_same(app):
    _, me = _create(app)
    _, wrong = _sign_in(app, me["code"], "nope-nope-1")
    _, unknown = _sign_in(app, "ZZZZ-ZZZZ-ZZZZ", "nope-nope-1")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_ac2_short_pin_is_refused(ctx, app):
    with pytest.raises(accounts.AccountError):
        accounts.create_code_account(ctx.db, "Ok name", "123", "KEYKEYKEYKEY",
                                     user_store.new_user_id)
    c = _client(app)
    assert c.post("/api/session", json={}).status_code == 400


# ── AC3 ───────────────────────────────────────────────────────────────────────

def test_ac3_secrets_are_hashed(ctx, app):
    _, me = _create(app, name="Hashy", pin="plain-pin-42")
    row = dict(ctx.db.conn.execute("SELECT * FROM users WHERE user_id = ?",
                                   (me["user_id"],)).fetchone())
    blob = " ".join(str(v) for v in row.values())
    assert "plain-pin-42" not in blob
    assert row["pin_hash"].startswith("scrypt$")


def test_ac3_verify_secret_rejects_malformed_hashes():
    assert not accounts.verify_secret("x", None)
    assert not accounts.verify_secret("x", "md5$aa$bb")
    assert accounts.verify_secret("pin", accounts.hash_secret("pin"))
    assert not accounts.verify_secret("pin2", accounts.hash_secret("pin"))


# ── AC4 ───────────────────────────────────────────────────────────────────────

def test_ac4_lockout_after_repeated_failures(ctx, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "3")
    monkeypatch.setenv("LOGIN_LOCK_MINUTES", "15")
    uid, _ = legacy_account(ctx.db, "Locky", "right-pin-1")
    t0 = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    for _ in range(3):
        with pytest.raises(accounts.BadCredentials):
            accounts.sign_in_user(ctx.db, uid, "wrong-pin", now=t0)
    with pytest.raises(accounts.AccountLocked):       # even the right PIN
        accounts.sign_in_user(ctx.db, uid, "right-pin-1", now=t0 + timedelta(minutes=5))
    assert accounts.sign_in_user(ctx.db, uid, "right-pin-1", now=t0 + timedelta(minutes=16))


def test_ac4_success_resets_the_count(ctx, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "3")
    uid, _ = legacy_account(ctx.db, "Resetty", "right-pin-1")
    for _ in range(2):
        with pytest.raises(accounts.BadCredentials):
            accounts.sign_in_user(ctx.db, uid, "wrong-pin")
    accounts.sign_in_user(ctx.db, uid, "right-pin-1")
    for _ in range(2):                                  # would lock if not reset
        with pytest.raises(accounts.BadCredentials):
            accounts.sign_in_user(ctx.db, uid, "wrong-pin")
    assert accounts.sign_in_user(ctx.db, uid, "right-pin-1")


def test_ac4_locked_route_is_429(app, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "2")
    _, me = _create(app, name="Rate", pin="rate-pin-1")
    for _ in range(2):
        _sign_in(app, me["code"], "bad-pin-00")
    _, r = _sign_in(app, me["code"], "rate-pin-1")
    assert r.status_code == 429
    assert "Try again in" in r.json()["detail"]


# AC5 (recovery codes) was removed with the name sign-in (decision D2,
# 2026-09-30): a forgotten PIN is reset by the owner (access_codes reset-pin).

# AC6 (claim a name for a cookie-only user) was removed with personal access
# codes: a code now makes the account (invite_codes plan PC3).


# ── AC7 ───────────────────────────────────────────────────────────────────────

def test_ac7_merge_moves_everything_and_the_old_cookie_follows(ctx, app):
    from web.auth import issue_session
    from fastapi import Response
    from src.access_codes import code_key
    from tests.web.conftest import add_code
    src = user_store.create_user(ctx.db, "dave")
    dst = user_store.create_user(ctx.db, "dave")
    # The target has a code, as every account that can sign in does (D2).
    ctx.db.conn.execute("INSERT INTO access_code_bindings (code_key, user_id) VALUES (?, ?)",
                        (code_key(add_code("dave")), dst))
    ctx.db.conn.commit()
    user_store.insert_filter(ctx.db, src, "Shared", {"text_groups": []})
    user_store.insert_filter(ctx.db, dst, "Shared", {"text_groups": [{"both": "x"}]})
    user_store.insert_filter(ctx.db, src, "Only src", {"text_groups": []})
    lid = user_store.create_reference_list(ctx.db, src, "Src list")
    pid = ctx.db.insert_paper({"title": "T", "doi": "10.1/m", "canonical_id": "doi:10.1/m"})
    user_store.add_reference_item(ctx.db, lid, pid)

    user_store.insert_filter(ctx.db, src, "Same", {"days_back": 7})
    user_store.insert_filter(ctx.db, dst, "Same", {"days_back": 7})
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

def test_ac4_parallel_wrong_pins_are_all_counted(ctx, monkeypatch, caplog):
    """200 wrong PINs sent at once were all checked (187 before the first lock)
    because each read the count before any wrote it. At most
    LOGIN_MAX_FAILURES may be checked per lock window."""
    import threading
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "5")
    uid, _ = legacy_account(ctx.db, "Target", "right-pin-1")
    outcomes, barrier = [], threading.Barrier(30)

    def guess(i):
        barrier.wait()
        try:
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
        accounts.sign_in_user(ctx.db, uid, "right-pin-1")


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


# ── M32: new accounts are seeded from filters.seed.json ──────────────────────

def test_m32_seed_from_seed_file(ctx, app, monkeypatch, tmp_path):
    import json
    from web import routes_session
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"filters": [{"name": "Only in the seed", "days_back": 3}]}))
    monkeypatch.setattr(routes_session, "SEED_FILTERS", seed)
    c = TestClient(app)
    c.post("/api/session", json=account_body())
    assert [f["name"] for f in c.get("/api/filters").json()["filters"]] == ["Only in the seed"]


def test_m32_the_real_seed_file_is_neutral():
    import json
    from web.routes_session import SEED_FILTERS
    names = [f["name"] for f in json.loads(SEED_FILTERS.read_text())["filters"]]
    assert names and "New Filter" not in names
    assert len({n.casefold() for n in names}) == len(names)


def test_m32_seed_never_overwrites(ctx, caplog):
    """Batch-3 gate note 2: two seed entries with one name used to collapse."""
    import logging
    user = user_store.create_user(ctx.db, "Ann")
    with caplog.at_level(logging.WARNING, logger="src.user_store"):
        n = user_store.seed_filters_from_file(ctx.db, user, [
            {"name": "Stress", "days_back": 7}, {"name": "stress", "days_back": 99},
            {"name": "", "days_back": 1}])
    assert n == 1
    kept = user_store.list_filters(ctx.db, user)
    assert [f["name"] for f in kept] == ["Stress"] and kept[0]["days_back"] == 7
    assert "'stress' skipped" in caplog.text and "no name" in caplog.text
