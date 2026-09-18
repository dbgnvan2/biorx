"""
Personal access codes: one code per person, kept in a plain file the owner reads.

Purpose: The code says who you are, the PIN proves it. The owner hands out,
         renews and turns off codes by editing access_codes.yaml (or with the
         commands below); the server re-reads the file when it changes.
Spec:    docs/implementation_plan_2026-09-18_invite_codes.md#PC1-PC15
Tests:   tests/web/test_access_codes.py

The codes are not secret on purpose (Dave, 2026-09-18): people forget them, and
the owner tells them again from the file. The PIN is the secret.

Commands (run with the same environment as the server, e.g.
`set -a && source .env && set +a` first, so DATA_DIR points at the same place):

    python -m src.access_codes add --for "Alice" [--account dave | --user-id ID]
    python -m src.access_codes renew --for "Alice"
    python -m src.access_codes reset-pin --for "Alice"
    python -m src.access_codes list
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# Hand-written codes must be long enough not to be guessed through the public
# lookup (there is no per-IP limit yet — TODO). Generated codes have 12.
CODE_MIN_CHARS = 10
_KNOWN_KEYS = {"code", "for", "created", "expires", "account", "disabled"}
_TRUE = {"true", "yes", "y", "on", "1"}
_FALSE = {"false", "no", "n", "off", "0"}
DEFAULT_DAYS = 180
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0/O/1/I; 12 chars = 60 bits

EXPIRED_MESSAGE = "Your access code has expired. Ask the owner to renew it."
DISABLED_MESSAGE = "Your access code has been turned off. Ask the owner."
BAD_CODE_OR_PIN = "That code or PIN is not right."
# The file itself is broken or gone: say so, rather than telling each person
# their own code was turned off (csdp review 2026-09-18). Still refuses.
UNAVAILABLE_MESSAGE = ("Sign-in is unavailable: the server's access-code file has a "
                       "problem. Please tell the owner.")
_FILE_PROBLEM = "access codes file"

_HEADER = """\
# Personal access codes — one per person.
# Kept readable on purpose: when someone forgets their code, look it up here.
# Each entry: code, for (who it is for), created or expires (YYYY-MM-DD).
# Optional: account (ties the code to an existing account's sign-in name),
#           disabled: true (turns this person off).
# Changes take effect without restarting the server.
codes:
"""


def codes_file_path() -> str:
    """ACCESS_CODES_FILE, else DATA_DIR/access_codes.yaml (same rule as the db)."""
    from .db import resolve_data_path
    return str(Path(resolve_data_path("ACCESS_CODES_FILE", "access_codes.yaml")).expanduser())


def code_days() -> int:
    raw = os.environ.get("ACCESS_CODE_DAYS", "")
    if not raw:
        return DEFAULT_DAYS
    try:
        days = int(raw)
        if days <= 0:
            raise ValueError
        return days
    except ValueError:
        logger.warning("ACCESS_CODE_DAYS=%r is not a positive whole number — using %d",
                       raw, DEFAULT_DAYS)
        return DEFAULT_DAYS


def code_key(code: str) -> str:
    """Case, spaces and dashes do not matter when a code is typed."""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def new_code() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(12))
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


@dataclass(frozen=True)
class CodeEntry:
    number: int              # 1-based position in the file, for messages
    code: str
    key: str
    for_name: str
    expires: date
    account: str = ""
    disabled: bool = False

    def status(self, today: date) -> str:
        if self.disabled:
            return "disabled"
        if today > self.expires:
            return "expired"
        return "active"

    def refusal(self, today: date) -> Optional[str]:
        """None when the code may be used today, else the message to show."""
        st = self.status(today)
        if st == "disabled":
            return DISABLED_MESSAGE
        if st == "expired":
            return EXPIRED_MESSAGE
        return None


def _as_date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str):
        return date.fromisoformat(value.strip())
    # e.g. `expires: 20270101` loads as a number: refuse, do not fall back.
    raise ValueError(f"{value!r} is not a date")


def parse_codes(text: str, days: Optional[int] = None) -> Tuple[Dict[str, CodeEntry], List[str]]:
    """(entries by key, warnings). A bad entry is skipped and named in a
    warning; the good ones still load (PC2)."""
    days = days if days is not None else code_days()
    warnings: List[str] = []
    try:
        data = yaml.safe_load(text)
    except (yaml.YAMLError, ValueError) as e:
        # ValueError: an impossible date such as 2026-02-30, which YAML's own
        # date reader rejects. Either way, report it; never crash the server.
        return {}, [f"access codes file cannot be read, so no codes work: {e}"]
    if data is None:
        return {}, []
    if not isinstance(data, dict) or "codes" not in data:
        return {}, ["access codes file has no 'codes:' list, so no codes work"]
    raw = data["codes"]
    if raw is None:                       # `codes:` with nothing under it
        return {}, []
    if not isinstance(raw, list):
        return {}, ["access codes file: 'codes:' must be a list of entries"]

    entries: Dict[str, CodeEntry] = {}
    for n, item in enumerate(raw, start=1):
        where = f"access code entry {n}"
        if not isinstance(item, dict):
            warnings.append(f"{where}: not an entry (expected code:, for:, …) — skipped")
            continue
        code = str(item.get("code") or "").strip()
        for_name = " ".join(str(item.get("for") or "").split())
        # Never the code itself: warnings reach the log and a count reaches /healthz.
        where = f"{where} ({for_name or 'no name'})"
        key = code_key(code)
        if len(key) < CODE_MIN_CHARS:
            warnings.append(f"{where}: code must have at least {CODE_MIN_CHARS} letters "
                            "or digits — skipped")
            continue
        if not for_name:
            warnings.append(f"{where}: needs 'for:' (who the code is for) — skipped")
            continue
        try:
            created = _as_date(item.get("created"))
            expires = _as_date(item.get("expires"))
        except ValueError:
            warnings.append(f"{where}: a date is not YYYY-MM-DD — skipped")
            continue
        if expires is None and created is None:
            warnings.append(f"{where}: needs 'created:' or 'expires:' (YYYY-MM-DD) — skipped")
            continue
        if expires is None:
            expires = created + timedelta(days=days)
        if key in entries:
            warnings.append(f"{where}: same code as entry {entries[key].number} — skipped")
            continue
        unknown = sorted(str(k) for k in item if str(k) not in _KNOWN_KEYS)
        if unknown:
            warnings.append(f"{where}: unknown setting(s) {', '.join(unknown)} — ignored")
        raw_disabled = item.get("disabled", False)
        flag = str(raw_disabled).strip().lower()
        if raw_disabled is True or flag in _TRUE:
            disabled = True
        elif raw_disabled is False or (raw_disabled is not None and flag in _FALSE):
            disabled = False
        else:
            # An off switch must fail closed: an unreadable value turns it off.
            warnings.append(f"{where}: disabled: {raw_disabled!r} is not true/false "
                            "— treated as disabled")
            disabled = True
        entries[key] = CodeEntry(
            number=n, code=code, key=key, for_name=for_name, expires=expires,
            account=" ".join(str(item.get("account") or "").split()),
            disabled=disabled,
        )
    return entries, warnings


class CodeStore:
    """The codes file, re-read when it changes (PC10). Thread-safe."""

    def __init__(self, path: str):
        self.path = Path(path).expanduser()
        self._lock = threading.Lock()
        self._stamp = None
        self._loaded = False
        self._entries: Dict[str, CodeEntry] = {}
        self.warnings: List[str] = []
        self.missing = False
        self.broken = False        # the whole file failed to load
        self._seen = False         # the file has existed while this server ran

    def unavailable(self) -> bool:
        """True when no code can be trusted because the file is gone or broken."""
        self._refresh()
        # A file that was never created is just "no codes yet"; one that
        # existed and went away is a problem to report.
        return (self.missing and self._seen) or self.broken

    def _refresh(self) -> None:
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        except FileNotFoundError:
            stamp = None
        with self._lock:
            if self._loaded and stamp == self._stamp:
                return
            if stamp is None:
                if self._loaded and not self.missing:
                    logger.warning("Access codes file %s has gone — no code works "
                                   "until it is back", self.path)
                elif not self._loaded:
                    logger.warning("No access codes file at %s", self.path)
                self._entries, self.warnings, self.missing, self.broken = {}, [], True, False
            else:
                self.missing, self._seen = False, True
                try:
                    text = self.path.read_text(encoding="utf-8")
                except UnicodeDecodeError as e:
                    self._entries, self.broken = {}, True
                    self.warnings = [f"access codes file is not UTF-8 text, so no codes work: {e}"]
                    logger.warning("%s", self.warnings[0])
                    self._stamp, self._loaded = stamp, True
                    return
                except OSError as e:
                    # Keep the last good copy rather than locking everyone out
                    # because of a read that raced an editor's save.
                    logger.warning("Could not read %s: %s — keeping the last copy",
                                   self.path, e)
                    return
                self._entries, self.warnings = parse_codes(text)
                self.broken = (not self._entries and
                               any(w.startswith(_FILE_PROBLEM) for w in self.warnings))
                for w in self.warnings:
                    logger.warning("%s", w)
                logger.info("Loaded %d access code(s) from %s", len(self._entries), self.path)
            self._stamp = stamp
            self._loaded = True

    def get(self, code: str) -> Optional[CodeEntry]:
        self._refresh()
        key = code_key(code)
        return self._entries.get(key) if key else None

    def get_by_key(self, key: str) -> Optional[CodeEntry]:
        self._refresh()
        return self._entries.get(key)

    def entries(self) -> List[CodeEntry]:
        self._refresh()
        return sorted(self._entries.values(), key=lambda e: e.number)

    def current_warnings(self) -> List[str]:
        self._refresh()
        return list(self.warnings)


# ── Which account a code belongs to ───────────────────────────────────────────

def bound_user(db, entry: CodeEntry) -> Optional[str]:
    """The user a code belongs to: its recorded binding, else the account named
    by `account:` (recorded on first use)."""
    row = db.conn.execute("SELECT user_id FROM access_code_bindings WHERE code_key = ?",
                          (entry.key,)).fetchone()
    if row:
        from .accounts import resolve_user_id
        return resolve_user_id(db, row["user_id"]) or row["user_id"]
    if entry.account:
        user = db.conn.execute(
            "SELECT user_id FROM users WHERE login_name IS NOT NULL "
            "AND lower(login_name) = lower(?)", (entry.account,)).fetchone()
        if user is None:
            logger.warning("access code entry %d: no account named %r",
                           entry.number, entry.account)
            return None
        bind(db, entry.key, user["user_id"])
        return bound_user_by_key(db, entry.key)
    return None


def bound_user_by_key(db, key: str) -> Optional[str]:
    row = db.conn.execute("SELECT user_id FROM access_code_bindings WHERE code_key = ?",
                          (key,)).fetchone()
    return row["user_id"] if row else None


def bind(db, key: str, user_id: str) -> bool:
    """Record the binding; False if the code is already bound (first use wins)."""
    cur = db.conn.execute(
        "INSERT OR IGNORE INTO access_code_bindings (code_key, user_id) VALUES (?, ?)",
        (key, user_id))
    db.conn.commit()
    return cur.rowcount == 1


def session_refusal(db, store: CodeStore, user_id: str, shared_code_set: bool,
                    today: Optional[date] = None) -> Optional[str]:
    """None if this user may keep using the app today, else why not (PC5, PC8).

    A user with a code needs at least one of their codes to be in the file and
    usable. A user with none (an account from before codes) is allowed only
    while the shared ACCESS_CODE is still set.
    """
    today = today or date.today()
    # Bindings made under an account later merged into this one count too.
    keys = [r["code_key"] for r in db.conn.execute(
        "SELECT code_key FROM access_code_bindings WHERE user_id = ? OR user_id IN "
        "(SELECT user_id FROM users WHERE merged_into = ?)", (user_id, user_id))]
    login = db.conn.execute("SELECT login_name FROM users WHERE user_id = ?",
                            (user_id,)).fetchone()
    login_name = (login["login_name"] or "").lower() if login else ""
    mine = [store.get_by_key(k) for k in keys]
    if login_name:
        mine += [e for e in store.entries() if e.account.lower() == login_name]
    if not keys and not any(mine):
        if store.unavailable() and not shared_code_set:
            return UNAVAILABLE_MESSAGE
        return None if shared_code_set else "Sign in with your personal access code."
    if keys and store.unavailable():
        return UNAVAILABLE_MESSAGE
    live = [e for e in mine if e is not None]
    if any(e.refusal(today) is None for e in live):
        return None
    if any(e.status(today) == "expired" for e in live):
        return EXPIRED_MESSAGE
    return DISABLED_MESSAGE


# ── Editing the file (comments the owner wrote are kept) ──────────────────────
# Edits are made line by line so hand-written comments survive, then the
# result is parsed again and checked; if the check fails the file is left as it
# was and the command fails (a renew that silently changed someone else's entry
# would be worse than no renew).

_ITEM_START = re.compile(r"^(\s*)-\s+(\w+)\s*:")
_EXPIRES_LINE = re.compile(r"^(\s*)expires\s*:.*$")


def _item_indent(lines: List[str]) -> Optional[str]:
    """The indent of the list items under `codes:`."""
    for ln in lines:
        m = _ITEM_START.match(ln)
        if m:
            return m.group(1)
    return None


def _blocks(lines: List[str]) -> List[Tuple[int, int]]:
    """(start, end) of each list item, whatever key it starts with."""
    indent = _item_indent(lines)
    if indent is None:
        return []
    starts = [i for i, ln in enumerate(lines)
              if (m := _ITEM_START.match(ln)) and m.group(1) == indent]
    ends = starts[1:] + [len(lines)]
    return list(zip(starts, ends))


def _block_for_name(lines: List[str], start: int, end: int) -> str:
    try:
        item = yaml.safe_load("\n".join(lines[start:end]))
    except (yaml.YAMLError, ValueError):
        return ""
    if isinstance(item, list) and item and isinstance(item[0], dict):
        return " ".join(str(item[0].get("for") or "").split())
    return ""


def _find_block(lines: List[str], for_name: str) -> Tuple[int, int]:
    want = " ".join(for_name.split()).lower()
    found = [(s, e) for s, e in _blocks(lines) if _block_for_name(lines, s, e).lower() == want]
    if not found:
        raise LookupError(f"No access code for {for_name!r} in the file.")
    if len(found) > 1:
        raise LookupError(f"More than one access code is for {for_name!r}; "
                          "edit the file by hand.")
    return found[0]


def _write_atomically(p: Path, text: str) -> None:
    """Write via a temporary file and a rename, so the server never reads a
    half-written file and cuts people off for a moment."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def _scalar(value: str) -> str:
    return yaml.safe_dump(value, allow_unicode=True, width=1000).strip().splitlines()[0]


def add_entry(path: str, for_name: str, account: str = "",
              today: Optional[date] = None) -> str:
    """Append a new code for a person and return it (PC11)."""
    today = today or date.today()
    for_name = " ".join((for_name or "").split())
    if not for_name:
        raise ValueError("Say who the code is for (--for).")
    p = Path(path).expanduser()
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    if not text.strip():
        text = _HEADER
    existing, _ = parse_codes(text)
    code = new_code()
    while code_key(code) in existing:
        code = new_code()
    if not text.endswith("\n"):
        text += "\n"
    lines = text.splitlines()
    indent = _item_indent(lines)
    if indent is None:
        indent = "  "
    inner = indent + "  "
    block = [f"{indent}- code: {code}", f"{inner}for: {_scalar(for_name)}",
             f"{inner}created: {today.isoformat()}",
             f"{inner}expires: {(today + timedelta(days=code_days())).isoformat()}"]
    if account:
        block.append(f"{inner}account: {_scalar(account)}")
    new_text = text + "\n".join(block) + "\n"
    entries, _ = parse_codes(new_text)
    added = entries.get(code_key(code))
    if added is None or added.for_name != for_name:
        raise ValueError(f"Could not add the code: {p} does not have the expected layout "
                         "(a 'codes:' list). The file was not changed.")
    _write_atomically(p, new_text)
    return code


def renew_entry(path: str, for_name: str, today: Optional[date] = None) -> date:
    """Give a person's code a new expiry date; the code stays the same (PC11)."""
    today = today or date.today()
    p = Path(path).expanduser()
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    start, end = _find_block(lines, for_name)
    new_expiry = today + timedelta(days=code_days())
    m = _ITEM_START.match(lines[start])
    key_indent = " " * (lines[start].index(m.group(2)))     # where "code:" / "for:" start
    for i in range(start, end):
        e = _EXPIRES_LINE.match(lines[i])
        if e and (i == start or len(e.group(1)) == len(key_indent)):
            if i == start:
                lines[i] = f"{m.group(1)}- expires: {new_expiry.isoformat()}"
            else:
                lines[i] = f"{key_indent}expires: {new_expiry.isoformat()}"
            break
    else:
        last = end
        # Step back over blank lines and comments, which belong to the next entry.
        while last > start + 1 and (not lines[last - 1].strip()
                                    or lines[last - 1].lstrip().startswith("#")):
            last -= 1
        lines.insert(last, f"{key_indent}expires: {new_expiry.isoformat()}")
    new_text = "\n".join(lines) + "\n"
    # Check the right person, and only them, was changed.
    before, _ = parse_codes(text)
    after, _ = parse_codes(new_text)
    want = " ".join(for_name.split()).lower()
    changed = {k for k in after if k not in before or after[k] != before[k]}
    target = [k for k, e in after.items() if e.for_name.lower() == want]
    if (len(target) != 1 or after[target[0]].expires != new_expiry
            or changed - set(target) or set(before) != set(after)):
        raise LookupError(f"Could not renew {for_name!r} safely; the file was not changed. "
                          "Edit its expires: line by hand.")
    _write_atomically(p, new_text)
    return new_expiry


def reset_pin(db, store: CodeStore, for_name: str) -> str:
    """Clear the PIN of the account a person's code belongs to (PC6). Their
    next sign-in with the code asks for a new PIN. Returns the user id."""
    want = " ".join(for_name.split()).lower()
    matches = [e for e in store.entries() if e.for_name.lower() == want]
    if not matches:
        raise LookupError(f"No access code for {for_name!r} in {store.path}.")
    users = {u for u in (bound_user(db, e) for e in matches) if u}
    if not users:
        raise LookupError(f"{for_name}'s code has not been used yet, so there is no PIN to reset.")
    if len(users) > 1:
        raise LookupError(f"{for_name!r} has codes for more than one account; edit by hand.")
    user_id = users.pop()
    # Also end open sessions: a reset usually means someone else may know the PIN.
    db.conn.execute("UPDATE users SET pin_hash = NULL, failed_logins = 0, locked_until = NULL, "
                    "session_epoch = COALESCE(session_epoch, 0) + 1 WHERE user_id = ?",
                    (user_id,))
    db.conn.commit()
    return user_id


def describe(db, store: CodeStore, today: Optional[date] = None) -> List[dict]:
    """Rows for `list`: every entry and its status (PC11)."""
    today = today or date.today()
    rows = []
    for e in store.entries():
        uid = bound_user_by_key(db, e.key) if db is not None else None
        status = e.status(today)
        if status == "active" and not uid and not e.account:
            status = "new (not used yet)"
        rows.append({"code": e.code, "for": e.for_name, "expires": e.expires.isoformat(),
                     "status": status, "account": e.account, "user_id": uid or ""})
    return rows


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="BioRx personal access codes")
    ap.add_argument("--file", default=None, help="codes file (default: ACCESS_CODES_FILE "
                                                 "or DATA_DIR/access_codes.yaml)")
    ap.add_argument("--db", default=None, help="database (default: same as the server)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="make a code for someone")
    a.add_argument("--for", dest="for_name", required=True)
    a.add_argument("--account", default="", help="existing account sign-in name to tie it to")
    a.add_argument("--user-id", default="", help="existing account id to tie it to (for "
                   "accounts with no sign-in name; see python -m src.accounts list)")
    sub.add_parser("renew", help="push a code's expiry out; same code").add_argument(
        "--for", dest="for_name", required=True)
    sub.add_parser("reset-pin", help="let someone choose a new PIN").add_argument(
        "--for", dest="for_name", required=True)
    sub.add_parser("list", help="show every code and its status")
    args = ap.parse_args(argv)
    path = args.file or codes_file_path()
    print(f"Codes file: {path}")

    if args.cmd == "add":
        if args.account:
            from .db import Database
            adb = Database(args.db) if args.db else Database()
            if adb.conn.execute("SELECT 1 FROM users WHERE login_name IS NOT NULL AND "
                                "lower(login_name) = lower(?)", (args.account,)).fetchone() is None:
                print(f"No account signs in as {args.account!r} in {adb.db_path}. Nothing added.")
                return 1
        if args.user_id:
            from .db import Database
            db = Database(args.db) if args.db else Database()
            if db.conn.execute("SELECT 1 FROM users WHERE user_id = ?",
                               (args.user_id,)).fetchone() is None:
                print(f"No account with id {args.user_id} in {db.db_path}. Nothing added.")
                return 1
        code = add_entry(path, args.for_name, account=args.account)
        if args.user_id:
            if not bind(db, code_key(code), args.user_id):
                print("Could not tie the code to that account.")
                return 1
            print(f"Tied to account {args.user_id}; they choose a PIN on first sign-in "
                  "if it has none.")
        print(f"Code for {args.for_name}: {code}  (expires in {code_days()} days)")
        return 0
    if args.cmd == "renew":
        try:
            print(f"{args.for_name}'s code now expires {renew_entry(path, args.for_name)}")
        except LookupError as e:
            print(e)
            return 1
        return 0

    from .db import Database
    db = Database(args.db) if args.db else Database()
    store = CodeStore(path)
    for w in store.current_warnings():
        print(f"WARNING: {w}")
    if args.cmd == "reset-pin":
        try:
            reset_pin(db, store, args.for_name)
        except LookupError as e:
            print(e)
            return 1
        print(f"{args.for_name} will choose a new PIN the next time they enter their code.")
        return 0
    for r in describe(db, store):
        extra = f"  account={r['account']}" if r["account"] else ""
        print(f"{r['code']:<16} {r['for']:<24} expires {r['expires']}  {r['status']}{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
