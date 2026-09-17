"""
SQLite-backed cache for publication source responses.

SearchCache is implemented here but not yet wired into the orchestrator.
Wire it when caching is needed: pass a SearchCache instance into
SourceOrchestrator and call get_raw/set_raw around each adapter.search() call.
Every write location must honour an env override (BIORX_CACHE_PATH or DATA_DIR)
before it is wired, per the repo invariant in src/db.py.
"""

from __future__ import annotations
import os
import sqlite3
import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


def _parse_ts(s: str) -> datetime:
    """Parse an ISO timestamp; treat naive strings as UTC for backward compatibility."""
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# TTLs in hours
_TTL_RAW_SEARCH   = 24
_TTL_OA_LOOKUP    = 7 * 24
_TTL_ID_RESOLVE   = 30 * 24


def _default_cache_path() -> str:
    """Resolve cache path: BIORX_CACHE_PATH → DATA_DIR/source_cache.db → ~/preprints."""
    explicit = os.environ.get("BIORX_CACHE_PATH")
    if explicit:
        return explicit
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        return str(Path(data_dir) / "source_cache.db")
    return "~/preprints/source_cache.db"


class SearchCache:
    """SQLite-backed multi-layer cache for source API responses.

    Not yet wired into production (zero callers). See module docstring.
    """

    def __init__(self, cache_path: str | None = None):
        self.path = Path(cache_path or _default_cache_path()).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._init_tables()

    def _init_tables(self) -> None:
        cur = self._conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS raw_search_cache (
                query_hash TEXT NOT NULL,
                page       INTEGER NOT NULL,
                response   TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (query_hash, page)
            );
            CREATE TABLE IF NOT EXISTS oa_lookup_cache (
                doi        TEXT PRIMARY KEY,
                response   TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS id_resolution_cache (
                identifier      TEXT NOT NULL,
                id_type         TEXT NOT NULL,
                canonical_id    TEXT NOT NULL,
                fetched_at      TEXT NOT NULL,
                PRIMARY KEY (identifier, id_type)
            );
        """)
        self._conn.commit()

    # ── Raw search cache ───────────────────────────────────────────────────────

    def _query_hash(self, source: str, query: str, page: int) -> str:
        key = f"{source}:{query}:{page}"
        return hashlib.sha256(key.encode()).hexdigest()[:20]

    def get_raw(self, source: str, query: str, page: int) -> Optional[Any]:
        """Return cached raw API response or None if expired / not found."""
        h = self._query_hash(source, query, page)
        cur = self._conn.execute(
            "SELECT response, fetched_at FROM raw_search_cache WHERE query_hash=? AND page=?",
            (h, page),
        )
        row = cur.fetchone()
        if not row:
            return None
        fetched_at = _parse_ts(row[1])
        if datetime.now(timezone.utc) - fetched_at > timedelta(hours=_TTL_RAW_SEARCH):
            return None  # expired
        return json.loads(row[0])

    def set_raw(self, source: str, query: str, page: int, data: Any) -> None:
        """Store a raw API response."""
        h = self._query_hash(source, query, page)
        self._conn.execute(
            "INSERT OR REPLACE INTO raw_search_cache (query_hash, page, response, fetched_at) VALUES (?,?,?,?)",
            (h, page, json.dumps(data), datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    # ── OA lookup cache ────────────────────────────────────────────────────────

    def get_oa(self, doi: str) -> Optional[Dict[str, Any]]:
        """Return cached Unpaywall response or None if expired / not found."""
        cur = self._conn.execute(
            "SELECT response, fetched_at FROM oa_lookup_cache WHERE doi=?",
            (doi.lower().strip(),),
        )
        row = cur.fetchone()
        if not row:
            return None
        fetched_at = _parse_ts(row[1])
        if datetime.now(timezone.utc) - fetched_at > timedelta(hours=_TTL_OA_LOOKUP):
            return None
        return json.loads(row[0])

    def set_oa(self, doi: str, data: Dict[str, Any]) -> None:
        """Store an Unpaywall response."""
        self._conn.execute(
            "INSERT OR REPLACE INTO oa_lookup_cache (doi, response, fetched_at) VALUES (?,?,?)",
            (doi.lower().strip(), json.dumps(data), datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    # ── ID resolution cache ────────────────────────────────────────────────────

    def get_id_resolution(self, identifier: str, id_type: str) -> Optional[str]:
        """Return cached canonical_id for an identifier, or None if expired."""
        cur = self._conn.execute(
            "SELECT canonical_id, fetched_at FROM id_resolution_cache WHERE identifier=? AND id_type=?",
            (identifier, id_type),
        )
        row = cur.fetchone()
        if not row:
            return None
        fetched_at = _parse_ts(row[1])
        if datetime.now(timezone.utc) - fetched_at > timedelta(hours=_TTL_ID_RESOLVE):
            return None
        return row[0]

    def set_id_resolution(self, identifier: str, id_type: str, canonical_id: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO id_resolution_cache (identifier, id_type, canonical_id, fetched_at) VALUES (?,?,?,?)",
            (identifier, id_type, canonical_id, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
