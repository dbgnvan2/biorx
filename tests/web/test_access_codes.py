"""
Personal access codes: code + PIN, codes in a readable file.

Spec: docs/implementation_plan_2026-09-18_invite_codes.md#PC1-PC15
"""
import os
import subprocess
import sys
import threading
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from fastapi.testclient import TestClient

from src import access_codes, accounts, user_store
from tests.web.conftest import ACCESS_CODE, TEST_PIN, add_code

ROOT = Path(__file__).parent.parent.parent


def _codes_file() -> Path:
    return Path(os.environ["ACCESS_CODES_FILE"])


def _write(text: str) -> None:
    p = _codes_file()
    p.write_text(text)
    # Make sure the store sees a new stamp even on a coarse-mtime filesystem.
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def _sign_in(app, code, pin=TEST_PIN):
    c = TestClient(app)
    return c, c.post("/api/session", json={"code": code, "pin": pin})


# ── PC1 ───────────────────────────────────────────────────────────────────────

def test_pc1_file_loads_with_default_expiry(monkeypatch):
    text = ("codes:\n"
            "  - code: AAAA-BBBB-CCCC\n    for: Alice\n    created: 2026-01-01\n"
            "  - code: dddd eeee ffff\n    for: Bob\n    expires: 2026-12-31\n")
    entries, warnings = access_codes.parse_codes(text)
    assert warnings == []
    assert entries["AAAABBBBCCCC"].expires == date(2026, 1, 1) + timedelta(days=180)
    assert entries["DDDDEEEEFFFF"].expires == date(2026, 12, 31)
    monkeypatch.setenv("ACCESS_CODE_DAYS", "30")
    assert access_codes.parse_codes(text)[0]["AAAABBBBCCCC"].expires == date(2026, 1, 31)
    monkeypatch.setenv("ACCESS_CODE_DAYS", "six months")
    assert access_codes.code_days() == 180


# ── PC2 ───────────────────────────────────────────────────────────────────────

def test_pc2_bad_entries_are_reported(caplog):
    text = ("codes:\n"
            "  - code: GOOD-CODE-0001\n    for: Good\n    created: 2026-09-18\n"
            "  - code: SHORT\n    for: Shorty\n    created: 2026-09-18\n"
            "  - code: NOFO-RNAM-E001\n    created: 2026-09-18\n"
            "  - code: BADD-ATEE-0001\n    for: Baddate\n    created: 18/09/2026\n"
            "  - code: NODA-TEST-0001\n    for: Nodate\n"
            "  - code: good code 0001\n    for: Dupe\n    created: 2026-09-18\n")
    entries, warnings = access_codes.parse_codes(text)
    assert list(entries) == ["GOODCODE0001"]
    assert len(warnings) == 5
    for who in ("Shorty", "no name", "Baddate", "Nodate", "Dupe"):
        assert any(who in w for w in warnings), who
    assert any("same code as entry 1" in w for w in warnings)
    # The code itself never appears in a warning (they reach the log).
    assert not any("GOOD" in w.upper().replace(" ", "") for w in warnings)


def test_pc2_healthz_reports_a_count_not_the_entries(app, client):
    _write("codes:\n  - code: SHORT\n    for: Secret Person\n    created: 2026-09-18\n")
    body = client.get("/healthz").json()
    joined = " ".join(body["startup_warnings"])
    assert "1 problem" in joined and "Secret Person" not in joined


def test_pc2_a_file_that_cannot_be_read_is_reported_not_a_crash(ctx, app, client):
    """learning-qa finding 1: an impossible date raised ValueError, not
    YAMLError, and took every request (and startup) down with a 500."""
    code = add_code("Keeper")
    for bad in ("codes:\n  - code: ABCD-EFGH-JKLM\n    for: X\n    expires: 2026-02-30\n",
                "codes:\n  - code: [unclosed\n"):
        entries, warnings = access_codes.parse_codes(bad)
        assert entries == {} and len(warnings) == 1
    _codes_file().write_bytes(b"codes:\n  - code: \xff\xfe\n")
    os.utime(_codes_file(), ns=(1, 10**18))
    assert client.get("/healthz").status_code == 200
    assert any("problem" in w for w in client.get("/healthz").json()["startup_warnings"])
    # Not "your code is wrong": the file is the problem, and it says so (503).
    _, r = _sign_in(app, code)
    assert r.status_code == 503 and r.json()["detail"] == access_codes.UNAVAILABLE_MESSAGE


def test_pc2_a_broken_file_tells_open_sessions_the_real_reason(ctx, app):
    """csdp review: a typo in the file told every signed-in person their own
    code had been turned off."""
    code = add_code("Mid Edit")
    c, _ = _sign_in(app, code)
    good = _codes_file().read_text()
    _write(good + "  - code: [unclosed\n")
    r = c.get("/api/me")
    assert r.status_code == 503 and r.json()["detail"] == access_codes.UNAVAILABLE_MESSAGE
    assert TestClient(app).post("/api/session/lookup", json={"code": code}).status_code == 503
    _write(good)
    assert c.get("/api/me").status_code == 200


def test_pc2_numbers_and_blanks_do_not_slip_through():
    """csdp review: `expires: 20270101` loaded as a number and quietly fell back
    to created + 180 days; a bare `disabled:` left the person on."""
    text = ("codes:\n"
            "  - code: AAAA-AAAA-AAAA\n    for: Num\n    created: 2026-01-01\n    expires: 20270101\n"
            "  - code: BBBB-BBBB-BBBB\n    for: Blank\n    created: 2026-09-18\n    disabled:\n")
    entries, warnings = access_codes.parse_codes(text)
    assert "AAAAAAAAAAAA" not in entries and any("Num" in w for w in warnings)
    assert entries["BBBBBBBBBBBB"].disabled and any("Blank" in w for w in warnings)


def test_pc2_content_without_a_codes_list_is_reported():
    assert access_codes.parse_codes("") == ({}, [])
    assert access_codes.parse_codes("# only a comment\n") == ({}, [])
    _, warnings = access_codes.parse_codes("- code: ABCD-EFGH-JKLM\n  for: Top\n")
    assert warnings and "no 'codes:' list" in warnings[0]


def test_pc2_disabled_fails_closed_and_unknown_keys_are_reported():
    """learning-qa finding 6: `disabled: y` or `disable: true` left the code on."""
    text = ("codes:\n"
            "  - code: AAAA-AAAA-AAAA\n    for: Yes\n    created: 2026-09-18\n    disabled: y\n"
            "  - code: BBBB-BBBB-BBBB\n    for: Garble\n    created: 2026-09-18\n"
            "    disabled: off-please\n"
            "  - code: CCCC-CCCC-CCCC\n    for: Typo\n    created: 2026-09-18\n    disable: true\n"
            "  - code: DDDD-DDDD-DDDD\n    for: On\n    created: 2026-09-18\n    disabled: false\n")
    entries, warnings = access_codes.parse_codes(text)
    assert entries["AAAAAAAAAAAA"].disabled and entries["BBBBBBBBBBBB"].disabled
    assert not entries["DDDDDDDDDDDD"].disabled
    assert any("Garble" in w and "treated as disabled" in w for w in warnings)
    assert any("Typo" in w and "disable" in w for w in warnings)


def test_pc2_a_missing_file_is_reported(ctx, client, caplog):
    add_code("Present")
    assert client.get("/healthz").json()["codes_in_use"] is True
    _codes_file().unlink()
    body = client.get("/healthz").json()
    assert body["codes_in_use"] is False
    assert any("no access codes file" in w.lower() for w in body["startup_warnings"])
    assert any("has gone" in r.getMessage() for r in caplog.records)


# ── PC3 ───────────────────────────────────────────────────────────────────────

def test_pc3_new_code_creates_account_with_pin(ctx, app):
    code = add_code("Alice")
    _, r = _sign_in(app, code, pin="")
    assert r.status_code == 400                          # "Enter your PIN."
    _, r = _sign_in(app, code, pin="123")
    assert r.status_code == 400                          # too short
    c, r = _sign_in(app, code, pin="alice-pin-1")
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["display_name"] == "Alice" and me["new_account"] is True
    assert c.get("/api/filters").json()["filters"], "new account has no seeded filters"
    # Afterwards the code needs that PIN, and returns the same account.
    _, wrong = _sign_in(app, code, pin="not-alices")
    assert wrong.status_code == 401
    _, again = _sign_in(app, code, pin="alice-pin-1")
    assert again.json()["user_id"] == me["user_id"] and again.json()["new_account"] is False
    assert ctx.db.conn.execute("SELECT COUNT(*) FROM access_code_bindings").fetchone()[0] == 1


# ── PC4 ───────────────────────────────────────────────────────────────────────

def test_pc4_code_and_pin_sign_in(app, monkeypatch):
    monkeypatch.setenv("LOGIN_MAX_FAILURES", "3")
    code = add_code("Bob")
    _, first = _sign_in(app, code, pin="bob-pin-11")
    typed = code.lower().replace("-", " ")               # case, spaces, dashes ignored
    _, r = _sign_in(app, typed, pin="bob-pin-11")
    assert r.status_code == 200 and r.json()["user_id"] == first.json()["user_id"]
    _, wrong_pin = _sign_in(app, code, pin="nope-nope-1")
    _, unknown = _sign_in(app, "ZZZZ-ZZZZ-ZZZZ", pin="bob-pin-11")
    assert wrong_pin.status_code == unknown.status_code == 401
    assert wrong_pin.json()["detail"] == unknown.json()["detail"]
    _sign_in(app, code, pin="nope-nope-2")
    _sign_in(app, code, pin="nope-nope-3")               # third failure locks
    _, locked = _sign_in(app, code, pin="bob-pin-11")
    assert locked.status_code == 429


# ── PC5 ───────────────────────────────────────────────────────────────────────

def test_pc5_cut_off_and_renew(app):
    code = add_code("Carol")
    c, r = _sign_in(app, code)
    assert r.status_code == 200
    text = _codes_file().read_text()

    _write(text.replace("    for: Carol\n", "    for: Carol\n    disabled: true\n"))
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/me").json()["detail"] == access_codes.DISABLED_MESSAGE
    _, r = _sign_in(app, code)
    assert r.status_code == 403 and r.json()["detail"] == access_codes.DISABLED_MESSAGE

    past = (date.today() - timedelta(days=1)).isoformat()
    expired = "\n".join(ln if "expires:" not in ln else f"    expires: {past}"
                        for ln in text.splitlines()) + "\n"
    _write(expired)
    assert c.get("/api/me").json()["detail"] == access_codes.EXPIRED_MESSAGE
    _, r = _sign_in(app, code)
    assert r.status_code == 403 and "renew" in r.json()["detail"]

    _write("codes:\n")                                    # entry deleted
    assert c.get("/api/me").status_code == 401

    _write(expired)
    access_codes.renew_entry(str(_codes_file()), "Carol")
    assert c.get("/api/me").status_code == 200            # renewing restores the session
    assert _sign_in(app, code)[1].status_code == 200


def test_pc5_expiry_is_checked_against_the_day(ctx):
    add_code("Dan")
    entry = ctx.codes.entries()[0]
    uid = accounts.create_code_account(ctx.db, "Dan", TEST_PIN, entry.key, user_store.new_user_id)
    assert access_codes.session_refusal(ctx.db, ctx.codes, uid, False, today=entry.expires) is None
    assert (access_codes.session_refusal(ctx.db, ctx.codes, uid, False,
                                         today=entry.expires + timedelta(days=1))
            == access_codes.EXPIRED_MESSAGE)


# ── PC6 ───────────────────────────────────────────────────────────────────────

def test_pc6_reset_pin(ctx, app):
    code = add_code("Erin")
    c, r = _sign_in(app, code, pin="old-pin-111")
    uid = r.json()["user_id"]
    c.post("/api/filters", json={"name": "Mine", "filter": {"text_groups": []}})
    assert access_codes.reset_pin(ctx.db, ctx.codes, "erin") == uid
    lookup = TestClient(app).post("/api/session/lookup", json={"code": code}).json()
    assert lookup == {"name": "Erin", "pin_set": False}
    # The reset also ends sessions opened with the old PIN.
    assert c.get("/api/me").status_code == 401
    c2, r = _sign_in(app, code, pin="new-pin-222")
    assert r.status_code == 200 and r.json()["user_id"] == uid
    assert _sign_in(app, code, pin="old-pin-111")[1].status_code == 401
    assert any(f["name"] == "Mine" for f in c2.get("/api/filters").json()["filters"])


def test_pc6_reset_pin_for_an_unused_code_says_so(ctx):
    add_code("Fresh")
    with pytest.raises(LookupError, match="not been used"):
        access_codes.reset_pin(ctx.db, ctx.codes, "Fresh")
    with pytest.raises(LookupError, match="No access code"):
        access_codes.reset_pin(ctx.db, ctx.codes, "Nobody")


# ── PC7 ───────────────────────────────────────────────────────────────────────

def test_pc7_existing_account_keeps_pin_and_data(ctx, app):
    uid, _ = accounts.create_account(ctx.db, "dave", "dave-pin-1", user_store.new_user_id)
    lid = user_store.create_reference_list(ctx.db, uid, "Dave's list")
    code = add_code("Dave", account="dave")
    lookup = TestClient(app).post("/api/session/lookup", json={"code": code}).json()
    assert lookup == {"name": "Dave", "pin_set": True}
    c, r = _sign_in(app, code, pin="dave-pin-1")
    assert r.status_code == 200 and r.json()["user_id"] == uid
    assert [l["id"] for l in c.get("/api/references").json()["lists"]] == [lid]


def test_pc7_unknown_account_is_refused_not_a_new_account(ctx, app, caplog):
    code = add_code("Ghost", account="no-such-person")
    _, r = _sign_in(app, code)
    assert r.status_code == 403
    assert ctx.db.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert any("no account named" in rec.getMessage() for rec in caplog.records)


# ── PC8 ───────────────────────────────────────────────────────────────────────

def test_pc8_switch_over(ctx, app, tmp_path):
    accounts.create_account(ctx.db, "Oldtimer", "old-pin-11", user_store.new_user_id)
    old = {"access_code": ACCESS_CODE, "name": "Oldtimer", "pin": "old-pin-11"}
    c = TestClient(app)
    assert c.post("/api/session", json=old).status_code == 200
    assert c.post("/api/session", json=old | {"name": "Newbie", "create": True}).status_code == 403

    # Shared code removed: the old way stops, and so does the open session.
    ctx.access_code = ""
    assert c.get("/api/me").status_code == 401
    assert TestClient(app).post("/api/session", json=old).status_code == 401
    # Given a code, the same account works again.
    code = add_code("Oldtimer", account="Oldtimer")
    _, r = _sign_in(app, code, pin="old-pin-11")
    assert r.status_code == 200


def test_pc8_an_old_account_with_a_disabled_code_cannot_use_the_old_way(ctx, app):
    accounts.create_account(ctx.db, "Sly", "sly-pin-11", user_store.new_user_id)
    add_code("Sly", account="Sly")
    _write(_codes_file().read_text().replace("    for: Sly\n", "    for: Sly\n    disabled: true\n"))
    r = TestClient(app).post("/api/session", json={"access_code": ACCESS_CODE, "name": "Sly",
                                                   "pin": "sly-pin-11"})
    assert r.status_code == 403


# ── PC9 ───────────────────────────────────────────────────────────────────────

def test_pc9_first_use_is_atomic(ctx):
    add_code("Racer")
    entry = ctx.codes.entries()[0]
    results, barrier = [], threading.Barrier(4)

    def go():
        barrier.wait()
        results.append(accounts.create_code_account(ctx.db, "Racer", TEST_PIN, entry.key,
                                                    user_store.new_user_id))
    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [r for r in results if r]
    assert len(winners) == 1
    assert access_codes.bound_user_by_key(ctx.db, entry.key) == winners[0]
    # The losers' user rows were rolled back, not left behind.
    assert ctx.db.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_pc9_the_request_that_loses_the_race_signs_in_to_the_winner(ctx, app, monkeypatch):
    """The route's fallback: create_code_account finds the code already bound,
    so the PIN is checked against the account that won."""
    code = add_code("Twin")
    entry = ctx.codes.get(code)
    winner = accounts.create_code_account(ctx.db, "Twin", "winner-pin-1", entry.key,
                                          user_store.new_user_id)
    real = access_codes.bound_user
    calls = []

    def first_sees_nothing(db, e):
        calls.append(1)
        return None if len(calls) == 1 else real(db, e)
    monkeypatch.setattr(access_codes, "bound_user", first_sees_nothing)
    _, r = _sign_in(app, code, pin="winner-pin-1")
    assert r.status_code == 200 and r.json()["user_id"] == winner
    assert r.json()["new_account"] is False
    calls.clear()
    assert _sign_in(app, code, pin="loser-pin-22")[1].status_code == 401
    assert ctx.db.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_pc9_a_merged_account_keeps_working_with_its_code(ctx, app):
    """learning-qa finding 3: after `accounts merge`, the code signed in and
    the next request refused (a sign-in loop)."""
    old_id, _ = accounts.create_account(ctx.db, "oldme", "old-pin-111", user_store.new_user_id)
    code = add_code("Me")
    _, r = _sign_in(app, code, pin="new-pin-222")
    new_id = r.json()["user_id"]
    accounts.merge_users(ctx.db, new_id, old_id)
    assert access_codes.bound_user_by_key(ctx.db, access_codes.code_key(code)) == old_id
    ctx.access_code = ""                                   # switch-over finished
    c, r = _sign_in(app, code, pin="old-pin-111")         # the merged-into account's PIN
    assert r.status_code == 200 and r.json()["user_id"] == old_id
    assert c.get("/api/me").status_code == 200
    # Turning the code off reaches the merged account too.
    _write(_codes_file().read_text().replace("    for: Me\n", "    for: Me\n    disabled: true\n"))
    assert c.get("/api/me").status_code == 401


# ── PC10 ──────────────────────────────────────────────────────────────────────

def test_pc10_file_change_picked_up(ctx, app):
    assert TestClient(app).post("/api/session/lookup",
                                json={"code": "HAND-MADE-CODE"}).status_code == 401
    _write("codes:\n  - code: HAND-MADE-CODE\n    for: Hana\n    created: 2026-09-18\n")
    r = TestClient(app).post("/api/session/lookup", json={"code": "hand made code"})
    assert r.status_code == 200 and r.json()["name"] == "Hana"


# ── PC11 ──────────────────────────────────────────────────────────────────────

def test_pc11_cli(tmp_path):
    f = tmp_path / "codes.yaml"
    f.write_text("# my own note, keep me\ncodes:\n")
    env = dict(os.environ, ACCESS_CODES_FILE=str(f), BIORX_DB_PATH=str(tmp_path / "cli.db"))
    run = lambda *a: subprocess.run([sys.executable, "-m", "src.access_codes", *a], cwd=ROOT,
                                    env=env, capture_output=True, text=True, timeout=60)
    out = run("add", "--for", "Ivy O'Neil")
    assert out.returncode == 0, out.stderr
    code = out.stdout.strip().splitlines()[-1].split(": ")[1].split()[0]
    entries, warnings = access_codes.parse_codes(f.read_text())
    assert warnings == [] and entries[access_codes.code_key(code)].for_name == "Ivy O'Neil"

    past = date.today() - timedelta(days=400)
    f.write_text(f.read_text().replace(f"created: {date.today()}", f"created: {past}")
                 .replace("expires:", "expires: 2020-01-01 #"))
    assert run("renew", "--for", "ivy o'neil").returncode == 0
    text = f.read_text()
    assert "# my own note, keep me" in text                       # comments survive
    assert code in text                                           # same code
    entry = access_codes.parse_codes(text)[0][access_codes.code_key(code)]
    assert entry.expires == date.today() + timedelta(days=180)

    listed = run("list")
    assert listed.returncode == 0 and code in listed.stdout and "new (not used yet)" in listed.stdout
    assert run("renew", "--for", "Nobody").returncode == 1
    assert run("reset-pin", "--for", "Ivy O'Neil").returncode == 1   # not used yet


def test_pc7_a_code_can_be_tied_to_an_account_with_no_name(tmp_path):
    """Dave's real account was reached only by cookie: no sign-in name, no PIN.
    `add --user-id` ties a code to it; the first sign-in chooses the PIN."""
    from src.db import Database
    dbp = tmp_path / "cli.db"
    db = Database(str(dbp))
    uid = user_store.create_user(db, "dave")
    user_store.create_reference_list(db, uid, "Dave's refs")
    f = tmp_path / "codes.yaml"
    env = dict(os.environ, ACCESS_CODES_FILE=str(f), BIORX_DB_PATH=str(dbp))
    run = lambda *a: subprocess.run([sys.executable, "-m", "src.access_codes", *a], cwd=ROOT,
                                    env=env, capture_output=True, text=True, timeout=60)
    bad = run("add", "--for", "Dave", "--user-id", "no-such-id")
    assert bad.returncode == 1 and not f.exists()
    out = run("add", "--for", "Dave", "--user-id", uid)
    assert out.returncode == 0, out.stderr
    code = out.stdout.strip().splitlines()[-1].split(": ")[1].split()[0]
    assert access_codes.bound_user_by_key(db, access_codes.code_key(code)) == uid
    assert not accounts.has_pin(db, uid)
    assert accounts.sign_in_user(db, uid, "dave-new-pin") == uid     # first sign-in sets it
    assert accounts.has_pin(db, uid)
    with pytest.raises(accounts.BadCredentials):
        accounts.sign_in_user(db, uid, "someone-else")
    db.close()


def test_pc11_renew_adds_a_missing_expiry_line(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("codes:\n  - code: ABCD-EFGH-JKLM\n    for: Jo\n    created: 2020-01-01\n\n"
                 "  - code: NMKL-HGFE-DCBA\n    for: Kim\n    created: 2020-01-01\n")
    access_codes.renew_entry(str(f), "Jo", today=date(2026, 9, 18))
    entries, warnings = access_codes.parse_codes(f.read_text())
    assert warnings == []
    assert entries["ABCDEFGHJKLM"].expires == date(2026, 9, 18) + timedelta(days=180)
    assert entries["NMKLHGFEDCBA"].expires == date(2020, 1, 1) + timedelta(days=180)


def test_pc11_renew_finds_the_right_entry_whatever_the_layout(tmp_path):
    """learning-qa findings 2 and 5: an entry starting `- for:` was folded into
    the one above, so renewing Bob changed Alice; a 2-space layout got an
    expires line at the wrong indent and broke the whole file."""
    f = tmp_path / "c.yaml"
    f.write_text("codes:\n"
                 "- code: AAAA-AAAA-AAAA\n  for: Alice\n  created: 2020-01-01\n"
                 "- for: Bob\n  code: BBBB-BBBB-BBBB\n  created: 2020-01-01\n")
    access_codes.renew_entry(str(f), "Bob", today=date(2026, 9, 18))
    entries, warnings = access_codes.parse_codes(f.read_text())
    assert warnings == []
    assert entries["BBBBBBBBBBBB"].expires == date(2026, 9, 18) + timedelta(days=180)
    assert entries["AAAAAAAAAAAA"].expires == date(2020, 1, 1) + timedelta(days=180)


def test_pc11_names_with_accents_and_symbols_round_trip(tmp_path):
    f = tmp_path / "c.yaml"
    for name in ("José Núñez", "Team #2", "O'Brien: lab"):
        code = access_codes.add_entry(str(f), name)
        assert access_codes.parse_codes(f.read_text())[0][access_codes.code_key(code)].for_name == name
        access_codes.renew_entry(str(f), name)
    assert "José Núñez" in f.read_text()                 # readable, not escaped


def test_pc11_add_to_an_empty_or_odd_file(tmp_path):
    """learning-qa finding 4: add to an empty file wrote an entry with no
    `codes:` above it — a code that never worked, with no warning."""
    f = tmp_path / "c.yaml"
    f.write_text("")
    code = access_codes.add_entry(str(f), "Empty")
    assert access_codes.code_key(code) in access_codes.parse_codes(f.read_text())[0]
    odd = tmp_path / "odd.yaml"
    odd.write_text("people:\n  - Alice\n")
    with pytest.raises(ValueError, match="not changed"):
        access_codes.add_entry(str(odd), "Nope")
    assert odd.read_text() == "people:\n  - Alice\n"


def test_pc11_a_renew_that_would_change_the_wrong_thing_leaves_the_file(tmp_path):
    f = tmp_path / "c.yaml"
    original = ("codes:\n  - code: AAAA-AAAA-AAAA\n    for: Dup\n    created: 2020-01-01\n"
                "  - code: BBBB-BBBB-BBBB\n    for: Dup\n    created: 2020-01-01\n")
    f.write_text(original)
    with pytest.raises(LookupError):
        access_codes.renew_entry(str(f), "Dup")
    assert f.read_text() == original


# ── PC12 ──────────────────────────────────────────────────────────────────────

def test_pc12_ignored_and_documented():
    gitignore = (ROOT / ".gitignore").read_text().splitlines()
    assert "access_codes.yaml" in gitignore
    example = ROOT / "access_codes.example.yaml"
    text = example.read_text()
    entries, warnings = access_codes.parse_codes(text)
    # No live codes in a public file (the first to use a code sets its PIN)...
    assert entries == {} and warnings == []
    # ...but the commented examples are valid when uncommented.
    import re
    uncommented = re.sub(r"(?m)^#(  +(?:- )?\w+:)", r"\1", text)
    entries, warnings = access_codes.parse_codes(uncommented)
    assert len(entries) == 2 and warnings == []
    env = (ROOT / ".env.example").read_text()
    assert "ACCESS_CODES_FILE" in env and "ACCESS_CODE_DAYS" in env


# ── PC14 ──────────────────────────────────────────────────────────────────────

def test_pc14_lookup_reveals_only_name(app):
    code = add_code("Lena")
    c = TestClient(app)
    assert c.post("/api/session/lookup", json={"code": code}).json() == {
        "name": "Lena", "pin_set": False}
    _sign_in(app, code)
    assert c.post("/api/session/lookup", json={"code": code}).json() == {
        "name": "Lena", "pin_set": True}
    assert "biorx_session" not in c.cookies                   # a lookup is not a sign-in
    r = c.post("/api/session/lookup", json={"code": "ZZZZ-ZZZZ-ZZZZ"})
    assert r.status_code == 401 and r.json()["detail"] == access_codes.BAD_CODE_OR_PIN


# ── csdp review 2026-09-18 ────────────────────────────────────────────────────

def test_pc14_lookup_does_no_dummy_hash_but_sign_in_does(app, monkeypatch):
    """The lookup answers at once for a known code, so a dummy scrypt for an
    unknown one hid nothing and let anyone make the server hash for free."""
    calls = []
    real = accounts.verify_secret
    monkeypatch.setattr(accounts, "verify_secret", lambda *a: calls.append(1) or real(*a))
    c = TestClient(app)
    assert c.post("/api/session/lookup", json={"code": "ZZZZ-ZZZZ-ZZZZ"}).status_code == 401
    assert calls == []
    assert c.post("/api/session", json={"code": "ZZZZ-ZZZZ-ZZZZ", "pin": "x" * 8}).status_code == 401
    assert calls == [1]


def test_pc14_lookup_refuses_a_code_sign_in_would_refuse(app):
    code = add_code("Ghost", account="no-such-person")
    r = TestClient(app).post("/api/session/lookup", json={"code": code})
    assert r.status_code == 403


def test_pc8_recover_is_refused_when_the_code_is_turned_off(ctx, app):
    """Recovery changed the PIN and issued a cookie that the next request
    refused. Now it is refused before anything changes."""
    _, recovery = accounts.create_account(ctx.db, "Sly2", "sly-pin-11", user_store.new_user_id)
    add_code("Sly2", account="Sly2")
    _write(_codes_file().read_text().replace("    for: Sly2\n", "    for: Sly2\n    disabled: true\n"))
    r = TestClient(app).post("/api/session/recover", json={
        "access_code": ACCESS_CODE, "name": "Sly2", "recovery_code": recovery,
        "new_pin": "new-pin-999"})
    assert r.status_code == 403 and r.json()["detail"] == access_codes.DISABLED_MESSAGE
    assert "biorx_session" not in r.cookies
    # The PIN was not changed.
    assert accounts.sign_in(ctx.db, "Sly2", "sly-pin-11")


def test_pc11_add_refuses_an_unknown_account(tmp_path):
    from src.db import Database
    dbp = tmp_path / "a.db"
    Database(str(dbp)).close()
    f = tmp_path / "codes.yaml"
    env = dict(os.environ, ACCESS_CODES_FILE=str(f), BIORX_DB_PATH=str(dbp))
    out = subprocess.run([sys.executable, "-m", "src.access_codes", "add", "--for", "X",
                          "--account", "typo-name"], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 1 and "Nothing added" in out.stdout and not f.exists()


def test_pc11_renew_keeps_the_next_entrys_comment_with_it(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("codes:\n  - code: AAAA-AAAA-AAAA\n    for: Alice\n    created: 2020-01-01\n"
                 "  # Bob, from the lab\n"
                 "  - code: BBBB-BBBB-BBBB\n    for: Bob\n    created: 2020-01-01\n")
    access_codes.renew_entry(str(f), "Alice", today=date(2026, 9, 18))
    lines = f.read_text().splitlines()
    assert lines[lines.index("  # Bob, from the lab") + 1] == "  - code: BBBB-BBBB-BBBB"
    assert lines[lines.index("  # Bob, from the lab") - 1].strip().startswith("expires:")


# ── csdp fix re-review 2026-09-18 ─────────────────────────────────────────────

def test_pc6_resets_and_merges_end_merged_away_sessions(ctx, app):
    """The check was on the cookie's own row, so a merged-away account's
    cookie survived a PIN reset of the account it points to."""
    bob, _ = accounts.create_account(ctx.db, "bob", "bob-pin-111", user_store.new_user_id)
    add_code("Bob", account="bob")
    code_m = add_code("Em")
    c_m, r = _sign_in(app, code_m, pin="em-pin-111")
    em = r.json()["user_id"]
    accounts.merge_users(ctx.db, em, bob)
    assert c_m.get("/api/me").json()["user_id"] == bob      # AC7: the cookie follows
    c2, r = _sign_in(app, code_m, pin="bob-pin-111")        # Em's code now reaches Bob
    assert r.status_code == 200 and r.json()["user_id"] == bob
    assert c2.get("/api/me").status_code == 200
    access_codes.reset_pin(ctx.db, ctx.codes, "Bob")
    assert c2.get("/api/me").status_code == 401             # a reset of Bob ends both
    assert c_m.get("/api/me").status_code == 401


def test_pc6_recovering_a_merged_away_name_ends_the_thiefs_session(ctx, app):
    alice, code = accounts.create_account(ctx.db, "alice", "alice-pin-1", user_store.new_user_id)
    bob, _ = accounts.create_account(ctx.db, "bob2", "bob-pin-111", user_store.new_user_id)
    accounts.merge_users(ctx.db, alice, bob)
    thief = TestClient(app)
    assert thief.post("/api/session", json={"access_code": ACCESS_CODE, "name": "alice",
                                            "pin": "alice-pin-1"}).status_code == 200
    assert thief.get("/api/me").json()["user_id"] == bob
    r = TestClient(app).post("/api/session/recover", json={
        "access_code": ACCESS_CODE, "name": "alice", "recovery_code": code, "new_pin": "new-pin-99"})
    assert r.status_code == 200
    assert thief.get("/api/me").status_code == 401


def test_pc5_a_down_file_refuses_old_accounts_too(ctx, app):
    """With the file unreadable we cannot tell whose entry says disabled, so the
    old name sign-in is refused as well (it let a disabled account in)."""
    accounts.create_account(ctx.db, "Legacy", "leg-pin-111", user_store.new_user_id)
    add_code("Legacy", account="Legacy")
    other = add_code("Someone")
    _sign_in(app, other)                                    # a code in use: bindings exist
    _write(_codes_file().read_text().replace("    for: Legacy\n",
                                             "    for: Legacy\n    disabled: true\n"))
    old = {"access_code": ACCESS_CODE, "name": "Legacy", "pin": "leg-pin-111"}
    assert TestClient(app).post("/api/session", json=old).status_code == 403
    for broken in ("codes:\n  - code: [unclosed\n", ""):
        _write(broken)
        r = TestClient(app).post("/api/session", json=old)
        assert r.status_code == 503 and r.json()["detail"] == access_codes.UNAVAILABLE_MESSAGE
    _codes_file().unlink()
    assert TestClient(app).post("/api/session", json=old).status_code == 503


def test_pc5_a_blank_file_is_down_but_an_emptied_list_is_not(ctx, app):
    code = add_code("Blank")
    c, _ = _sign_in(app, code)
    _write("")
    assert c.get("/api/me").status_code == 503
    _write("codes:\n")
    r = c.get("/api/me")
    assert r.status_code == 401 and r.json()["detail"] == access_codes.DISABLED_MESSAGE


def test_pc2_one_bad_entry_says_so_to_its_person(ctx, app):
    """A typo in one person's entry told them their code was turned off."""
    code = add_code("Typo")
    c, _ = _sign_in(app, code)
    text = _codes_file().read_text()
    expires_line = next(l for l in text.splitlines() if "expires:" in l)
    _write(text.replace(expires_line, "    expires: 20270101"))
    r = c.get("/api/me")
    assert r.status_code == 503 and r.json()["detail"] == access_codes.ENTRY_PROBLEM_MESSAGE
    _, r = _sign_in(app, code)
    assert r.status_code == 503 and r.json()["detail"] == access_codes.ENTRY_PROBLEM_MESSAGE


def test_pc8_recover_with_a_broken_file_is_503_and_costs_no_attempt(ctx, app):
    _, recovery = accounts.create_account(ctx.db, "Rec", "rec-pin-111", user_store.new_user_id)
    add_code("Rec", account="Rec")
    _sign_in(app, add_code("Other"))
    _write("codes:\n  - code: [unclosed\n")
    r = TestClient(app).post("/api/session/recover", json={
        "access_code": ACCESS_CODE, "name": "Rec", "recovery_code": recovery,
        "new_pin": "new-pin-999"})
    assert r.status_code == 503
    row = ctx.db.conn.execute("SELECT failed_logins FROM users WHERE login_name = 'Rec'").fetchone()
    assert row["failed_logins"] == 0


def test_pc4_a_reset_during_the_pin_check_does_not_leave_a_session(ctx, monkeypatch):
    add_code("Racy")
    entry = ctx.codes.entries()[0]
    uid = accounts.create_code_account(ctx.db, "Racy", "racy-pin-11", entry.key,
                                       user_store.new_user_id)
    real = accounts.verify_secret

    def reset_meanwhile(secret, stored):
        ok = real(secret, stored)
        access_codes.reset_pin(ctx.db, ctx.codes, "Racy")   # owner resets mid-check
        return ok
    monkeypatch.setattr(accounts, "verify_secret", reset_meanwhile)
    with pytest.raises(accounts.BadCredentials, match="just changed"):
        accounts.sign_in_user(ctx.db, uid, "racy-pin-11")


# ── csdp fix re-review, round 3 (2026-09-18) ──────────────────────────────────

def test_pc6_a_cookie_ended_by_a_reset_never_comes_back(ctx, app):
    """Per-account counters could meet after a merge (A reset twice = 2; B at 0
    then bumped...), reviving a dead cookie. Random nonces cannot."""
    a_id, _ = accounts.create_account(ctx.db, "reva", "reva-pin-11", user_store.new_user_id)
    b_id, _ = accounts.create_account(ctx.db, "revb", "revb-pin-11", user_store.new_user_id)
    add_code("RevA", account="reva")
    add_code("RevB", account="revb")
    old = {"access_code": ACCESS_CODE, "name": "reva", "pin": "reva-pin-11"}
    c = TestClient(app)
    accounts.end_sessions(ctx.db, a_id)                     # first reset
    assert c.post("/api/session", json=old).status_code == 200
    accounts.end_sessions(ctx.db, a_id)                     # second: this cookie dies
    assert c.get("/api/me").status_code == 401
    accounts.merge_users(ctx.db, a_id, b_id)
    assert c.get("/api/me").status_code == 401
    for _ in range(3):
        accounts.end_sessions(ctx.db, b_id)
        assert c.get("/api/me").status_code == 401


def test_pc6_reset_pin_also_clears_merged_away_pins(ctx, app):
    old_id, _ = accounts.create_account(ctx.db, "oldname", "old-pin-111", user_store.new_user_id)
    code = add_code("Newname")
    _, r = _sign_in(app, code, pin="new-pin-222")
    accounts.merge_users(ctx.db, old_id, r.json()["user_id"])
    access_codes.reset_pin(ctx.db, ctx.codes, "Newname")
    old = {"access_code": ACCESS_CODE, "name": "oldname", "pin": "old-pin-111"}
    assert TestClient(app).post("/api/session", json=old).status_code == 401


@pytest.mark.parametrize("text", [
    "{codes: [{code: CCCC-CCCC-CCCC, for: Carol, created: 2026-09-18}]}\n",
    "﻿codes:\n  - code: CCCC-CCCC-CCCC\n    for: Carol\n    created: 2026-09-18\n",
    '"codes":\n  - code: CCCC-CCCC-CCCC\n    for: Carol\n    created: 2026-09-18\n',
])
def test_pc2_valid_layouts_are_not_mistaken_for_a_blank_file(ctx, app, text):
    _sign_in(app, add_code("Someone"))                       # codes in use
    _codes_file().write_text(text, encoding="utf-8")
    os.utime(_codes_file(), ns=(1, 10**18))
    assert [e.for_name for e in ctx.codes.entries()] == ["Carol"]
    assert not ctx.codes.down(ctx.db)


def test_pc2_healthz_names_a_blank_file_while_codes_are_in_use(ctx, client, app):
    _sign_in(app, add_code("Someone"))
    _write("")
    warnings = client.get("/healthz").json()["startup_warnings"]
    assert any("missing, blank or unreadable" in w for w in warnings)


def test_pc2_an_unreadable_file_at_start_fails_closed(ctx, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can read a mode-000 file")
    f = tmp_path / "locked.yaml"
    f.write_text("codes:\n  - code: CCCC-CCCC-CCCC\n    for: Carol\n    created: 2026-09-18\n")
    f.chmod(0)
    try:
        store = access_codes.CodeStore(str(f))
        assert store.down(ctx.db)
        assert any("cannot be opened" in w for w in store.current_warnings())
    finally:
        f.chmod(0o600)
    assert not store.down(ctx.db)                            # retried, now readable


def test_pc8_a_mistyped_disabled_entry_does_not_let_the_old_way_in(ctx, app):
    accounts.create_account(ctx.db, "Legacy2", "leg-pin-111", user_store.new_user_id)
    _write("codes:\n  - code: LLLL-LLLL-LLLL\n    for: Legacy2\n    account: Legacy2\n"
           "    created: 18/09/2026\n    disabled: true\n")
    r = TestClient(app).post("/api/session", json={"access_code": ACCESS_CODE,
                                                   "name": "Legacy2", "pin": "leg-pin-111"})
    assert r.status_code == 503 and r.json()["detail"] == access_codes.ENTRY_PROBLEM_MESSAGE


# ── csdp fix re-review, round 4 (2026-09-18) ──────────────────────────────────

def _legacy_cookie(app, name, pin):
    c = TestClient(app)
    assert c.post("/api/session", json={"access_code": ACCESS_CODE, "name": name,
                                        "pin": pin}).status_code == 200
    return c


def test_pc6_an_ended_cookie_stays_ended_after_a_merge(ctx, app):
    """Round 3 compared on the resolved account; an ended "" cookie matched a
    never-reset target's "" after a merge and came back."""
    pa, _ = accounts.create_account(ctx.db, "pa", "pa-pin-111", user_store.new_user_id)
    pb, _ = accounts.create_account(ctx.db, "pb", "pb-pin-111", user_store.new_user_id)
    thief = _legacy_cookie(app, "pa", "pa-pin-111")
    accounts.end_sessions(ctx.db, pa)
    assert thief.get("/api/me").status_code == 401
    accounts.merge_users(ctx.db, pa, pb)
    assert thief.get("/api/me").status_code == 401


def test_ac7_a_merge_never_signs_anyone_out(ctx, app):
    qa, _ = accounts.create_account(ctx.db, "qa", "qa-pin-111", user_store.new_user_id)
    qb, _ = accounts.create_account(ctx.db, "qb", "qb-pin-111", user_store.new_user_id)
    accounts.end_sessions(ctx.db, qb)                       # qb was reset once, long ago
    c_a = _legacy_cookie(app, "qa", "qa-pin-111")
    c_b = _legacy_cookie(app, "qb", "qb-pin-111")
    accounts.merge_users(ctx.db, qa, qb)
    assert c_a.get("/api/me").json()["user_id"] == qb
    assert c_b.get("/api/me").json()["user_id"] == qb
    accounts.end_sessions(ctx.db, qb)                       # a reset of qb ends both
    assert c_a.get("/api/me").status_code == 401 and c_b.get("/api/me").status_code == 401


def test_pc6_reset_pin_reaches_every_merge_level(ctx, app):
    ca, _ = accounts.create_account(ctx.db, "ca", "ca-pin-111", user_store.new_user_id)
    cb, _ = accounts.create_account(ctx.db, "cb", "cb-pin-111", user_store.new_user_id)
    code = add_code("Cc")
    _, r = _sign_in(app, code, pin="cc-pin-111")
    cc = r.json()["user_id"]
    old_a = _legacy_cookie(app, "ca", "ca-pin-111")
    accounts.merge_users(ctx.db, ca, cb)
    accounts.merge_users(ctx.db, cb, cc)
    access_codes.reset_pin(ctx.db, ctx.codes, "Cc")
    r = TestClient(app).post("/api/session", json={"access_code": ACCESS_CODE,
                                                   "name": "ca", "pin": "ca-pin-111"})
    assert r.status_code == 401
    assert old_a.get("/api/me").status_code == 401


def test_pc2_a_file_recreated_unreadable_after_going_missing_is_down(ctx, app, caplog):
    accounts.create_account(ctx.db, "Leg3", "leg-pin-111", user_store.new_user_id)
    _sign_in(app, add_code("Someone"))                       # codes in use
    _codes_file().unlink()
    assert ctx.codes.down(ctx.db)
    _codes_file().mkdir()                                    # IsADirectoryError on read
    try:
        caplog.clear()
        assert ctx.codes.down(ctx.db)
        r = TestClient(app).post("/api/session", json={"access_code": ACCESS_CODE,
                                                       "name": "Leg3", "pin": "leg-pin-111"})
        assert r.status_code == 503
        for _ in range(5):
            ctx.codes.down(ctx.db)
        opened = [x for x in caplog.records if "cannot be opened" in x.getMessage()]
        assert len(opened) == 1                              # logged once, not per request
    finally:
        _codes_file().rmdir()


def test_pc8_an_entry_for_a_merged_away_name_still_applies(ctx, app):
    alice, _ = accounts.create_account(ctx.db, "alice3", "al-pin-111", user_store.new_user_id)
    bob, _ = accounts.create_account(ctx.db, "bob3", "bo-pin-111", user_store.new_user_id)
    accounts.merge_users(ctx.db, alice, bob)
    _write("codes:\n  - code: AAAA-LLLL-CCCC\n    for: Alice\n    account: alice3\n"
           "    created: 2026-09-18\n    disabled: true\n")
    r = TestClient(app).post("/api/session", json={"access_code": ACCESS_CODE,
                                                   "name": "alice3", "pin": "al-pin-111"})
    assert r.status_code == 403
