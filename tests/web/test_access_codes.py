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
    assert _sign_in(app, code)[1].status_code == 401


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
    _, r = _sign_in(app, code, pin="new-pin-222")
    assert r.status_code == 200 and r.json()["user_id"] == uid
    assert _sign_in(app, code, pin="old-pin-111")[1].status_code == 401
    assert any(f["name"] == "Mine" for f in c.get("/api/filters").json()["filters"])


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
    entries, warnings = access_codes.parse_codes(example.read_text())
    assert entries and warnings == []
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
