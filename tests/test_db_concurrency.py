"""
Tests for src/db.py — threading, path resolution, and the web-app schema.

Spec: docs/implementation_plan_2026-09-15.md#1.6, #2.5, #2.6
"""
import os
import sqlite3
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src import db as db_module
from src.db import Database, default_db_path


@pytest.fixture
def db(tmp_path):
    """Always an explicit temporary path — never a production default (P28)."""
    d = Database(str(tmp_path / "test.db"))
    yield d
    d.close()


# ── Path resolution (P34: every write location overridable by env) ────────────

def test_default_db_path_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("BIORX_DB_PATH", "/tmp/explicit.db")
    monkeypatch.setenv("DATA_DIR", "/tmp/data")
    assert default_db_path() == "/tmp/explicit.db"


def test_default_db_path_falls_back_to_data_dir(monkeypatch):
    monkeypatch.delenv("BIORX_DB_PATH", raising=False)
    monkeypatch.setenv("DATA_DIR", "/tmp/data")
    assert default_db_path() == "/tmp/data/biorxiv.db"


def test_default_db_path_falls_back_to_the_desktop_location(monkeypatch):
    monkeypatch.delenv("BIORX_DB_PATH", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert default_db_path() == db_module.DEFAULT_DB_PATH


# ── Concurrency ───────────────────────────────────────────────────────────────

def test_each_thread_gets_its_own_connection(db):
    seen = {}

    def grab(name):
        seen[name] = id(db.conn)

    t1 = threading.Thread(target=grab, args=("a",))
    t2 = threading.Thread(target=grab, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert len(set(seen.values())) == 2, "threads shared one connection"
    assert id(db.conn) not in seen.values(), "main thread reused a worker's connection"


def test_parallel_writes_from_multiple_threads_all_land(db):
    """
    The old single shared connection was justified by "writes are always serial".
    The web app breaks that premise, so writes from many threads must all commit.
    """
    threads, errors = [], []
    per_thread = 20

    def writer(n):
        try:
            for i in range(per_thread):
                db.insert_paper({
                    "doi": f"10.1234/t{n}-{i}", "title": f"Paper {n}-{i}",
                    "authors": "A B", "abstract": "x", "date": "2026-01-01",
                })
        except Exception as e:      # surfaced, not swallowed
            errors.append(e)

    for n in range(4):
        t = threading.Thread(target=writer, args=(n,))
        threads.append(t); t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent writes raised: {errors}"
    count = db.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    assert count == 4 * per_thread


def test_wal_and_busy_timeout_are_set(db):
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert db.conn.execute("PRAGMA busy_timeout").fetchone()[0] == db_module.BUSY_TIMEOUT_MS


def test_close_closes_every_handed_out_connection(db):
    opened = []

    def grab():
        opened.append(db.conn)

    t = threading.Thread(target=grab); t.start(); t.join()
    _ = db.conn
    db.close()

    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


# ── Schema ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("table", ["users", "user_filters", "usage_events"])
def test_web_app_tables_exist(db, table):
    names = {r[0] for r in db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert table in names


def test_summaries_records_who_created_it(db):
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(summaries)")}
    assert "created_by_user_id" in cols
    assert "model_version" in cols


def test_migration_is_additive_on_a_populated_db(tmp_path):
    """
    Dirty-state (P8): a database that already holds rows must migrate without
    losing them, and re-opening must be a no-op rather than an error.
    """
    path = str(tmp_path / "existing.db")
    first = Database(path)
    first.insert_paper({"doi": "10.1/keep", "title": "Keep me", "authors": "A",
                        "abstract": "x", "date": "2026-01-01"})
    first.close()

    second = Database(path)
    rows = second.conn.execute("SELECT doi, title FROM papers").fetchall()
    assert [tuple(r) for r in rows] == [("10.1/keep", "Keep me")]
    second.close()

    third = Database(path)        # third open: still idempotent
    assert third.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
    third.close()


def test_add_column_if_missing_raises_on_a_real_failure(db):
    """
    A migration failure must not be swallowed as "column already exists" (P2).
    """
    cursor = db.conn.cursor()
    with pytest.raises(sqlite3.Error):
        db._add_column_if_missing(cursor, "no_such_table", "c", "TEXT")
