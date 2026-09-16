"""
SQLite database utilities for storing papers, summaries, bookmarks, and search history.

Threading: each thread gets its own connection (see the ``conn`` property). The
desktop app previously shared one connection across threads on the argument that
"writes are always serial (one worker at a time)" — true for a single-user GUI,
and false for the web app, where several request threads and job workers write
concurrently. WAL plus a busy timeout lets readers and one writer proceed
without blocking each other.
"""

import os
import sqlite3
import json
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "~/preprints/biorxiv.db"

# How long a writer waits for a competing write before raising "database is
# locked". Named and env-tunable rather than left at SQLite's 0 ms default.
BUSY_TIMEOUT_MS = int(os.environ.get("BIORX_DB_BUSY_TIMEOUT_MS", "10000"))


def default_db_path() -> str:
    """Resolve the database path: BIORX_DB_PATH, else DATA_DIR/biorxiv.db, else ~.

    Every location this app writes to must be overridable by environment, so a
    container, a test, or a second deployment never resolves to a developer's
    home directory (learnings P34).
    """
    explicit = os.environ.get("BIORX_DB_PATH")
    if explicit:
        return explicit
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        return str(Path(data_dir) / "biorxiv.db")
    return DEFAULT_DB_PATH


class Database:
    """SQLite database for bioRxiv papers and metadata."""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file. Defaults to default_db_path().
        """
        self.db_path = Path(db_path or default_db_path()).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._all_conns: List[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._init_db()

    # ── Connection handling ───────────────────────────────────────────────────

    def _new_connection(self) -> sqlite3.Connection:
        # sqlite3.connect(timeout=...) IS the busy timeout — it installs the
        # busy handler and is reported by `PRAGMA busy_timeout`. An additional
        # explicit PRAGMA here would be redundant, and a test asserting the
        # pragma would pass whether or not the pragma line existed.
        conn = sqlite3.connect(str(self.db_path), timeout=BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        # WAL is a property of the file, not the connection, but setting it is
        # idempotent and cheap. It lets readers run while a writer holds the
        # write lock, which is what makes concurrent requests workable.
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.Error as e:          # e.g. a database on a network mount
            logger.warning("Could not enable WAL on %s: %s", self.db_path, e)
        with self._conns_lock:
            self._all_conns.append(conn)
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        """This thread's connection, created on first use.

        Exposed as a property so the ~30 existing ``self.conn.cursor()`` call
        sites keep working unchanged while each thread gets its own handle.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_connection()
            self._local.conn = conn
        return conn

    def _init_db(self):
        """Create tables if they don't exist."""
        cursor = self.conn.cursor()

        # Papers table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doi TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                authors TEXT,
                abstract TEXT,
                pub_date TEXT,
                category TEXT,
                url TEXT,
                version INTEGER DEFAULT 1,
                license TEXT,
                server TEXT DEFAULT 'biorxiv',
                downloaded BOOLEAN DEFAULT FALSE,
                pdf_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Summaries table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id INTEGER UNIQUE NOT NULL,
                summary_text TEXT,
                key_findings TEXT,
                methodology TEXT,
                conclusions TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                model_version TEXT DEFAULT 'qwen:7b',
                FOREIGN KEY (paper_id) REFERENCES papers(id)
            )
        """
        )

        # Bookmarks table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_id INTEGER UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (paper_id) REFERENCES papers(id)
            )
        """
        )

        # Search history table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                search_params TEXT,
                results_count INTEGER,
                searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Reference lists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reference_lists (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT DEFAULT '',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reference_list_items (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id    INTEGER NOT NULL,
                doi        TEXT,
                paper_data TEXT NOT NULL,
                added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (list_id) REFERENCES reference_lists(id) ON DELETE CASCADE,
                UNIQUE(list_id, doi)
            )
        """)

        # ── Web app tables (docs/implementation_plan_2026-09-15.md#2.5) ──────
        # A user is a server-issued opaque id bound to a signed cookie. The
        # display name is a label with no authority: it must never be the thing
        # that selects whose API key is used.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id            TEXT PRIMARY KEY,
                display_name       TEXT      DEFAULT '',
                llm_provider       TEXT      DEFAULT '',
                llm_key_ciphertext BLOB,
                llm_key_last4      TEXT      DEFAULT '',
                created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_filters (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                name        TEXT NOT NULL,
                filter_json TEXT NOT NULL,
                enabled     BOOLEAN   DEFAULT 1,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, name),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        # One row per billable action, so the owner-key spend cap can be counted
        # and attributed. Never stores a key or any part of one.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usage_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                kind       TEXT NOT NULL,
                provider   TEXT DEFAULT '',
                model      TEXT DEFAULT '',
                key_source TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_user_created "
            "ON usage_events(user_id, created_at)"
        )

        self.conn.commit()
        self._run_migrations(cursor)
        self.conn.commit()
        logger.info(f"Database initialized: {self.db_path}")

    def _run_migrations(self, cursor):
        """Additive migrations — safe to run on every startup."""
        new_cols = [
            ("canonical_id",       "TEXT"),
            ("pmid",               "TEXT"),
            ("pmcid",              "TEXT"),
            ("document_type",      "TEXT DEFAULT 'preprint'"),
            ("is_preprint",        "BOOLEAN DEFAULT TRUE"),
            ("journal_or_server",  "TEXT"),
            ("best_oa_url",        "TEXT"),
            ("oa_status",          "TEXT"),
            ("source_hits",        "TEXT"),
            ("flags",              "TEXT DEFAULT '{}'"),
            ("source_trust_weight","REAL DEFAULT 0.75"),
            ("source",             "TEXT DEFAULT 'biorxiv'"),
        ]
        for col, definition in new_cols:
            self._add_column_if_missing(cursor, "papers", col, definition)

        # Provenance for a summary: who ran it, and which model produced it.
        # `summaries.paper_id` is UNIQUE, so one summary exists per paper and a
        # later run replaces it; these columns record whose run is current.
        self._add_column_if_missing(cursor, "summaries", "created_by_user_id", "TEXT")

    @staticmethod
    def _add_column_if_missing(cursor, table: str, column: str, definition: str) -> None:
        """Add a column, skipping it when already present.

        Reads the table's own schema rather than catching every exception from
        ALTER TABLE — a bare except there makes a genuine migration failure
        indistinguishable from "column already exists" (learnings P2).
        """
        existing = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
        if column in existing:
            return
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        logger.info("Migration: added %s.%s", table, column)

    def insert_paper(self, paper: Dict[str, Any]) -> Optional[int]:
        """
        Insert a paper into the database.

        Args:
            paper: Dictionary with paper fields

        Returns:
            Paper ID if successful, None if duplicate DOI
        """
        import json as _json
        # Normalize authors to a JSON string
        authors = paper.get("authors", "")
        if isinstance(authors, list):
            authors_str = _json.dumps(authors)
        else:
            authors_str = str(authors or "")

        doi = paper.get("doi") or ""
        canonical_id = paper.get("canonical_id") or (f"doi:{doi}" if doi else "")

        try:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO papers
                (doi, canonical_id, title, authors, abstract, pub_date, category, url,
                 version, license, server, source, pmid, pmcid, document_type,
                 is_preprint, journal_or_server, best_oa_url, oa_status,
                 source_hits, flags, source_trust_weight)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    doi or None,
                    canonical_id or None,
                    paper.get("title"),
                    authors_str,
                    paper.get("abstract"),
                    paper.get("pub_date") or paper.get("date"),
                    paper.get("category"),
                    paper.get("url") or paper.get("source_url"),
                    paper.get("version", 1),
                    paper.get("license"),
                    paper.get("server", paper.get("source", "biorxiv")),
                    paper.get("source", paper.get("server", "biorxiv")),
                    paper.get("pmid"),
                    paper.get("pmcid"),
                    paper.get("document_type", paper.get("type", "preprint")),
                    paper.get("is_preprint", True),
                    paper.get("journal_or_server"),
                    paper.get("best_oa_url"),
                    paper.get("oa_status"),
                    _json.dumps(paper.get("source_hits", [])),
                    _json.dumps(paper.get("flags", {})),
                    paper.get("source_trust_weight", 0.75),
                ),
            )
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            logger.debug(f"Paper with DOI {doi} already exists")
            return None
        except sqlite3.Error as e:
            logger.error(f"Database error inserting paper: {e}")
            return None

    def insert_summary(
        self,
        paper_id: int,
        summary_text: str,
        key_findings: Optional[List[str]] = None,
        methodology: Optional[str] = None,
        conclusions: Optional[str] = None,
        model_version: str = "qwen:7b",
    ) -> Optional[int]:
        """
        Insert or update a summary for a paper.

        Args:
            paper_id: ID of the paper
            summary_text: Full summary text
            key_findings: List of key findings
            methodology: Methodology summary
            conclusions: Conclusions summary
            model_version: LLM model version used

        Returns:
            Summary ID if successful, None otherwise
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO summaries
                (paper_id, summary_text, key_findings, methodology,
                 conclusions, model_version)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    paper_id,
                    summary_text,
                    json.dumps(key_findings) if key_findings else None,
                    methodology,
                    conclusions,
                    model_version,
                ),
            )
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            logger.error(f"Database error inserting summary: {e}")
            return None

    def get_unsummarized_papers(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get papers that don't have summaries yet.

        Args:
            limit: Maximum number of papers to return

        Returns:
            List of paper dictionaries
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT p.* FROM papers p
            LEFT JOIN summaries s ON p.id = s.paper_id
            WHERE s.id IS NULL AND p.downloaded = TRUE
            LIMIT ?
        """,
            (limit,),
        )

        papers = []
        for row in cursor.fetchall():
            papers.append(dict(row))

        return papers

    def get_paper_by_doi(self, doi: str) -> Optional[Dict[str, Any]]:
        """
        Get a paper by DOI.

        Args:
            doi: Paper DOI

        Returns:
            Paper dictionary or None
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM papers WHERE doi = ?", (doi,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_paper_by_id(self, paper_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a paper by ID.

        Args:
            paper_id: Paper database ID

        Returns:
            Paper dictionary or None
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM papers WHERE id = ?", (paper_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_paper_path(self, paper_id: int, pdf_path: str) -> bool:
        """
        Update PDF path for a paper.

        Args:
            paper_id: Paper database ID
            pdf_path: Path to downloaded PDF

        Returns:
            True if successful
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                UPDATE papers
                SET pdf_path = ?, downloaded = TRUE, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (pdf_path, paper_id),
            )
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Database error updating paper path: {e}")
            return False

    def get_summary(self, paper_id: int) -> Optional[Dict[str, Any]]:
        """
        Get summary for a paper.

        Args:
            paper_id: Paper database ID

        Returns:
            Summary dictionary or None
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM summaries WHERE paper_id = ?", (paper_id,))
        row = cursor.fetchone()
        if row:
            result = dict(row)
            # Parse JSON fields
            if result.get("key_findings"):
                result["key_findings"] = json.loads(result["key_findings"])
            return result
        return None

    def bookmark_paper(self, paper_id: int) -> bool:
        """
        Bookmark a paper.

        Args:
            paper_id: Paper database ID

        Returns:
            True if successful
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO bookmarks (paper_id) VALUES (?)", (paper_id,)
            )
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Database error bookmarking paper: {e}")
            return False

    def get_bookmarked_papers(self) -> List[Dict[str, Any]]:
        """
        Get all bookmarked papers.

        Returns:
            List of paper dictionaries
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT p.* FROM papers p
            INNER JOIN bookmarks b ON p.id = b.paper_id
            ORDER BY b.created_at DESC
        """
        )

        papers = []
        for row in cursor.fetchall():
            papers.append(dict(row))

        return papers

    # ── Reference lists ───────────────────────────────────────────────────────

    def create_reference_list(self, name: str, description: str = "") -> Optional[int]:
        """Create a named reference list and return its id."""
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO reference_lists (name, description) VALUES (?, ?)",
                (name, description),
            )
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            logger.error(f"Error creating reference list: {e}")
            return None

    def add_to_reference_list(self, list_id: int, paper: Dict[str, Any]) -> bool:
        """Add a paper (full dict) to a reference list. Silently skips duplicates."""
        doi = paper.get("doi") or None
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO reference_list_items (list_id, doi, paper_data) VALUES (?, ?, ?)",
                (list_id, doi, json.dumps(paper)),
            )
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error(f"Error adding to reference list: {e}")
            return False

    def get_reference_lists(self) -> List[Dict[str, Any]]:
        """Return all reference lists with item counts."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT rl.id, rl.name, rl.description, rl.created_at,
                   COUNT(rli.id) AS item_count
            FROM reference_lists rl
            LEFT JOIN reference_list_items rli ON rl.id = rli.list_id
            GROUP BY rl.id
            ORDER BY rl.created_at DESC
        """)
        return [dict(r) for r in cursor.fetchall()]

    def get_reference_list_items(self, list_id: int) -> List[Dict[str, Any]]:
        """Return papers in a reference list, each with a parsed 'paper' key."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM reference_list_items WHERE list_id = ? ORDER BY added_at",
            (list_id,),
        )
        result = []
        for row in cursor.fetchall():
            d = dict(row)
            d["paper"] = json.loads(d["paper_data"])
            result.append(d)
        return result

    def delete_reference_list(self, list_id: int) -> bool:
        """Delete a reference list and all its items."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("DELETE FROM reference_list_items WHERE list_id = ?", (list_id,))
            cursor.execute("DELETE FROM reference_lists WHERE id = ?", (list_id,))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Error deleting reference list: {e}")
            return False

    def remove_from_reference_list(self, item_id: int) -> bool:
        """Remove a single paper from a reference list by its row id."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("DELETE FROM reference_list_items WHERE id = ?", (item_id,))
            self.conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Error removing item from reference list: {e}")
            return False

    def close(self):
        """Close every connection this Database has handed out."""
        with self._conns_lock:
            conns, self._all_conns = self._all_conns, []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error as e:
                logger.debug("Error closing connection: %s", e)
        self._local = threading.local()
