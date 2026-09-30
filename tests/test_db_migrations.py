"""
Opening an old database with Database() brings it up to date without losing rows.

Spec: docs/implementation_plan_2026-09-28_review_fixes.md#T2, #M30

The schema below is what src/db.py at 82d524a (the last change on 2026-09-15)
created, dumped from sqlite_master. It is built with raw sqlite3 so no current
code runs until the test opens it.
"""
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import user_store
from src.db import Database

SCHEMA_2026_09_15 = """
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
, canonical_id TEXT, pmid TEXT, pmcid TEXT, document_type TEXT DEFAULT 'preprint', is_preprint BOOLEAN DEFAULT TRUE, journal_or_server TEXT, best_oa_url TEXT, oa_status TEXT, source_hits TEXT, flags TEXT DEFAULT '{}', source_trust_weight REAL DEFAULT 0.75, source TEXT DEFAULT 'biorxiv');
CREATE TABLE summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER UNIQUE NOT NULL,
    summary_text TEXT,
    key_findings TEXT,
    methodology TEXT,
    conclusions TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    model_version TEXT DEFAULT 'qwen:7b', created_by_user_id TEXT,
    FOREIGN KEY (paper_id) REFERENCES papers(id)
);
CREATE TABLE bookmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id) REFERENCES papers(id)
);
CREATE TABLE search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_params TEXT,
    results_count INTEGER,
    searched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE reference_lists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE reference_list_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id    INTEGER NOT NULL,
    doi        TEXT,
    paper_data TEXT NOT NULL,
    added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (list_id) REFERENCES reference_lists(id) ON DELETE CASCADE,
    UNIQUE(list_id, doi)
);
CREATE TABLE users (
    user_id            TEXT PRIMARY KEY,
    display_name       TEXT      DEFAULT '',
    llm_provider       TEXT      DEFAULT '',
    llm_key_ciphertext BLOB,
    llm_key_last4      TEXT      DEFAULT '',
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE user_filters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    filter_json TEXT NOT NULL,
    enabled     BOOLEAN   DEFAULT 1,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, name),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE TABLE usage_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    provider   TEXT DEFAULT '',
    model      TEXT DEFAULT '',
    key_source TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_usage_user_created ON usage_events(user_id, created_at);
"""

ROWS = {"papers": 2, "summaries": 1, "bookmarks": 1, "users": 2,
        "user_filters": 3, "usage_events": 2}


def _old_db(path, login_names=None):
    """A populated 2026-09-15 database. login_names, if given, adds the
    accounts column (2026-09-18) with those values but not its unique index,
    which is the state a database with case-duplicate names would be in."""
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_2026_09_15)
    conn.executemany("INSERT INTO papers (id, doi, title, abstract) VALUES (?, ?, ?, ?)",
                     [(1, "10.1/a", "Paper A", "abs A"), (2, "10.1/b", "Paper B", "abs B")])
    conn.execute("INSERT INTO summaries (paper_id, summary_text, key_findings) "
                 "VALUES (1, 'old summary', '[\"f\"]')")
    conn.execute("INSERT INTO bookmarks (paper_id) VALUES (2)")
    conn.executemany("INSERT INTO users (user_id, display_name, created_at) VALUES (?, ?, ?)",
                     [("u1", "Dave", "2026-09-10 10:00:00"),
                      ("u2", "dave", "2026-09-12 10:00:00")])
    conn.executemany("INSERT INTO user_filters (user_id, name, filter_json) VALUES (?, ?, '{}')",
                     [("u1", "Stress"), ("u1", "stress"), ("u2", "Sleep")])
    conn.executemany("INSERT INTO usage_events (user_id, kind, key_source) VALUES (?, 'summary', 'owner')",
                     [("u1",), ("u2",)])
    if login_names:
        conn.execute("ALTER TABLE users ADD COLUMN login_name TEXT")
        for user_id, name in login_names.items():
            conn.execute("UPDATE users SET login_name = ? WHERE user_id = ?", (name, user_id))
    conn.commit()
    conn.close()


def _columns(db, table):
    return {r[1] for r in db.conn.execute(f"PRAGMA table_info({table})")}


# ── T2: upgrading a 2026-09-15 database ──────────────────────────────────────

def test_t2_upgrade_from_2026_09_15_schema(tmp_path):
    path = str(tmp_path / "old.db")
    _old_db(path)

    db = Database(path)
    for table, expected in {
        "papers": {"summary_attempted_at"},
        "summaries": {"source_text", "text_source"},
        "users": {"preferred_model", "login_name", "pin_hash", "recovery_hash",
                  "failed_logins", "locked_until", "merged_into", "session_nonce",
                  "setup_code_hash"},
        "usage_events": {"prompt_tokens", "completion_tokens", "tokens_counted"},
    }.items():
        assert expected <= _columns(db, table), table
    for table in ("user_reference_lists", "user_reference_list_items",
                  "user_reviews", "access_code_bindings"):
        assert _columns(db, table), table
    for table, n in ROWS.items():
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == n, table
    # N1 ran: a paper without a DOI can be stored now.
    assert db.insert_paper({"title": "No DOI", "canonical_id": "title:x"}) is not None
    assert db.get_summary(1)["summary_text"] == "old summary"
    db.close()

    again = Database(path)                       # a second open changes nothing
    for table, n in ROWS.items():
        extra = 1 if table == "papers" else 0
        assert again.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == n + extra
    again.close()


def test_t2_case_duplicate_names_handled(tmp_path, caplog):
    """Two login names that differ only in case would stop the unique index
    being built, and Database() would fail on every open. The newer one is
    renamed and logged; saved filters that differ only in case are reported."""
    path = str(tmp_path / "dupes.db")
    _old_db(path, login_names={"u1": "Dave", "u2": "dave"})

    with caplog.at_level(logging.WARNING, logger="src.db"):
        db = Database(path)
    names = dict(db.conn.execute("SELECT user_id, login_name FROM users"))
    assert names == {"u1": "Dave", "u2": "dave (2)"}          # the oldest keeps its name
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("'dave'" in m and "'dave (2)'" in m for m in errors)
    assert any("'stress'" in r.getMessage() and "u1" in r.getMessage()
               for r in caplog.records if r.levelno == logging.WARNING)
    assert db.conn.execute("SELECT COUNT(*) FROM user_filters WHERE user_id = 'u1'"
                           ).fetchone()[0] == 2                 # both filters kept
    db.close()


# ── M30: foreign keys on; orphans from before are removed ────────────────────

def test_m30_orphans_cleaned_and_cascade_on(tmp_path, caplog):
    path = str(tmp_path / "m30.db")
    db = Database(path)
    user = user_store.create_user(db, "Ann")
    paper = db.insert_paper({"doi": "10.1/p", "title": "P", "canonical_id": "doi:10.1/p"})
    db.close()

    # Rows left behind by deletes made while foreign keys were off.
    raw = sqlite3.connect(path)
    raw.execute("INSERT INTO user_reviews (user_id, list_id, review_text) VALUES (?, 999, 'r')",
                (user,))
    raw.execute("INSERT INTO user_reference_list_items (list_id, paper_id) VALUES (999, ?)",
                (paper,))
    raw.commit()
    raw.close()

    with caplog.at_level(logging.WARNING, logger="src.db"):
        db = Database(path)
    for table in ("user_reviews", "user_reference_list_items"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert any(f"removed 1 {table}" in r.getMessage() for r in caplog.records)
    assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    # Deleting a list now takes its items and reviews with it.
    list_id = user_store.create_reference_list(db, user, "Reading")
    user_store.add_reference_item(db, list_id, paper)
    user_store.save_review(db, user, list_id, "text", "", [], [], "m")
    user_store.delete_reference_list(db, user, list_id)
    for table in ("user_reviews", "user_reference_list_items"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    db.close()


def test_t2_non_ascii_names_fold_as_each_comparison_does(tmp_path, caplog):
    """Batch-8 gate finding 2. Login names fold as SQLite's lower() does
    (ASCII only), because the unique index and sign-in compare that way:
    "ÉRIN" and "érin" are two accounts and neither is renamed. Filter names
    fold with casefold, as filter_name_taken does, so "CAFÉ"/"café" is reported."""
    path = str(tmp_path / "accents.db")
    _old_db(path, login_names={"u1": "ÉRIN", "u2": "érin"})
    raw = sqlite3.connect(path)
    raw.executemany("INSERT INTO user_filters (user_id, name, filter_json) VALUES ('u2', ?, '{}')",
                    [("CAFÉ",), ("café",)])
    raw.commit()
    raw.close()

    with caplog.at_level(logging.WARNING, logger="src.db"):
        db = Database(path)
    assert dict(db.conn.execute("SELECT user_id, login_name FROM users")) == \
        {"u1": "ÉRIN", "u2": "érin"}
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("'café'" in r.getMessage() and "u2" in r.getMessage() for r in caplog.records)
    db.close()


def test_br8_stored_markup_cleaned_once(tmp_path, caplog):
    """Rows stored before the markup fix are cleaned on open; other text,
    and full-text (model) summaries, are not touched."""
    path = str(tmp_path / "markup.db")
    db = Database(path)
    tagged = db.insert_paper({"doi": "10.1/t", "canonical_id": "doi:10.1/t",
                              "title": "T<sub>reg</sub> cells",
                              "abstract": "<h4>Objective</h4>Find out.<h4>Methods</h4>Ask."})
    escaped = db.insert_paper({"doi": "10.1/e", "canonical_id": "doi:10.1/e",
                               "title": "By &lt;i&gt;P. gingivalis&lt;/i&gt;", "abstract": ""})
    plain = db.insert_paper({"doi": "10.1/p", "canonical_id": "doi:10.1/p",
                             "title": "AT&amp;T  and p < 0.05", "abstract": "x > 1"})
    db.insert_summary(tagged, summary_text="<h4>Objective</h4>Find out.", source_text="abstract")
    db.insert_summary(plain, summary_text="", key_findings=["<b>kept</b> as the model wrote it"],
                      source_text="full_text")
    db.close()

    with caplog.at_level(logging.INFO, logger="src.db"):
        db = Database(path)
    t, p = db.get_paper_by_id(tagged), db.get_paper_by_id(plain)
    assert (t["title"], t["abstract"]) == ("Treg cells", "Objective: Find out.\nMethods: Ask.")
    assert (p["title"], p["abstract"]) == ("AT&amp;T  and p < 0.05", "x > 1")   # untouched
    assert db.get_summary(tagged)["summary_text"] == "Objective: Find out."
    assert db.get_summary(plain)["key_findings"] == ["<b>kept</b> as the model wrote it"]
    assert db.get_paper_by_id(escaped)["title"] == "By P. gingivalis"
    assert "2 papers and 1 abstract-only summaries" in caplog.text
    db.close()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="src.db"):
        Database(path).close()                     # second open: nothing to do
    assert "Cleaned source markup" not in caplog.text


def test_br14_stored_pubmed_label_fixed(tmp_path, caplog):
    """Production check 2026-09-29: 6 summaries sat on papers stored as
    " (PubMed)" (a PubMed paper without a journal, before the label fix)."""
    path = str(tmp_path / "labels.db")
    db = Database(path)
    bare = db.insert_paper({"doi": "10.1/a", "canonical_id": "doi:10.1/a", "title": "A",
                            "journal_or_server": " (PubMed)"})
    padded = db.insert_paper({"doi": "10.1/b", "canonical_id": "doi:10.1/b", "title": "B",
                              "journal_or_server": " Lancet (PubMed) "})
    good = db.insert_paper({"doi": "10.1/c", "canonical_id": "doi:10.1/c", "title": "C",
                            "journal_or_server": "Lancet (PubMed)"})
    other = db.insert_paper({"doi": "10.1/d", "canonical_id": "doi:10.1/d", "title": "D",
                             "journal_or_server": "bioRxiv"})
    db.close()
    with caplog.at_level(logging.INFO, logger="src.db"):
        db = Database(path)
    got = [db.get_paper_by_id(i)["journal_or_server"] for i in (bare, padded, good, other)]
    assert got == ["PubMed", "Lancet (PubMed)", "Lancet (PubMed)", "bioRxiv"]
    assert "Fixed the source label of 2 stored papers" in caplog.text
    db.close()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="src.db"):
        Database(path).close()
    assert "Fixed the source label" not in caplog.text
