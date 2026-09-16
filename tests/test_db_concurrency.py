"""
Tests for src/db.py — threading, path resolution, and the web-app schema.

Spec: docs/implementation_plan_2026-09-15.md#1.6, #2.5, #2.6
"""
import gc
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
    assert db.conn.execute("PRAGMA busy_timeout").fetchone()[0] == db_module.busy_timeout_ms()


def test_busy_timeout_is_read_at_call_time_not_at_import(monkeypatch):
    """A frozen module constant silently ignores env set after import."""
    monkeypatch.setenv("BIORX_DB_BUSY_TIMEOUT_MS", "4321")
    assert db_module.busy_timeout_ms() == 4321
    monkeypatch.setenv("BIORX_DB_BUSY_TIMEOUT_MS", "not-a-number")
    assert db_module.busy_timeout_ms() == db_module.DEFAULT_BUSY_TIMEOUT_MS


# ── Releasing connections, not merely handing them out (P30) ──────────────────

def _is_closed(conn) -> bool:
    try:
        conn.execute("SELECT 1")
        return False
    except sqlite3.ProgrammingError:
        return True


def test_connections_of_finished_threads_are_released(db):
    """
    A per-thread connection that is never given back is a leaked file
    descriptor. The GUI starts a thread per search/download/summarize.

    This asserts the connections were CLOSED — the quantity that actually costs
    something (learnings P30's corollary) — not that a registry dict stayed
    small, which stays small anyway because CPython reuses thread idents.
    """
    handed_out = []
    for _ in range(12):
        t = threading.Thread(target=lambda: handed_out.append(db.conn))
        t.start()
        t.join()

    assert len(handed_out) == 12
    gc.collect()
    still_open = [c for c in handed_out if not _is_closed(c)]
    assert still_open == [], f"{len(still_open)} connections left open"


def test_connection_is_released_when_a_QTHREAD_finishes(tmp_path):
    """
    The regression test for the real production thread type.

    The GUI runs every worker on a QThread. threading.current_thread() there
    returns a _DummyThread whose is_alive() stays True forever, so any release
    keyed on thread-object liveness collects nothing in the application that
    churns threads hardest — while a threading.Thread test passes happily.
    Release is therefore keyed on the lifetime of the thread-local holder, and
    this asserts it with an actual QThread.
    """
    pytest.importorskip("PyQt6.QtCore")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QCoreApplication, QObject, QThread

    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    database = Database(str(tmp_path / "qt.db"))
    grabbed = {}

    class Worker(QObject):
        def run(self):
            grabbed["conn"] = database.conn
            grabbed["thread_type"] = type(threading.current_thread()).__name__

    qthread = QThread()
    worker = Worker()
    worker.moveToThread(qthread)
    qthread.started.connect(worker.run)
    qthread.start()
    qthread.wait(5000)
    qthread.quit()
    qthread.wait(5000)
    gc.collect()

    assert grabbed["thread_type"] == "_DummyThread", (
        "PyQt no longer yields a _DummyThread; the hazard this guards may have "
        "changed shape — re-check before relaxing this test"
    )
    assert _is_closed(grabbed["conn"]), "QThread's connection was never closed"
    database.close()


def test_release_closes_this_threads_connection(db):
    conn = db.conn
    assert conn in db._conns.values()

    db.release()
    gc.collect()

    assert conn not in db._conns.values(), "released connection still registered"
    assert _is_closed(conn), "release() did not close the connection"
    assert db.conn is not conn       # a later call transparently reconnects


def test_release_is_safe_when_this_thread_never_connected(db):
    done = []

    def never_connected():
        db.release()
        done.append(True)

    t = threading.Thread(target=never_connected); t.start(); t.join()
    assert done == [True]


def test_a_live_threads_connection_is_not_released(db):
    """A running thread must keep its connection while it is still using it."""
    started, may_finish, seen = threading.Event(), threading.Event(), {}

    def worker():
        seen["conn"] = db.conn
        started.set()
        may_finish.wait(timeout=5)

    t = threading.Thread(target=worker); t.start()
    started.wait(timeout=5)
    for _ in range(3):               # churn other threads
        x = threading.Thread(target=lambda: db.conn.execute("SELECT 1"))
        x.start(); x.join()
    _ = db.conn

    assert seen["conn"].execute("SELECT 1").fetchone()[0] == 1
    may_finish.set(); t.join()


# ── Summary provenance is written, not merely migrated in (P21) ───────────────

def test_insert_summary_records_the_user_and_model(db):
    paper_id = db.insert_paper({"doi": "10.1/s", "title": "T", "authors": "A",
                                "abstract": "x", "date": "2026-01-01"})
    db.insert_summary(paper_id, "text", ["f1"], "method", "concl",
                      model_version="claude-sonnet-5", created_by_user_id="u-123")
    row = db.conn.execute(
        "SELECT model_version, created_by_user_id FROM summaries WHERE paper_id = ?",
        (paper_id,)).fetchone()
    assert row["model_version"] == "claude-sonnet-5"
    assert row["created_by_user_id"] == "u-123"


def test_insert_summary_keeps_working_without_provenance(db):
    """The desktop agent calls this with no user; that must still work."""
    paper_id = db.insert_paper({"doi": "10.1/s2", "title": "T", "authors": "A",
                                "abstract": "x", "date": "2026-01-01"})
    assert db.insert_summary(paper_id, "text") is not None


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


# ── The GUI actually calls release (P21/P25: a method with no caller is dead) ──

def test_search_worker_releases_its_connection_when_it_finishes():
    """
    SearchWorker runs on its own QThread and takes a connection on it. Asserting
    the call arrives at the boundary, not that the code contains a line.
    """
    pytest.importorskip("PyQt6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import gui
    from unittest.mock import MagicMock

    orchestrator = MagicMock()
    orchestrator.search.return_value = []
    database = MagicMock()

    worker = gui.SearchWorker(
        orchestrator, {"text_groups": [], "authors": []},
        save_to_db=False, db=database,
    )
    worker.run()

    database.release.assert_called_once()


def test_search_worker_releases_its_connection_even_when_the_search_raises():
    pytest.importorskip("PyQt6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import gui
    from unittest.mock import MagicMock

    orchestrator = MagicMock()
    orchestrator.search.side_effect = RuntimeError("source exploded")
    database = MagicMock()

    worker = gui.SearchWorker(
        orchestrator, {"text_groups": [], "authors": []},
        save_to_db=False, db=database,
    )
    worker.run()        # error is emitted on a signal, not raised

    database.release.assert_called_once()
