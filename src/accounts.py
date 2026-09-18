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


def resolve_user_id(db, user_id: str, depth: int = 5) -> Optional[str]:
    """Follow merged_into, so a cookie for a merged user reaches the target."""
    for _ in range(depth):
        row = db.conn.execute("SELECT user_id, merged_into FROM users WHERE user_id = ?",
                              (user_id,)).fetchone()
        if row is None:
            return None
        if not row["merged_into"]:
            return row["user_id"]
        user_id = row["merged_into"]
    return None


def _check_lock(row, now: datetime) -> None:
    until = row["locked_until"]
    if until:
        until_dt = datetime.fromisoformat(until)
        if until_dt > now:
            left = max(1, int((until_dt - now).total_seconds() // 60) + 1)
            raise AccountLocked(left)


def _record_failure(db, row, now: datetime) -> None:
    failures = (row["failed_logins"] or 0) + 1
    locked = None
    if failures >= max_failures():
        locked = (now + timedelta(minutes=lock_minutes())).isoformat()
        failures = 0
        logger.warning("Account locked after repeated failures: %s", row["user_id"])
    db.conn.execute("UPDATE users SET failed_logins = ?, locked_until = ? WHERE user_id = ?",
                    (failures, locked, row["user_id"]))
    db.conn.commit()


def _record_success(db, user_id: str) -> None:
    db.conn.execute("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE user_id = ?",
                    (user_id,))
    db.conn.commit()


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
    _check_lock(row, now)
    if not verify_secret(pin, row["pin_hash"]):
        _record_failure(db, row, now)
        raise BadCredentials("That name or PIN is not right.")
    _record_success(db, row["user_id"])
    return resolve_user_id(db, row["user_id"]) or row["user_id"]


def recover(db, name: str, code: str, new_pin: str,
            now: Optional[datetime] = None) -> Tuple[str, str]:
    """Set a new PIN with the recovery code. (user_id, new recovery code)."""
    now = now or _now()
    if len(new_pin or "") < pin_min_length():
        raise AccountError(f"Choose a PIN of at least {pin_min_length()} characters.")
    row = _by_name(db, name)
    if row is None:
        verify_secret(code, None)
        raise BadCredentials("That name or recovery code is not right.")
    _check_lock(row, now)
    if not verify_secret(_normalise_code(code), row["recovery_hash"]):
        _record_failure(db, row, now)
        raise BadCredentials("That name or recovery code is not right.")
    fresh = new_recovery_code()
    db.conn.execute(
        "UPDATE users SET pin_hash = ?, recovery_hash = ?, failed_logins = 0, locked_until = NULL "
        "WHERE user_id = ?",
        (hash_secret(new_pin), hash_secret(_normalise_code(fresh)), row["user_id"]),
    )
    db.conn.commit()
    return row["user_id"], fresh


def claim(db, user_id: str, name: str, pin: str) -> str:
    """Give a cookie-only user a name + PIN. Returns the recovery code."""
    clean = _check_new(name, pin)
    row = db.conn.execute("SELECT login_name FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        raise AccountError("No such user.")
    if row["login_name"]:
        raise AccountError("This account already has a name and PIN.")
    code = new_recovery_code()
    try:
        db.conn.execute(
            "UPDATE users SET login_name = ?, display_name = ?, pin_hash = ?, recovery_hash = ? "
            "WHERE user_id = ?",
            (clean, clean, hash_secret(pin), hash_secret(_normalise_code(code)), user_id),
        )
        db.conn.commit()
    except sqlite3.IntegrityError as e:
        db.conn.rollback()
        raise NameTaken("That name is already taken. Choose another name.") from e
    return code


def merge_users(db, source_id: str, target_id: str) -> dict:
    """Move one user's data into another, in one transaction. Nothing is deleted:
    the source row stays, marked merged_into, so its cookie resolves to the target."""
    if source_id == target_id:
        raise AccountError("Cannot merge a user into itself.")
    for uid in (source_id, target_id):
        if db.conn.execute("SELECT 1 FROM users WHERE user_id = ?", (uid,)).fetchone() is None:
            raise AccountError(f"No such user: {uid}")
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
        conn.execute("UPDATE users SET merged_into = ? WHERE user_id = ?", (target_id, source_id))
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
