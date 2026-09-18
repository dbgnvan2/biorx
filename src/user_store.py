"""
Purpose: Persistence for web-app users, their saved filters, and their usage.
Spec:    docs/implementation_plan_2026-09-15.md#2.5, W3, W4, W6
Tests:   tests/web/test_user_store.py

Kept out of src/db.py, which already carries papers, summaries, bookmarks and
reference lists; adding a fifth concern there would make it a file with several
reasons to change (file-maintainability §1).

A user id is opaque and server-issued. The display name is a label with no
authority — it must never be the thing that selects whose API key is spent.
Every query is parameterised (security S2).
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
import sqlite3
from typing import Any, Dict, List, Optional

from .crypto import KeyEncryptionUnavailable, decrypt_key, encrypt_key, last4

logger = logging.getLogger(__name__)

USER_ID_BYTES = 16


def new_user_id() -> str:
    """A server-issued opaque id. Never derived from anything a user supplies."""
    return secrets.token_urlsafe(USER_ID_BYTES)


# ── Users ─────────────────────────────────────────────────────────────────────

def create_user(db, display_name: str = "") -> str:
    user_id = new_user_id()
    db.conn.execute(
        "INSERT INTO users (user_id, display_name) VALUES (?, ?)",
        (user_id, display_name.strip()[:100]),
    )
    db.conn.commit()
    return user_id


def get_user(db, user_id: str) -> Optional[Dict[str, Any]]:
    row = db.conn.execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


def touch_user(db, user_id: str) -> None:
    db.conn.execute(
        "UPDATE users SET last_seen_at = CURRENT_TIMESTAMP WHERE user_id = ?",
        (user_id,),
    )
    db.conn.commit()


def set_display_name(db, user_id: str, display_name: str) -> None:
    db.conn.execute(
        "UPDATE users SET display_name = ? WHERE user_id = ?",
        (display_name.strip()[:100], user_id),
    )
    db.conn.commit()


# ── Per-user API keys ─────────────────────────────────────────────────────────

def set_llm_key(db, user_id: str, provider: str, api_key: str) -> str:
    """Encrypt and store a user's key. Returns the last-4 fragment for display.

    Raises KeyEncryptionUnavailable when KEY_ENC_SECRET is absent, so the caller
    reports that storage is disabled rather than writing a plaintext key.
    """
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("refusing to store an empty key")
    blob = encrypt_key(api_key)          # raises if encryption is unavailable
    tail = last4(api_key)
    db.conn.execute(
        "UPDATE users SET llm_provider = ?, llm_key_ciphertext = ?, "
        "llm_key_last4 = ? WHERE user_id = ?",
        (provider, blob, tail, user_id),
    )
    db.conn.commit()
    logger.info("Stored an LLM key for user %s (provider=%s)", user_id, provider)
    return tail


def set_preferred_model(db, user_id: str, model: str) -> None:
    db.conn.execute(
        "UPDATE users SET preferred_model = ? WHERE user_id = ?",
        (model.strip()[:200], user_id),
    )
    db.conn.commit()


def clear_llm_key(db, user_id: str) -> None:
    db.conn.execute(
        "UPDATE users SET llm_provider = '', llm_key_ciphertext = NULL, "
        "llm_key_last4 = '' WHERE user_id = ?",
        (user_id,),
    )
    db.conn.commit()


def get_llm_key(db, user_id: str) -> tuple:
    """Return (provider, decrypted_key) for this user, or ("", "").

    A stored key that cannot be decrypted raises rather than returning "",
    because "" would look like "this user has no key" and silently spend the
    owner's credential instead (learnings P2/P14).
    """
    row = db.conn.execute(
        "SELECT llm_provider, llm_key_ciphertext FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row or not row["llm_key_ciphertext"]:
        return "", ""
    return row["llm_provider"] or "", decrypt_key(row["llm_key_ciphertext"])


# ── Usage, for the owner-key spend cap ────────────────────────────────────────

def record_usage(db, user_id: str, kind: str, provider: str, model: str,
                 key_source: str) -> int:
    """Record one billable action. Never stores a key or part of one."""
    cur = db.conn.execute(
        "INSERT INTO usage_events (user_id, kind, provider, model, key_source) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, kind, provider, model, key_source),
    )
    db.conn.commit()
    return int(cur.lastrowid)


def reserve_owner_usage(db, user_id: str, kind: str, cap: int,
                        provider: str = "", model: str = "") -> Optional[int]:
    """Claim one owner-key slot, atomically. Returns the row id, or None if the
    cap is already reached.

    Counting first and inserting afterwards is a check-then-act race: several
    requests arriving together all read the same count, all find room, and all
    proceed — which is exactly what a shared access code makes easy. A burst of
    six requests against a cap of three was measured passing six times.

    The insert and the count are therefore a single statement, so SQLite
    evaluates them under one write lock and only the first `cap` writers win.
    """
    if cap <= 0:
        return None
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    cur = db.conn.execute(
        "INSERT INTO usage_events (user_id, kind, provider, model, key_source) "
        "SELECT ?, ?, ?, ?, 'owner' WHERE ("
        "    SELECT COUNT(*) FROM usage_events "
        "    WHERE user_id = ? AND kind = ? AND key_source = 'owner' "
        "      AND created_at >= ?"
        ") < ?",
        (user_id, kind, provider, model, user_id, kind, since, cap),
    )
    db.conn.commit()
    if cur.rowcount != 1:
        return None
    return int(cur.lastrowid)


def release_usage(db, usage_id: int) -> None:
    """Give a reserved slot back.

    Used when a job fails before the provider was ever called, so a server-side
    problem does not consume a user's daily allowance.
    """
    db.conn.execute("DELETE FROM usage_events WHERE id = ?", (usage_id,))
    db.conn.commit()


def finalize_usage(db, usage_id: int, provider: str, model: str) -> None:
    """Fill in which provider and model the reserved slot actually used."""
    db.conn.execute(
        "UPDATE usage_events SET provider = ?, model = ? WHERE id = ?",
        (provider, model, usage_id),
    )
    db.conn.commit()


def owner_usage_today(db, user_id: str, kind: str = "summary") -> int:
    """How many owner-key actions this user has run in the last 24 hours."""
    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    row = db.conn.execute(
        "SELECT COUNT(*) AS n FROM usage_events "
        "WHERE user_id = ? AND kind = ? AND key_source = 'owner' AND created_at >= ?",
        (user_id, kind, since),
    ).fetchone()
    return int(row["n"]) if row else 0


# ── Saved filters, per user ───────────────────────────────────────────────────

def list_filters(db, user_id: str) -> List[Dict[str, Any]]:
    rows = db.conn.execute(
        "SELECT id, name, filter_json, enabled FROM user_filters "
        "WHERE user_id = ? ORDER BY name",
        (user_id,),
    ).fetchall()
    out = []
    for row in rows:
        try:
            payload = json.loads(row["filter_json"])
        except ValueError:
            logger.warning("Filter %s for user %s is not valid JSON — skipped",
                           row["id"], user_id)
            continue
        payload["id"] = row["id"]
        payload["name"] = row["name"]
        payload["enabled"] = bool(row["enabled"])
        out.append(payload)
    return out


def get_filter(db, user_id: str, filter_id: int) -> Optional[Dict[str, Any]]:
    row = db.conn.execute(
        "SELECT id, name, filter_json, enabled FROM user_filters "
        "WHERE user_id = ? AND id = ?",
        (user_id, filter_id),
    ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["filter_json"])
    except ValueError:
        return None
    payload["id"] = row["id"]
    payload["name"] = row["name"]
    payload["enabled"] = bool(row["enabled"])
    return payload


def upsert_filter(db, user_id: str, name: str, filter_dict: Dict[str, Any],
                  enabled: bool = True) -> int:
    """Create or replace a named filter for this user. Returns its id."""
    name = (name or "").strip()[:200] or "Untitled"
    payload = {k: v for k, v in filter_dict.items() if k not in ("id",)}
    db.conn.execute(
        "INSERT INTO user_filters (user_id, name, filter_json, enabled) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, name) DO UPDATE SET "
        "filter_json = excluded.filter_json, enabled = excluded.enabled, "
        "updated_at = CURRENT_TIMESTAMP",
        (user_id, name, json.dumps(payload), 1 if enabled else 0),
    )
    db.conn.commit()
    row = db.conn.execute(
        "SELECT id FROM user_filters WHERE user_id = ? AND name = ?",
        (user_id, name),
    ).fetchone()
    return int(row["id"])


def delete_filter(db, user_id: str, filter_id: int) -> bool:
    cur = db.conn.execute(
        "DELETE FROM user_filters WHERE user_id = ? AND id = ?",
        (user_id, filter_id),
    )
    db.conn.commit()
    return cur.rowcount > 0


def seed_filters_from_file(db, user_id: str, filters: List[Dict[str, Any]]) -> int:
    """Give a new user a copy of the shared filters.json as a starting point."""
    count = 0
    for f in filters:
        name = f.get("name")
        if not name:
            continue
        upsert_filter(db, user_id, name, f, enabled=bool(f.get("enabled", True)))
        count += 1
    return count


# ── Per-user reference lists ──────────────────────────────────────────────────

class DuplicateListName(ValueError):
    """The user already has a reference list with this name."""

def list_reference_lists(db, user_id: str) -> List[Dict[str, Any]]:
    rows = db.conn.execute(
        """
        SELECT l.id, l.name, l.created_at,
               COUNT(i.id) AS item_count
          FROM user_reference_lists l
     LEFT JOIN user_reference_list_items i ON i.list_id = l.id
         WHERE l.user_id = ?
      GROUP BY l.id
      ORDER BY l.created_at DESC
        """,
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_reference_list(db, user_id: str, list_id: int) -> Optional[Dict[str, Any]]:
    row = db.conn.execute(
        "SELECT id, name, created_at FROM user_reference_lists "
        "WHERE user_id = ? AND id = ?",
        (user_id, list_id),
    ).fetchone()
    return dict(row) if row else None


def _insert_reference_list(db, user_id: str, name: str) -> int:
    """Insert without committing. Raises DuplicateListName."""
    try:
        cur = db.conn.execute(
            "INSERT INTO user_reference_lists (user_id, name) VALUES (?, ?)",
            (user_id, name.strip()),
        )
    except sqlite3.IntegrityError as e:
        raise DuplicateListName(name) from e
    return cur.lastrowid


def create_reference_list(db, user_id: str, name: str) -> int:
    try:
        list_id = _insert_reference_list(db, user_id, name)
    except DuplicateListName:
        db.conn.rollback()
        raise
    db.conn.commit()
    return list_id


def delete_reference_list(db, user_id: str, list_id: int) -> None:
    """Delete a list and its items in one transaction.

    The items are deleted explicitly: the schema's ON DELETE CASCADE does
    nothing because SQLite leaves foreign keys off unless each connection
    enables them, and this codebase does not.
    """
    try:
        owned = db.conn.execute(
            "SELECT 1 FROM user_reference_lists WHERE user_id = ? AND id = ?",
            (user_id, list_id),
        ).fetchone()
        if owned:
            db.conn.execute(
                "DELETE FROM user_reference_list_items WHERE list_id = ?", (list_id,))
            db.conn.execute(
                "DELETE FROM user_reference_lists WHERE user_id = ? AND id = ?",
                (user_id, list_id),
            )
        db.conn.commit()
    except BaseException:
        db.conn.rollback()
        raise


def list_reference_items(db, list_id: int) -> List[Dict[str, Any]]:
    """Return items with their paper dicts joined in."""
    rows = db.conn.execute(
        """
        SELECT i.id AS item_id, i.added_at,
               p.id AS paper_id, p.title, p.authors, p.pub_date,
               p.doi, p.url, p.abstract, p.source, p.server,
               p.canonical_id, p.pdf_path, p.best_oa_url, p.version
          FROM user_reference_list_items i
          JOIN papers p ON p.id = i.paper_id
         WHERE i.list_id = ?
         ORDER BY i.added_at DESC
        """,
        (list_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        result.append({
            "item_id": d.pop("item_id"),
            "added_at": d.pop("added_at"),
            "paper": d,
        })
    return result


def get_reference_list_item(db, list_id: int, item_id: int) -> Optional[Dict[str, Any]]:
    row = db.conn.execute(
        "SELECT id AS item_id, list_id, paper_id, added_at "
        "FROM user_reference_list_items WHERE list_id = ? AND id = ?",
        (list_id, item_id),
    ).fetchone()
    return dict(row) if row else None


def get_reference_item_by_paper(db, list_id: int, paper_id: int) -> Optional[Dict[str, Any]]:
    row = db.conn.execute(
        "SELECT id AS item_id, list_id, paper_id, added_at "
        "FROM user_reference_list_items WHERE list_id = ? AND paper_id = ?",
        (list_id, paper_id),
    ).fetchone()
    return dict(row) if row else None


def add_reference_item(db, list_id: int, paper_id: int) -> int:
    """Insert paper into list; ignore if already present. Returns item id."""
    db.conn.execute(
        "INSERT OR IGNORE INTO user_reference_list_items (list_id, paper_id) "
        "VALUES (?, ?)",
        (list_id, paper_id),
    )
    db.conn.commit()
    row = db.conn.execute(
        "SELECT id FROM user_reference_list_items WHERE list_id = ? AND paper_id = ?",
        (list_id, paper_id),
    ).fetchone()
    return row["id"]


def remove_reference_item(db, list_id: int, item_id: int) -> None:
    db.conn.execute(
        "DELETE FROM user_reference_list_items WHERE list_id = ? AND id = ?",
        (list_id, item_id),
    )
    db.conn.commit()


def create_reference_list_with_papers(
    db, user_id: str, name: str, paper_ids: List[int]
) -> int:
    """Create a list and populate it in one transaction. Returns list_id.
    Raises DuplicateListName, leaving nothing behind."""
    try:
        list_id = _insert_reference_list(db, user_id, name)
        for pid in paper_ids:
            db.conn.execute(
                "INSERT OR IGNORE INTO user_reference_list_items (list_id, paper_id) "
                "VALUES (?, ?)",
                (list_id, pid),
            )
        db.conn.commit()
    except BaseException:
        db.conn.rollback()
        raise
    return list_id
