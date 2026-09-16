"""
N1 — a paper without a DOI could never be stored.

Spec:  docs/implementation_plan_2026-09-16_backlog.md#N1
Found: live smoke test, 2026-09-16. `papers.doi` was TEXT UNIQUE NOT NULL and
insert_paper() wrote `doi or None`, so every DOI-less record failed the NOT NULL
constraint and returned None. Every arXiv record has an empty DOI, so an arXiv
summary was billed and never saved.

Identity moves to canonical_id, which every adapter sets. The migration rebuilds
the table on existing databases, which is the step most likely to lose data —
so it is tested against a populated, old-schema database with an exact
before/after comparison (learnings P8, P9).
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.db import Database

OLD_PAPERS_SQL = """
CREATE TABLE papers (
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

# Real scale: the live database holds 2,623 papers (measured 2026-09-16).
REAL_SCALE_ROWS = 2700


def _old_schema_db(path: Path, rows: int = REAL_SCALE_ROWS) -> None:
    """Build a database exactly as the pre-N1 code left it, with the columns the
    old additive migrations had added, populated at real scale, plus summaries
    and bookmarks that reference paper ids."""
    conn = sqlite3.connect(str(path))
    conn.execute(OLD_PAPERS_SQL)
    for col, definition in [("canonical_id", "TEXT"), ("pmid", "TEXT"),
                            ("source", "TEXT DEFAULT 'biorxiv'")]:
        conn.execute(f"ALTER TABLE papers ADD COLUMN {col} {definition}")
    conn.execute("""CREATE TABLE summaries (id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id INTEGER UNIQUE NOT NULL, summary_text TEXT, key_findings TEXT,
        methodology TEXT, conclusions TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        model_version TEXT DEFAULT 'qwen:7b', FOREIGN KEY (paper_id) REFERENCES papers(id))""")
    conn.execute("""CREATE TABLE bookmarks (id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id INTEGER UNIQUE NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (paper_id) REFERENCES papers(id))""")
    conn.executemany(
        "INSERT INTO papers (doi, title, abstract, canonical_id, source) VALUES (?, ?, ?, ?, ?)",
        [(f"10.1234/p{i}", f"Paper {i}", f"Abstract {i}", f"doi:10.1234/p{i}", "europepmc")
         for i in range(rows)],
    )
    # Delete a few in the middle so ids are not contiguous and the AUTOINCREMENT
    # high-water mark is above the highest surviving id.
    conn.execute("DELETE FROM papers WHERE id IN (5, 6, 7)")
    conn.execute(f"DELETE FROM papers WHERE id = {rows}")
    conn.execute("INSERT INTO summaries (paper_id, conclusions) VALUES (10, 'kept')")
    conn.execute("INSERT INTO bookmarks (paper_id) VALUES (20)")
    conn.commit()
    conn.close()


def _snapshot(conn):
    return conn.execute(
        "SELECT id, doi, title, abstract, canonical_id, source FROM papers ORDER BY id"
    ).fetchall()


# ── Highest impact first: the migration on a populated old-schema database ────

def test_n1_migration_preserves_every_row_on_a_populated_db(tmp_path):
    path = tmp_path / "old.db"
    _old_schema_db(path)
    before_conn = sqlite3.connect(str(path))
    before = _snapshot(before_conn)
    before_conn.close()
    assert len(before) == REAL_SCALE_ROWS - 4

    db = Database(str(path))
    after = [tuple(r) for r in _snapshot(db.conn)]

    assert after == [tuple(r) for r in before], "rows changed during the rebuild"
    doi_col = [r for r in db.conn.execute("PRAGMA table_info(papers)") if r[1] == "doi"][0]
    assert doi_col[3] == 0, "doi is still NOT NULL after migration"
    db.close()


def test_n1_migration_keeps_summaries_and_bookmarks_attached(tmp_path):
    path = tmp_path / "old.db"
    _old_schema_db(path)
    db = Database(str(path))
    row = db.conn.execute(
        "SELECT p.title FROM summaries s JOIN papers p ON p.id = s.paper_id"
    ).fetchone()
    assert row["title"] == "Paper 9"          # id 10 → the tenth inserted paper
    assert db.conn.execute(
        "SELECT p.title FROM bookmarks b JOIN papers p ON p.id = b.paper_id"
    ).fetchone()["title"] == "Paper 19"
    db.close()


def test_n1_migration_does_not_reuse_old_paper_ids(tmp_path):
    """
    AUTOINCREMENT's high-water mark must survive the rebuild. If it reset, a new
    paper could take the id of a deleted one — and inherit a summary or bookmark
    that pointed at it.
    """
    path = tmp_path / "old.db"
    _old_schema_db(path)
    deleted_top_id = REAL_SCALE_ROWS
    db = Database(str(path))
    new_id = db.insert_paper({"doi": "10.9/new", "title": "New", "canonical_id": "doi:10.9/new"})
    assert new_id > deleted_top_id, f"id {new_id} reuses the pre-migration range"
    db.close()


def test_n1_migration_is_idempotent(tmp_path):
    path = tmp_path / "old.db"
    _old_schema_db(path, rows=50)
    Database(str(path)).close()
    second = Database(str(path))
    assert second.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 46
    second.close()


def test_n1_migration_runs_inside_one_transaction(tmp_path, monkeypatch):
    """A failure half-way through the rebuild must leave the old table intact."""
    path = tmp_path / "old.db"
    _old_schema_db(path, rows=50)

    real_verify = Database._verify_rebuild_counts

    def boom(self, *args, **kwargs):
        raise RuntimeError("simulated failure mid-rebuild")

    monkeypatch.setattr(Database, "_verify_rebuild_counts", boom)
    with pytest.raises(RuntimeError):
        Database(str(path))
    monkeypatch.setattr(Database, "_verify_rebuild_counts", real_verify)

    conn = sqlite3.connect(str(path))
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "papers" in names and "papers_rebuild" not in names
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 46
    doi_col = [r for r in conn.execute("PRAGMA table_info(papers)") if r[1] == "doi"][0]
    assert doi_col[3] == 1, "a failed rebuild must not leave a half-migrated schema"
    conn.close()


def test_n1_rebuild_refuses_an_unrecognised_schema(tmp_path, caplog):
    """
    The rebuild rewrites the table's CREATE statement. If that statement is not
    the shape the rewrite expects, guessing is how data gets lost: it must stop
    and say so, and leave the table untouched.
    """
    path = tmp_path / "odd.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE papers (id INTEGER PRIMARY KEY, doi TEXT NOT NULL UNIQUE, "
                 "title TEXT NOT NULL)")
    conn.execute("INSERT INTO papers (doi, title) VALUES ('10.1/x', 'x')")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError) as excinfo:
        Database(str(path))
    assert "doi" in str(excinfo.value)


# ── Behaviour ─────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "new.db"))
    yield d
    d.close()


def test_n1_two_doi_less_papers_are_both_stored(db):
    a = db.insert_paper({"doi": "", "title": "arXiv one", "canonical_id": "arxiv:2609.00001"})
    b = db.insert_paper({"doi": "", "title": "arXiv two", "canonical_id": "arxiv:2609.00002"})
    assert a is not None and b is not None and a != b
    assert db.conn.execute("SELECT COUNT(*) FROM papers WHERE doi IS NULL").fetchone()[0] == 2


def test_n1_a_duplicate_canonical_id_is_still_rejected(db):
    """Removing NOT NULL must not remove deduplication for DOI-less papers."""
    assert db.insert_paper({"title": "arXiv one", "canonical_id": "arxiv:2609.00001"})
    assert db.insert_paper({"title": "arXiv one again", "canonical_id": "arxiv:2609.00001"}) is None


def test_n1_a_duplicate_doi_is_still_rejected(db):
    assert db.insert_paper({"doi": "10.1/a", "title": "A", "canonical_id": "doi:10.1/a"})
    assert db.insert_paper({"doi": "10.1/a", "title": "A2", "canonical_id": "x:other"}) is None


def test_n1_a_paper_with_neither_identifier_is_refused_loudly(db, caplog):
    """No DOI and no canonical_id means duplicates could never be detected."""
    with caplog.at_level("WARNING"):
        assert db.insert_paper({"title": "Unidentifiable"}) is None
    assert any("no DOI and no canonical_id" in r.getMessage() for r in caplog.records)
    assert db.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 0


def test_n1_find_paper_falls_back_to_canonical_id(db):
    pid = db.insert_paper({"title": "arXiv one", "canonical_id": "arxiv:2609.00001"})
    assert db.find_paper({"doi": "", "canonical_id": "arxiv:2609.00001"})["id"] == pid
    assert db.find_paper({"canonical_id": "arxiv:nope"}) is None
    assert db.find_paper({}) is None


def test_n1_find_paper_prefers_doi(db):
    pid = db.insert_paper({"doi": "10.1/a", "title": "A", "canonical_id": "doi:10.1/a"})
    assert db.find_paper({"doi": "10.1/a", "canonical_id": "something-else"})["id"] == pid


def test_n1_a_real_arxiv_record_round_trips_through_the_database(db):
    """Built by the real adapter, not a hand-made dict."""
    from src.sources.arxiv import ArxivAdapter
    record = ArxivAdapter().normalize({
        "arxiv_id_full": "2609.01234v2", "title": "Generative agents at scale",
        "abstract": "We study…", "authors": ["A. Park"], "published": "2026-09-10",
        "categories": ["cs.AI"],
    }).to_dict()
    assert record["doi"] == ""

    pid = db.insert_paper(record)
    assert pid is not None
    assert db.find_paper(record)["id"] == pid


def test_n1_canonical_id_is_uniquely_indexed_on_a_clean_database(db):
    indexes = {row[1]: row[2] for row in db.conn.execute("PRAGMA index_list(papers)")}
    assert indexes.get("idx_papers_canonical_id") == 1, indexes


def test_n1_legacy_duplicate_ids_are_reported_and_still_deduplicated(tmp_path, caplog):
    """
    A legacy database whose rows already share a canonical_id cannot take the
    unique index. Startup must survive that, say so, and still refuse to add a
    third copy — which the index would otherwise have done.
    """
    path = tmp_path / "legacy-dupes.db"
    _old_schema_db(path, rows=10)
    conn = sqlite3.connect(str(path))
    conn.execute("UPDATE papers SET canonical_id = 'shared:1' WHERE id IN (1, 2)")
    conn.commit()
    conn.close()

    with caplog.at_level("WARNING"):
        db = Database(str(path))
    assert any("shared by more than one row" in r.getMessage() for r in caplog.records)
    indexes = {row[1] for row in db.conn.execute("PRAGMA index_list(papers)")}
    assert "idx_papers_canonical_id" not in indexes

    assert db.insert_paper({"title": "third copy", "canonical_id": "shared:1"}) is None
    assert db.conn.execute(
        "SELECT COUNT(*) FROM papers WHERE canonical_id = 'shared:1'").fetchone()[0] == 2
    db.close()


def test_n1_a_new_database_is_created_nullable_without_a_rebuild(tmp_path, caplog):
    """The rebuild is for old databases only; a fresh one must not pay for it."""
    with caplog.at_level("INFO"):
        d = Database(str(tmp_path / "fresh.db"))
    assert not any("rebuilding papers" in r.getMessage() for r in caplog.records)
    doi_col = [r for r in d.conn.execute("PRAGMA table_info(papers)") if r[1] == "doi"][0]
    assert doi_col[3] == 0
    d.close()


def test_n1_a_failed_rebuild_releases_the_write_lock(tmp_path, monkeypatch):
    """
    The rebuild takes BEGIN IMMEDIATE. If a failure skipped ROLLBACK, the lock
    would stay held for as long as anything referenced the connection — the
    exception's own traceback is enough — and every other writer would block
    until the busy timeout: the same failure as the handlers that never rolled
    back (fixed 2026-09-16).
    """
    path = tmp_path / "old.db"
    _old_schema_db(path, rows=20)

    def boom(self, *args, **kwargs):
        raise RuntimeError("simulated failure mid-rebuild")

    monkeypatch.setattr(Database, "_verify_rebuild_counts", boom)
    with pytest.raises(RuntimeError) as excinfo:
        Database(str(path))
    assert excinfo.value is not None         # the traceback is still referenced here

    other = sqlite3.connect(str(path), timeout=1)
    try:
        other.execute("BEGIN IMMEDIATE")     # needs the write lock
        other.execute("UPDATE papers SET title = title WHERE id = 1")
        other.execute("COMMIT")
    finally:
        other.close()
