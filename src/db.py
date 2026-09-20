"""
SQLite database utilities for storing papers, summaries, bookmarks, and search history.

Threading: each thread gets its own connection (see the ``conn`` property). The
desktop app previously shared one connection across threads on the argument that
"writes are always serial (one worker at a time)" — true for a single-user GUI,
and false for the web app, where several request threads and job workers write
concurrently. WAL plus a busy timeout lets readers and one writer proceed
without blocking each other.
"""

import itertools
import re
import os
import sqlite3
import json
import threading
import weakref
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = "~/preprints"
DEFAULT_DB_PATH  = f"{DEFAULT_DATA_DIR}/biorxiv.db"


class _ConnectionHolder:
    """Owns one thread's connection. Its death is the signal to close it.

    The holder lives in threading.local storage, which CPython releases when the
    owning thread exits — for *any* kind of thread. That matters: the GUI runs
    its workers on QThreads, where threading.current_thread() returns a
    _DummyThread whose is_alive() stays True forever. Liveness detection based
    on the thread object therefore never fires in the application that needs it
    most. The holder's lifetime does not have that problem.
    """

    __slots__ = ("conn", "__weakref__")

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn


def _release_connection(conn, conns, lock, key) -> None:
    """Close a connection whose owning thread has gone. Module-level so the
    finalizer does not keep the Database alive."""
    with lock:
        conns.pop(key, None)
    try:
        conn.close()
    except sqlite3.Error:
        pass

DEFAULT_BUSY_TIMEOUT_MS = 10000


def busy_timeout_ms() -> int:
    """How long a writer waits for a competing write before raising "database is
    locked". Read at call time, not at import, so setting the env var after
    importing this module still takes effect (as with default_db_path()).
    """
    try:
        return int(os.environ.get("BIORX_DB_BUSY_TIMEOUT_MS", DEFAULT_BUSY_TIMEOUT_MS))
    except ValueError:
        logger.warning("BIORX_DB_BUSY_TIMEOUT_MS is not an integer — using default")
        return DEFAULT_BUSY_TIMEOUT_MS


def resolve_data_path(env_key: str, filename: str) -> str:
    """Resolve a writable data-file path: env_key → DATA_DIR/filename → ~/preprints/filename.

    Every location this app writes to must be overridable by environment, so a
    container, a test, or a second deployment never resolves to a developer's
    home directory (learnings P34).  All sibling resolvers (db path, cache path)
    share this function so a contract change in one is applied everywhere (P5).
    """
    explicit = os.environ.get(env_key)
    if explicit:
        return explicit
    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        return str(Path(data_dir) / filename)
    return f"{DEFAULT_DATA_DIR}/{filename}"


def default_db_path() -> str:
    """Resolve the database path: BIORX_DB_PATH, else DATA_DIR/biorxiv.db, else ~/preprints."""
    return resolve_data_path("BIORX_DB_PATH", "biorxiv.db")


def ensure_writable_directory(directory: Path) -> None:
    """Create the directory and confirm it is writable.

    Without this, an unwritable directory — the usual symptom of a volume
    mounted with the wrong ownership — surfaces later as a bare sqlite
    "unable to open database file", which says nothing about the cause.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise PermissionError(
            f"Cannot create the data directory {directory}: {e}. "
            "If this is a container, the mounted volume must be writable "
            "by the user the app runs as."
        ) from e

    if not os.access(directory, os.W_OK):
        raise PermissionError(
            f"The data directory {directory} is not writable by uid "
            f"{os.getuid()}. If this is a container, mount the volume "
            "writable by that user."
        )


class Database:
    """SQLite database for bioRxiv papers and metadata."""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file. Defaults to default_db_path().
        """
        self.db_path = Path(db_path or default_db_path()).expanduser()
        self._ensure_writable_directory(self.db_path.parent)
        self._local = threading.local()
        # key -> connection, so close() can shut every outstanding handle. Each
        # entry is removed by its own finalizer when the owning thread exits, so
        # a per-thread connection is not a file descriptor held for the life of
        # the process (learnings P30 — handing a resource out per thread is not
        # the same as giving it back). The key is a counter, since CPython reuses
        # thread idents after a thread exits.
        self._conns: Dict[int, Any] = {}
        self._conn_keys = itertools.count()
        self._conns_lock = threading.Lock()
        self._init_db()

    def _rollback_quietly(self) -> None:
        """Abandon the current transaction after a failed write.

        Without this, a failed INSERT leaves the connection inside a
        transaction holding SQLite's write lock, and every other writer blocks
        until the busy timeout expires. Harmless in the single-threaded desktop
        app, which is why it went unnoticed; in the web app it presents as a
        job that hangs on "Saving" and then fails for no visible reason.
        """
        try:
            self.conn.rollback()
        except sqlite3.Error as e:
            logger.debug("Rollback after a failed write also failed: %s", e)

    @staticmethod
    def _ensure_writable_directory(directory: Path) -> None:
        ensure_writable_directory(directory)

    # ── Connection handling ───────────────────────────────────────────────────

    def _new_connection(self) -> sqlite3.Connection:
        # sqlite3.connect(timeout=...) IS the busy timeout — it installs the
        # busy handler and is reported by `PRAGMA busy_timeout`. An additional
        # explicit PRAGMA here would be redundant, and a test asserting the
        # pragma would pass whether or not the pragma line existed.
        #
        # check_same_thread=False permits the reaper below to close a connection
        # from another thread. Each thread still gets its own connection — the
        # `conn` property never hands one thread's connection to another — this
        # only allows an already-dead thread's handle to be released.
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=busy_timeout_ms() / 1000,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        # WAL is a property of the file, not the connection, but setting it is
        # idempotent and cheap. It lets readers run while a writer holds the
        # write lock, which is what makes concurrent requests workable.
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.Error as e:          # e.g. a database on a network mount
            logger.warning("Could not enable WAL on %s: %s", self.db_path, e)
        return conn

    def release(self) -> None:
        """Close and forget this thread's connection, now.

        Dropping the holder takes its refcount to zero, which runs the finalizer
        immediately on CPython. Release happens automatically when the thread
        exits; call this where a worker's end is known (a GUI worker, a job
        runner, a request handler) so a pooled thread does not hold a handle
        between tasks.
        """
        self._local.holder = None

    @property
    def conn(self) -> sqlite3.Connection:
        """This thread's connection, created on first use.

        Exposed as a property so the ~30 existing ``self.conn.cursor()`` call
        sites keep working unchanged while each thread gets its own handle.
        """
        holder = getattr(self._local, "holder", None)
        if holder is None:
            conn = self._new_connection()
            key = next(self._conn_keys)
            holder = _ConnectionHolder(conn)
            self._local.holder = holder
            with self._conns_lock:
                self._conns[key] = conn
            # When the holder dies — because the thread exited, or because
            # release() dropped it — the connection is closed and unregistered.
            weakref.finalize(
                holder, _release_connection, conn, self._conns, self._conns_lock, key
            )
        return holder.conn

    def _init_db(self):
        """Create tables if they don't exist."""
        cursor = self.conn.cursor()

        # Papers table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                -- Nullable: arXiv and many PubMed/PsyArXiv records have no DOI.
                -- Identity is canonical_id (unique index added in migrations).
                doi TEXT UNIQUE,
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
                model_version TEXT DEFAULT '',
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
                preferred_model    TEXT      DEFAULT '',
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

        # Per-user reference lists (web app, multi-user; distinct from the
        # desktop-app reference_lists which have no user_id).
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_reference_lists (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                name       TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                UNIQUE(user_id, name),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_reference_list_items (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id  INTEGER NOT NULL
                             REFERENCES user_reference_lists(id) ON DELETE CASCADE,
                paper_id INTEGER NOT NULL REFERENCES papers(id),
                added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                UNIQUE(list_id, paper_id)
            )
        """)

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

        # N1: papers without a DOI could never be stored. Relax the old
        # constraint on existing databases, then make canonical_id the identity.
        self._relax_doi_not_null()
        self._ensure_canonical_id_index(cursor)

        # Provenance for a summary: who ran it, and which model produced it.
        # `summaries.paper_id` is UNIQUE, so one summary exists per paper and a
        # later run replaces it; these columns record whose run is current.
        self._add_column_if_missing(cursor, "summaries", "created_by_user_id", "TEXT")
        # What the entry was made from (plan 2026-09-19 FT1): "full_text" (a
        # model summary of the paper's text), "abstract" (no full text was
        # found, so the abstract stands in and no model ran), or "" for rows
        # from before this was recorded. text_source: where the text came from.
        self._add_column_if_missing(cursor, "summaries", "source_text", "TEXT DEFAULT ''")
        self._add_column_if_missing(cursor, "summaries", "text_source", "TEXT DEFAULT ''")
        self._add_column_if_missing(cursor, "users", "preferred_model", "TEXT DEFAULT ''")

        # What each billable call cost (plan 2026-09-20 M1.B.1). Rows written
        # before this migration keep tokens_counted = 0, which reads as "the
        # tokens for this call are unknown" — the truthful answer for them, and
        # the same answer given for a provider that reports nothing. It must
        # never be read as "this call was free".
        for col in ("prompt_tokens", "completion_tokens"):
            self._add_column_if_missing(cursor, "usage_events", col,
                                        "INTEGER DEFAULT 0")
        self._add_column_if_missing(cursor, "usage_events", "tokens_counted",
                                    "INTEGER DEFAULT 0")

        # Web accounts (docs/implementation_plan_2026-09-18_accounts.md): a
        # name + PIN that always return the same user; secrets stored hashed.
        for col, definition in (("login_name", "TEXT"), ("pin_hash", "TEXT"),
                                ("recovery_hash", "TEXT"),
                                ("failed_logins", "INTEGER DEFAULT 0"),
                                ("locked_until", "TEXT"), ("merged_into", "TEXT"),
                                # A random value in every session cookie; a new
                                # one on PIN reset, recovery or merge ends all
                                # cookies issued before it. NULL = never changed.
                                ("session_nonce", "TEXT")):
            self._add_column_if_missing(cursor, "users", col, definition)
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_login_name "
            "ON users(lower(login_name)) WHERE login_name IS NOT NULL"
        )
        # Personal access codes (docs/implementation_plan_2026-09-18_invite_codes.md):
        # the codes themselves live in access_codes.yaml; this records which
        # account each code belongs to. PRIMARY KEY makes first use atomic (PC9).
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS access_code_bindings (
                code_key TEXT PRIMARY KEY,
                user_id  TEXT NOT NULL,
                bound_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_access_code_bindings_user "
                       "ON access_code_bindings(user_id)")

    # SQLite cannot change a column constraint in place, so relaxing NOT NULL
    # means rebuilding the table. The rewrite targets exactly this declaration.
    _DOI_NOT_NULL = re.compile(r"\bdoi\s+TEXT\s+UNIQUE\s+NOT\s+NULL\b", re.IGNORECASE)

    def _relax_doi_not_null(self) -> None:
        """Rebuild `papers` without NOT NULL on `doi`, if an old schema has it.

        Spec:  docs/implementation_plan_2026-09-16_backlog.md#N1
        Tests: tests/test_n1_doi_less_papers.py

        The table's own CREATE statement is read back from sqlite_master and
        rewritten, so every column the additive migrations have added over time
        is carried across in its original order — nothing is re-declared from
        memory. Ids are copied explicitly, so summaries and bookmarks stay
        attached, and the AUTOINCREMENT high-water mark follows the rename.

        All of it runs in one transaction with a row-count check before commit.
        An unrecognised schema raises instead of guessing: a rebuild that
        guesses is how data gets lost.
        """
        info = {row[1]: row for row in self.conn.execute("PRAGMA table_info(papers)")}
        if "doi" not in info or info["doi"][3] == 0:
            return                                    # already nullable

        create_sql = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'papers'"
        ).fetchone()[0]
        if len(self._DOI_NOT_NULL.findall(create_sql)) != 1:
            raise RuntimeError(
                "Cannot migrate papers.doi to nullable: the table definition is not "
                "the expected 'doi TEXT UNIQUE NOT NULL' shape. Refusing to rebuild "
                "the table by guessing. Definition: " + create_sql[:300]
            )
        rebuilt_sql = self._DOI_NOT_NULL.sub("doi TEXT UNIQUE", create_sql)
        rebuilt_sql = re.sub(r"^\s*CREATE\s+TABLE\s+\"?papers\"?",
                             "CREATE TABLE papers_rebuild", rebuilt_sql,
                             count=1, flags=re.IGNORECASE)
        columns = ", ".join(f'"{name}"' for name in info)

        before = self.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        # AUTOINCREMENT's high-water mark lives in sqlite_sequence, keyed by
        # table name. Copying rows into a new table resets it to the highest
        # SURVIVING id, so a paper deleted from the top of the range would have
        # its id reused — and a new paper would inherit its summary or bookmark.
        seq_row = self.conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'papers'"
        ).fetchone()
        high_water = seq_row[0] if seq_row else 0
        logger.info("Migration N1: rebuilding papers (%d rows) to allow papers "
                    "without a DOI", before)

        previous_isolation = self.conn.isolation_level
        self.conn.isolation_level = None               # explicit transaction control
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.execute(rebuilt_sql)
            self.conn.execute(
                f"INSERT INTO papers_rebuild ({columns}) SELECT {columns} FROM papers"
            )
            self._verify_rebuild_counts(before)
            self.conn.execute("DROP TABLE papers")
            self.conn.execute("ALTER TABLE papers_rebuild RENAME TO papers")
            self.conn.execute(
                "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name = 'papers'",
                (high_water,),
            )
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            logger.error("Migration N1 failed and was rolled back; papers is unchanged")
            raise
        finally:
            self.conn.isolation_level = previous_isolation
        logger.info("Migration N1: papers rebuilt, %d rows preserved", before)

    def _verify_rebuild_counts(self, expected: int) -> None:
        """Abort the rebuild unless every row reached the new table."""
        copied = self.conn.execute("SELECT COUNT(*) FROM papers_rebuild").fetchone()[0]
        if copied != expected:
            raise RuntimeError(
                f"Migration N1 aborted: copied {copied} of {expected} papers"
            )

    def _ensure_canonical_id_index(self, cursor) -> None:
        """Make canonical_id the unique identity of a paper.

        A unique index permits any number of NULLs, so legacy rows without a
        canonical_id are unaffected. If existing rows already share an id, the
        index cannot be built; that is reported rather than crashing startup,
        and duplicates are still caught by the lookup in insert_paper.
        """
        duplicates = cursor.execute(
            "SELECT COUNT(*) FROM (SELECT canonical_id FROM papers "
            "WHERE canonical_id IS NOT NULL AND canonical_id <> '' "
            "GROUP BY canonical_id HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        if duplicates:
            logger.warning(
                "Not adding a unique index on papers.canonical_id: %d id(s) are "
                "shared by more than one row. Duplicates will be caught on insert "
                "instead.", duplicates,
            )
            return
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_canonical_id "
            "ON papers(canonical_id)"
        )

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

        doi = (paper.get("doi") or "").strip()
        canonical_id = paper.get("canonical_id") or (f"doi:{doi}" if doi else "")

        if not doi and not canonical_id:
            logger.warning(
                "Not storing %r: it has no DOI and no canonical_id, so a duplicate "
                "could never be detected", (paper.get("title") or "")[:80],
            )
            return None

        # The unique index on canonical_id normally rejects a duplicate. It can
        # be absent on a legacy database whose rows already shared an id, so
        # check explicitly rather than rely on it.
        if not doi and self.find_paper({"canonical_id": canonical_id}) is not None:
            logger.debug("Paper %s already exists", canonical_id)
            return None

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
            self._rollback_quietly()
            logger.debug("Paper %s already exists", doi or canonical_id)
            return None
        except sqlite3.Error as e:
            self._rollback_quietly()
            logger.error(f"Database error inserting paper: {e}")
            return None

    def insert_summary(
        self,
        paper_id: int,
        summary_text: str,
        key_findings: Optional[List[str]] = None,
        methodology: Optional[str] = None,
        conclusions: Optional[str] = None,
        model_version: str = "",
        created_by_user_id: Optional[str] = None,
        source_text: str = "",
        text_source: str = "",
    ) -> Optional[int]:
        """
        Insert or update a summary for a paper.

        Args:
            paper_id: ID of the paper
            summary_text: Full summary text
            key_findings: List of key findings
            methodology: Methodology summary
            conclusions: Conclusions summary
            model_version: LLM model that produced the summary. No default
                model is assumed: a row claiming "qwen:7b" for a summary some
                other model wrote is a false record.
            created_by_user_id: Web-app user whose run produced this summary
            source_text: "full_text", "abstract" (stand-in, no model), or "" (unknown)
            text_source: where the text came from, e.g. "Unpaywall", "OpenAlex"

        Returns:
            Summary ID if successful, None otherwise
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO summaries
                (paper_id, summary_text, key_findings, methodology,
                 conclusions, model_version, created_by_user_id,
                 source_text, text_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    paper_id,
                    summary_text,
                    json.dumps(key_findings) if key_findings else None,
                    methodology,
                    conclusions,
                    model_version,
                    created_by_user_id,
                    source_text,
                    text_source,
                ),
            )
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            self._rollback_quietly()
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

    def get_paper_by_canonical_id(self, canonical_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM papers WHERE canonical_id = ?", (canonical_id,)
        ).fetchone()
        return dict(row) if row else None

    def find_paper(self, paper: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The stored row for a paper, by DOI first and canonical_id second.

        Spec:  docs/implementation_plan_2026-09-16_backlog.md#N1
        Tests: tests/test_n1_doi_less_papers.py::test_n1_find_paper_falls_back_to_canonical_id
        """
        doi = (paper.get("doi") or "").strip()
        if doi:
            found = self.get_paper_by_doi(doi)
            if found:
                return found
        canonical_id = (paper.get("canonical_id") or "").strip()
        if canonical_id:
            return self.get_paper_by_canonical_id(canonical_id)
        return None

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
            self._rollback_quietly()
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
            self._rollback_quietly()
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
            self._rollback_quietly()
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
            self._rollback_quietly()
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
            self._rollback_quietly()
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
            self._rollback_quietly()
            logger.error(f"Error removing item from reference list: {e}")
            return False

    def close(self):
        """Close every connection this Database has handed out."""
        with self._conns_lock:
            conns = list(self._conns.values())
            self._conns = {}
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error as e:
                logger.debug("Error closing connection: %s", e)
        self._local = threading.local()
