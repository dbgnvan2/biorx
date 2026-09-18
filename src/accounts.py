"""
Web accounts: a name + PIN that always return the same user.

Purpose: Let a user get back to their filters and Saved References from any
         browser, without the shared access code letting anyone pose as them.
Spec:    docs/implementation_plan_2026-09-18_accounts.md#AC1-AC9
Tests:   tests/web/test_accounts.py

The shared access code opens the door; the name + PIN say who you are. PINs and
recovery codes are stored as scrypt hashes, compared in constant time, and
repeated failures lock the account for a while — the access code is shared, so
without a lockout anyone holding it could guess PINs.

One-off merge:  python -m src.accounts merge --db PATH --from USER_ID --into USER_ID
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

NAME_MIN, NAME_MAX = 2, 40
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"    # no 0/O/1/I


class AccountError(Exception):
    """A sign-in problem safe to show the user."""


class BadCredentials(AccountError):
    pass


class NameTaken(AccountError):
    pass


class AccountCutOff(AccountError):
    """The person's access code is expired, disabled or gone."""


class AccountLocked(AccountError):
    def __init__(self, minutes_left: int):
        super().__init__(f"Too many wrong attempts. Try again in {minutes_left} minute(s).")
        self.minutes_left = minutes_left


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        logger.warning("%s is not a whole number — using %d", name, default)
        return default


def max_failures() -> int:
    return _env_int("LOGIN_MAX_FAILURES", 5)


def lock_minutes() -> int:
    return _env_int("LOGIN_LOCK_MINUTES", 15)


def pin_min_length() -> int:
    return _env_int("LOGIN_PIN_MIN_LENGTH", 6)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Secrets ───────────────────────────────────────────────────────────────────

def hash_secret(secret: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(secret.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_secret(secret: str, stored: Optional[str]) -> bool:
    try:
        scheme, salt_hex, digest_hex = (stored or "").split("$")
        if scheme != "scrypt":
            raise ValueError
        digest = hashlib.scrypt(secret.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except ValueError:
        # Burn the same work as a real check, so a missing account is not
        # distinguishable by timing from a wrong PIN.
        hashlib.scrypt(secret.encode(), salt=b"\0" * 16, **_SCRYPT)
        return False


def new_recovery_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def _normalise_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def normalise_name(name: str) -> str:
    return " ".join((name or "").split())


def _check_new(name: str, pin: str) -> str:
    clean = normalise_name(name)
    if not (NAME_MIN <= len(clean) <= NAME_MAX):
        raise AccountError(f"Choose a name of {NAME_MIN}–{NAME_MAX} characters.")
    if len(pin or "") < pin_min_length():
        raise AccountError(f"Choose a PIN of at least {pin_min_length()} characters.")
    return clean


# ── Lookups ───────────────────────────────────────────────────────────────────

def _by_name(db, name: str):
    return db.conn.execute(
        "SELECT * FROM users WHERE login_name IS NOT NULL AND lower(login_name) = lower(?)",
        (normalise_name(name),),
    ).fetchone()


def resolve_user_id(db, user_id: str) -> Optional[str]:
    """Follow merged_into, so a cookie for a merged user reaches the target.
    Any depth (a fixed limit signed people out after the fifth merge); None
    for an unknown id or a loop, which only a hand edit can make."""
    seen = set()
    while user_id not in seen:
        seen.add(user_id)
        row = db.conn.execute("SELECT user_id, merged_into FROM users WHERE user_id = ?",
                              (user_id,)).fetchone()
        if row is None:
            return None
        if not row["merged_into"]:
            return row["user_id"]
        user_id = row["merged_into"]
    logger.warning("merged_into loop at user %s — refusing", user_id)
    return None


def _check_lock(row, now: datetime) -> None:
    until = row["locked_until"]
    if until:
        until_dt = datetime.fromisoformat(until)
        if until_dt > now:
            left = max(1, int((until_dt - now).total_seconds() // 60) + 1)
            raise AccountLocked(left)


def _lock(db, user_id: str, now: datetime) -> None:
    stamp = now.astimezone(timezone.utc)
    until = (stamp + timedelta(minutes=lock_minutes())).isoformat()
    # Only if not already locked: a burst of parallel guesses would otherwise
    # re-lock (and log) once per request.
    cur = db.conn.execute(
        "UPDATE users SET failed_logins = 0, locked_until = ? WHERE user_id = ? "
        "AND (locked_until IS NULL OR locked_until <= ?)", (until, user_id, stamp.isoformat()))
    db.conn.commit()
    if cur.rowcount:
        logger.warning("Account locked after repeated failures: %s", user_id)


def _claim_attempt(db, row, now: datetime) -> int:
    """Count this attempt BEFORE checking the secret, in one statement.

    Reading the count, running scrypt, then writing count+1 let parallel
    guesses all read the same count: the security review sent 200 wrong PINs
    at once and 187 were checked before the lock (limit 5). The conditional
    UPDATE ... RETURNING makes each attempt take its own slot, so at most
    LOGIN_MAX_FAILURES secrets are checked per lock window.
    """
    _check_lock(row, now)
    cur = db.conn.execute(
        "UPDATE users SET failed_logins = COALESCE(failed_logins, 0) + 1 "
        "WHERE user_id = ? AND (locked_until IS NULL OR locked_until <= ?) "
        "RETURNING failed_logins",
        (row["user_id"], now.astimezone(timezone.utc).isoformat()))
    got = cur.fetchone()
    db.conn.commit()
    if got is None:                       # another request locked it just now
        raise AccountLocked(lock_minutes())
    if got[0] > max_failures():           # the slots for this window are used up
        _lock(db, row["user_id"], now)
        raise AccountLocked(lock_minutes())
    return got[0]


def _failed(db, user_id: str, attempt: int, now: datetime) -> None:
    if attempt >= max_failures():
        _lock(db, user_id, now)


class SignedIn(str):
    """The account signed in to (a plain user id for routes: the account the
    data lives in, after merges), carrying the cookie to issue: the row whose
    PIN was accepted and its session nonce, read in the same statement that
    accepted the PIN. A reset landing mid-check therefore ends this session
    too, merged-away names included (csdp review rounds 2 and 5)."""

    nonce: str = ""
    cookie_user: str = ""

    def __new__(cls, user_id: str, nonce: Optional[str], cookie_user: str = ""):
        obj = super().__new__(cls, user_id)
        obj.nonce = nonce or ""
        obj.cookie_user = cookie_user or user_id
        return obj


def _record_success(db, user_id: str, checked_hash: Optional[str]) -> int:
    """Clear the failure count, only if the PIN hash is still the one that was
    checked. Returns the session nonce; raises if the PIN changed meanwhile."""
    cur = db.conn.execute(
        "UPDATE users SET failed_logins = 0, locked_until = NULL "
        "WHERE user_id = ? AND pin_hash IS ? RETURNING session_nonce", (user_id, checked_hash))
    got = cur.fetchone()
    db.conn.commit()
    if got is None:
        raise BadCredentials("That PIN was just changed. Sign in again.")
    return got[0] or ""


def _signed_in(db, row_user_id: str, nonce: str) -> "SignedIn":
    final = resolve_user_id(db, row_user_id)
    if final is None:
        raise BadCredentials("This account cannot be opened. Ask the owner.")
    # The cookie names the checked row with the nonce read with the PIN;
    # current_user follows the merge to the data.
    return SignedIn(final, nonce, cookie_user=row_user_id)


# ── Operations ────────────────────────────────────────────────────────────────

def create_account(db, name: str, pin: str, new_user_id: Callable[[], str]) -> Tuple[str, str]:
    """(user_id, recovery_code). Raises NameTaken or AccountError."""
    clean = _check_new(name, pin)
    code = new_recovery_code()
    user_id = new_user_id()
    try:
        db.conn.execute(
            "INSERT INTO users (user_id, display_name, login_name, pin_hash, recovery_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, clean, clean, hash_secret(pin), hash_secret(_normalise_code(code))),
        )
        db.conn.commit()
    except sqlite3.IntegrityError as e:
        db.conn.rollback()
        raise NameTaken("That name is already taken. Sign in, or choose another name.") from e
    return user_id, code


def sign_in(db, name: str, pin: str, now: Optional[datetime] = None) -> str:
    now = now or _now()
    row = _by_name(db, name)
    if row is None:
        verify_secret(pin, None)
        raise BadCredentials("That name or PIN is not right.")
    attempt = _claim_attempt(db, row, now)
    if not verify_secret(pin, row["pin_hash"]):
        _failed(db, row["user_id"], attempt, now)
        raise BadCredentials("That name or PIN is not right.")
    nonce = _record_success(db, row["user_id"], row["pin_hash"])
    return _signed_in(db, row["user_id"], nonce)


def _by_id(db, user_id: str):
    return db.conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()


def check_new_pin(pin: str) -> None:
    if len(pin or "") < pin_min_length():
        raise AccountError(f"Choose a PIN of at least {pin_min_length()} characters.")


def has_pin(db, user_id: str) -> bool:
    row = _by_id(db, user_id)
    return bool(row and row["pin_hash"])


def sign_in_user(db, user_id: str, pin: str, now: Optional[datetime] = None) -> str:
    """Check the PIN of a user already picked out by their access code
    (docs/implementation_plan_2026-09-18_invite_codes.md#PC4). Same lockout as
    sign_in. A user with no PIN (new, or reset by the owner) sets it here."""
    now = now or _now()
    row = _by_id(db, user_id)
    if row is None:
        verify_secret(pin, None)
        raise BadCredentials("That code or PIN is not right.")
    _check_lock(row, now)
    if not row["pin_hash"]:
        check_new_pin(pin)
        # Only set if still unset, so two people racing to choose a PIN for
        # one code cannot overwrite each other.
        cur = db.conn.execute(
            "UPDATE users SET pin_hash = ?, failed_logins = 0, locked_until = NULL "
            "WHERE user_id = ? AND pin_hash IS NULL RETURNING session_nonce",
            (hash_secret(pin), user_id))
        got = cur.fetchone()
        db.conn.commit()
        if got is None:
            return sign_in_user(db, user_id, pin, now)
        return _signed_in(db, user_id, got[0] or "")
    attempt = _claim_attempt(db, row, now)
    if not verify_secret(pin, row["pin_hash"]):
        _failed(db, user_id, attempt, now)
        raise BadCredentials("That code or PIN is not right.")
    nonce = _record_success(db, user_id, row["pin_hash"])
    return _signed_in(db, user_id, nonce)


def create_code_account(db, display_name: str, pin: str, code_key: str,
                        new_user_id: Callable[[], str]) -> Optional[str]:
    """Make the account for a code's first use, with its PIN, and bind the code
    in the same transaction. None if another request bound the code first
    (PC9) — the caller then signs in against that account instead."""
    check_new_pin(pin)
    user_id = new_user_id()
    conn = db.conn
    try:
        conn.execute("INSERT INTO users (user_id, display_name, pin_hash) VALUES (?, ?, ?)",
                     (user_id, display_name, hash_secret(pin)))
        cur = conn.execute("INSERT OR IGNORE INTO access_code_bindings (code_key, user_id) "
                           "VALUES (?, ?)", (code_key, user_id))
        if cur.rowcount != 1:
            conn.rollback()
            return None
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return user_id


def merged_family(db, user_id: str) -> list:
    """The account a user resolves to, plus every account merged into it at any
    depth (A→B→C: all three). Loop-safe: UNION drops repeats."""
    final = resolve_user_id(db, user_id)
    if final is None:
        # Unknown id or a hand-made loop: say so, and act on this one account.
        logger.warning("Cannot follow merges from user %s — acting on it alone", user_id)
        final = user_id
    return [r[0] for r in db.conn.execute(
        "WITH RECURSIVE fam(id) AS (SELECT ? UNION "
        "SELECT u.user_id FROM users u JOIN fam ON u.merged_into = fam.id) "
        "SELECT id FROM fam", (final,))]


def end_sessions(db, user_id: str, commit: bool = True) -> None:
    """End every session that reaches this account's data.

    web/auth.py compares a cookie's nonce with the nonce of the account the
    cookie NAMES. Each account in the merged family gets a fresh random nonce,
    so cookies of merged-away accounts end too, and an ended cookie can never
    match again — not even after a later merge, which leaves nonces alone
    (csdp review rounds 3 and 4)."""
    for uid in merged_family(db, user_id):
        db.conn.execute("UPDATE users SET session_nonce = ? WHERE user_id = ?",
                        (secrets.token_urlsafe(16), uid))
    if commit:
        db.conn.commit()


def recover(db, name: str, code: str, new_pin: str,
            now: Optional[datetime] = None,
            refusal: Optional[Callable[[str], Optional[str]]] = None) -> Tuple[str, str]:
    """Set a new PIN with the recovery code. (user_id, new recovery code).

    refusal(user_id) -> reason is checked after the code is verified and before
    anything changes, so a person whose access code was turned off cannot use
    recovery to change their PIN (csdp review 2026-09-18)."""
    now = now or _now()
    if len(new_pin or "") < pin_min_length():
        raise AccountError(f"Choose a PIN of at least {pin_min_length()} characters.")
    row = _by_name(db, name)
    if row is None:
        verify_secret(code, None)
        raise BadCredentials("That name or recovery code is not right.")
    attempt = _claim_attempt(db, row, now)
    if not verify_secret(_normalise_code(code), row["recovery_hash"]):
        _failed(db, row["user_id"], attempt, now)
        raise BadCredentials("That name or recovery code is not right.")
    reason = refusal(resolve_user_id(db, row["user_id"]) or row["user_id"]) if refusal else None
    if reason:
        # The code was right: give the attempt back, so refusals for a reason
        # outside the person's control do not lock them out.
        db.conn.execute("UPDATE users SET failed_logins = 0 WHERE user_id = ?", (row["user_id"],))
        db.conn.commit()
        raise AccountCutOff(reason)
    fresh = new_recovery_code()
    db.conn.execute(
        "UPDATE users SET pin_hash = ?, recovery_hash = ?, failed_logins = 0, locked_until = NULL "
        "WHERE user_id = ?",
        (hash_secret(new_pin), hash_secret(_normalise_code(fresh)), row["user_id"]),
    )
    end_sessions(db, row["user_id"], commit=False)
    db.conn.commit()
    return row["user_id"], fresh


def merge_users(db, source_id: str, target_id: str) -> dict:
    """Move one user's data into another, in one transaction. Nothing is deleted:
    the source row stays, marked merged_into, so its cookie resolves to the target."""
    if source_id == target_id:
        raise AccountError("Cannot merge a user into itself.")
    rows = {}
    for uid in (source_id, target_id):
        rows[uid] = db.conn.execute("SELECT merged_into FROM users WHERE user_id = ?",
                                    (uid,)).fetchone()
        if rows[uid] is None:
            raise AccountError(f"No such user: {uid}")
    # Data moved into an account that is itself merged would be unreachable,
    # and a merge back the other way makes a loop (csdp review 2026-09-18).
    if rows[target_id]["merged_into"]:
        raise AccountError(f"{target_id} is already merged into "
                           f"{rows[target_id]['merged_into']}; merge into that one instead.")
    if rows[source_id]["merged_into"]:
        raise AccountError(f"{source_id} is already merged into {rows[source_id]['merged_into']}.")
    moved = {"filters": 0, "filters_renamed": 0, "filters_identical_left": 0,
             "lists": 0, "lists_renamed": 0}
    conn = db.conn
    try:
        target_filters = {r["name"]: r["filter_json"] for r in conn.execute(
            "SELECT name, filter_json FROM user_filters WHERE user_id = ?", (target_id,))}
        for table, key in (("user_filters", "filters"), ("user_reference_lists", "lists")):
            taken = {r["name"] for r in conn.execute(
                f"SELECT name FROM {table} WHERE user_id = ?", (target_id,))}
            cols = "id, name, filter_json" if table == "user_filters" else "id, name"
            for r in conn.execute(f"SELECT {cols} FROM {table} WHERE user_id = ?",
                                  (source_id,)).fetchall():
                name = r["name"]
                if table == "user_filters" and target_filters.get(name) == r["filter_json"]:
                    # An identical copy (every account starts with the same
                    # seeded filters): nothing to bring over. Left with the
                    # merged user, not deleted.
                    moved["filters_identical_left"] += 1
                    continue
                if name in taken:
                    base, n = f"{name} (merged)", 2
                    name = base
                    while name in taken:
                        name, n = f"{base} {n}", n + 1
                    moved[f"{key}_renamed"] += 1
                conn.execute(f"UPDATE {table} SET user_id = ?, name = ? WHERE id = ?",
                             (target_id, name, r["id"]))
                taken.add(name)
                moved[key] += 1
        conn.execute("UPDATE usage_events SET user_id = ? WHERE user_id = ?", (target_id, source_id))
        conn.execute("UPDATE summaries SET created_by_user_id = ? WHERE created_by_user_id = ?",
                     (target_id, source_id))
        # Personal access codes follow the data (invite_codes plan; learning-qa
        # finding 3: otherwise a merged person's code signs into an account
        # whose next request refuses them).
        conn.execute("UPDATE access_code_bindings SET user_id = ? WHERE user_id = ?",
                     (target_id, source_id))
        conn.execute("UPDATE users SET merged_into = ? WHERE user_id = ?", (target_id, source_id))
        # Session nonces are left alone: cookies are checked against the
        # account they name, so a live cookie of either account keeps working
        # (AC7: nobody is signed out mid-merge) and an ended one stays ended.
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return moved


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="BioRx web account tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge", help="move one user's data into another")
    m.add_argument("--db", required=True)
    m.add_argument("--from", dest="source", required=True)
    m.add_argument("--into", dest="target", required=True)
    sub.add_parser("list", help="list users").add_argument("--db", required=True)
    args = ap.parse_args(argv)
    from .db import Database
    db = Database(args.db)
    if args.cmd == "list":
        for r in db.conn.execute(
                "SELECT u.user_id, u.display_name, u.login_name, u.merged_into, u.created_at, "
                "(SELECT COUNT(*) FROM user_filters f WHERE f.user_id=u.user_id) AS filters, "
                "(SELECT COUNT(*) FROM user_reference_lists l WHERE l.user_id=u.user_id) AS lists "
                "FROM users u ORDER BY u.created_at"):
            print(dict(r))
        return 0
    print(merge_users(db, args.source, args.target))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
